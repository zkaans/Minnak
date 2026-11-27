"""Katman 4 & 5: Beyin (Olay Bazlı Ollama) + Aksiyon Kayıt Tablosu + Açık Web Arama + Gece Yansıması."""
import html
import json
import os
import random
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from ctypes import windll
from ctypes.wintypes import BOOL, DWORD, HANDLE
from memory import Memory

try:
    import psutil
except ImportError:
    psutil = None

# --- Yapılandırma ve Aksiyon Kayıt Tablosu (Single Source of Truth) ---
DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_HOST = "http://localhost:11434"
HISTORY_TIMEOUT_SEC = 600.0
HISTORY_MAX_MESSAGES = 8  # 4 tur (user + assistant)

ACTION_SPECS: list[dict] = [
    {"name": "yok"},
    {"name": "imleci_takip_et", "reflex": r"\b(peşimden gel|takip et|beni takip et)\b", "menu": "🐾 Takip Et", "ctx_menu": True},
    {"name": "imlece_gel", "reflex": r"\b(gel|buraya gel|yanıma gel)\b", "menu": "🧲 İmlece Gel", "ctx_menu": True},
    {"name": "dur", "reflex": r"\b(dur|bekle|takibi bırak)\b", "menu": "⏹ Dur", "ctx_menu": True},
    {"name": "pencereye_cik", "reflex": r"\b(pencereye çık|yukarı çık)\b", "menu": "🪟 Pencereye Çık", "ctx_menu": True},
    {"name": "gorev_cubuguna_in", "reflex": r"\b(aşağı in|görev çubuğuna in|yere in)\b", "menu": "⬇️ Aşağı İn", "ctx_menu": True},
    {"name": "uyu", "reflex": r"\b(uyu|yat)\b"},
    {"name": "uyan", "reflex": r"\b(uyan|kalk)\b"},
    {"name": "zipla", "reflex": r"\b(zıpla|hopla)\b", "menu": "🦘 Zıpla", "ctx_menu": True},
    {"name": "pati_at", "reflex": r"\b(pati at|pati ver|oyna)\b", "menu": "🐾 Pati At", "ctx_menu": False},
    {"name": "gunluk_yaz"},
    {"name": "dans_et", "reflex": r"\b(dans et|ritim tut)\b", "menu": "🎵 Dans Et", "ctx_menu": True},
]

ACTIONS: list[str] = [s["name"] for s in ACTION_SPECS]
ACTIVE_ACTIONS_PROMPT: str = ", ".join(a for a in ACTIONS if a != "yok")
REFLEX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(s["reflex"]), s["name"]) for s in ACTION_SPECS if s.get("reflex")
]
MENU_ACTIONS: list[tuple[str, str, bool]] = [
    (s["menu"], s["name"], bool(s.get("ctx_menu"))) for s in ACTION_SPECS if s.get("menu")
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "duygu": {"type": "string", "enum": ["normal", "merakli", "sikilmis", "uykulu", "heyecanli"]},
        "mesaj": {"type": "string"},
        "aksiyon": {"type": "string", "enum": ACTIONS},
        "hatirla_kural": {"type": "string"},
    },
    "required": ["duygu", "mesaj", "aksiyon"],
}

PROCESS_SET_INFORMATION = 0x0200
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020

windll.kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
windll.kernel32.OpenProcess.restype = HANDLE
windll.kernel32.SetPriorityClass.argtypes = [HANDLE, DWORD]
windll.kernel32.SetPriorityClass.restype = BOOL
windll.kernel32.CloseHandle.argtypes = [HANDLE]
windll.kernel32.CloseHandle.restype = BOOL

_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
_ANTIBOT_TOKENS = ("anomaly-modal", "cc=botnet", "captcha", "verify you are human")
_DISCOVERY_SUFFIXES = ("ilginç bilgiler", "yeni gelişmeler", "nasıl çalışır", "keşifler")


def _clean_html_text(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def _resolve_ddg_url(href: str) -> str:
    clean = html.unescape((href or "").strip())
    if clean.startswith("//"):
        clean = "https:" + clean
    if "duckduckgo.com/y.js" in clean or "ad_provider=" in clean:
        return ""
    if "duckduckgo.com/l/?" in clean:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(clean).query)
        clean = qs.get("uddg", [""])[0]
    return clean if clean.startswith(("http://", "https://")) else ""


def _fetch_page_excerpt(url: str, timeout: float = 5.0) -> str:
    try:
        req = urllib.request.Request(url, headers=_WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and "html" not in ctype and "text" not in ctype:
                return ""
            raw = resp.read(200_000).decode("utf-8", errors="ignore")
        if any(tok in raw.lower() for tok in _ANTIBOT_TOKENS):
            return ""
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
        paras = [_clean_html_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", stripped, re.S | re.I)]
        useful = [p for p in paras if len(p) >= 40]
        return " ".join(useful)[:400].strip()
    except Exception:
        return ""


def build_discovery_query(memory: Memory) -> tuple[str, str]:
    topic, _src, _w = memory.pick_hobby()
    suffix = random.choice(_DISCOVERY_SUFFIXES)
    return topic, f"{topic} {suffix}"


def search_open_web(query: str, memory: Memory | None = None, timeout: float = 5.0) -> dict[str, str] | None:
    """DuckDuckGo HTML üzerinden açık web araması yapar; bot koruması varsa vazgeçip None döner."""
    q = (query or "").strip()
    if not q:
        return None
    ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(q)}"
    try:
        req = urllib.request.Request(ddg_url, headers=_WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(300_000).decode("utf-8", errors="ignore")
    except Exception:
        return None

    if any(tok in raw.lower() for tok in _ANTIBOT_TOKENS):
        return None

    candidates: list[tuple[str, str, str]] = []
    blocks = re.split(r'class="[^"]*result__body[^"]*"', raw)
    for blk in blocks[1:]:
        m_a = re.search(r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S | re.I)
        if not m_a:
            continue
        url = _resolve_ddg_url(m_a.group(1))
        title = _clean_html_text(m_a.group(2))
        if not url or not title:
            continue
        m_s = re.search(r'<(?:a|div)[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>', blk, re.S | re.I)
        snippet = _clean_html_text(m_s.group(1)) if m_s else ""
        candidates.append((title, url, snippet))

    if not candidates:
        for href, raw_t in re.findall(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S | re.I
        ):
            url = _resolve_ddg_url(href)
            title = _clean_html_text(raw_t)
            if url and title:
                candidates.append((title, url, ""))

    checked = 0
    for title, url, snippet in candidates:
        if memory and memory.is_source_seen(url, title):
            continue
        checked += 1
        excerpt = _fetch_page_excerpt(url, timeout=timeout) or snippet
        if excerpt:
            if memory:
                memory.mark_source_seen(url, title)
            return {"title": title, "url": url, "snippet": excerpt[:400]}
        if checked >= 3:
            break
    return None


class Brain:
    def __init__(self, memory: Memory, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST):
        self.memory = memory
        self.model = model
        self.host = host
        self.vram_evacuated = False
        restored = self.memory.load_recent_turns(max_age_sec=HISTORY_TIMEOUT_SEC, limit=HISTORY_MAX_MESSAGES // 2)
        self.history: list[dict] = restored
        self.last_chat_ts: float = time.time() if restored else 0.0
        self.ollama_online: bool = False

    def clear_history(self) -> None:
        self.history.clear()

    def check_ollama(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=1.5) as resp:
                self.ollama_online = resp.status == 200
                return self.ollama_online
        except Exception:
            self.ollama_online = False
            return False

    def ensure_ollama(self, start_if_down: bool = True) -> bool:
        if self.check_ollama():
            return True
        if not start_if_down:
            return False
        local_exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        exe = local_exe if os.path.exists(local_exe) else shutil.which("ollama")
        if not exe:
            return False
        try:
            subprocess.Popen(
                [exe, "serve"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(5):
                time.sleep(0.5)
                if self.check_ollama():
                    return True
        except Exception:
            pass
        return False

    def warmup(self) -> bool:
        try:
            self._post("/api/generate", {"model": self.model, "prompt": "", "keep_alive": "10m"}, timeout=30.0)
            self.ollama_online = True
            return True
        except Exception:
            return False

    def build_options(self, is_gaming: bool) -> tuple[dict, str | int]:
        if is_gaming:
            # Oyun modu: VRAM=0, kısıtlı CPU thread, 5dk RAM keep_alive
            return {"num_gpu": 0, "num_thread": 3, "num_ctx": 1024, "temperature": 0.6}, "5m"
        return {"num_gpu": 99, "num_ctx": 2048, "temperature": 0.7}, "10m"

    def set_ollama_priority(self, low: bool) -> bool:
        """Oyun modunda ollama sürecini BELOW_NORMAL_PRIORITY_CLASS yapar."""
        if not psutil:
            return False  # shortcut: psutil yoksa öncelik değişimi atlanır
        target_cls = BELOW_NORMAL_PRIORITY_CLASS if low else NORMAL_PRIORITY_CLASS
        changed = False
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in ("ollama.exe", "ollama_llama_server.exe"):
                    handle = windll.kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, proc.info["pid"])
                    if handle:
                        if windll.kernel32.SetPriorityClass(handle, target_cls):
                            changed = True
                        windll.kernel32.CloseHandle(handle)
            except Exception:
                continue
        return changed

    def evacuate_vram(self) -> bool:
        """Oyun algılandığında modeli VRAM'den anında tahliye eder."""
        if self.vram_evacuated:
            return True
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": 0}, timeout=2.0)
            self.vram_evacuated = True
            return True
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        is_gaming: bool = False,
        image_b64: str | None = None,
        remember_turn: bool = False,
        cancel_event=None,
    ) -> dict[str, str]:
        if is_gaming:
            self.set_ollama_priority(True)
        else:
            self.vram_evacuated = False

        now = time.time()
        if now - self.last_chat_ts > HISTORY_TIMEOUT_SEC:
            self.history.clear()

        options, keep_alive = self.build_options(is_gaming)
        traits = self.memory.get_personality()
        rules = self.memory.read_rules()
        diary_txt = self.memory.recent_diary(days=3)
        memories_txt = self.memory.search_relevant_memories(prompt, limit=3) if remember_turn else ""
        sys_prompt = (
            f"Sen masaüstü kedisi Minnak'sın. Samimiyet:{traits.get('samimiyet', 50):.0f}/100. "
            f"Kısa, tek cümlelik doğal Türkçe yanıt ver. "
            f"Kullanıcı hareket/davranış isterse uygun 'aksiyon' seç "
            f"({ACTIVE_ACTIONS_PROMPT}), "
            f"istemezse 'yok' seç. Kullanıcı kendisine ait kalıcı bir tercih/bilgi söylerse 'hatirla_kural' "
            f"alanına kısa kural yaz, yoksa boş bırak.\nKurallar:\n{rules}"
        )
        if diary_txt:
            sys_prompt += f"\nSon Günlük:\n{diary_txt}"
        if memories_txt:
            sys_prompt += f"\nİlgili Anılar:\n{memories_txt}"
        user_msg: dict = {"role": "user", "content": prompt}
        if image_b64 and not is_gaming:
            user_msg["images"] = [image_b64]

        messages = [{"role": "system", "content": sys_prompt}]
        if remember_turn and self.history:
            messages.extend(self.history[-HISTORY_MAX_MESSAGES:])
        messages.append(user_msg)

        payload = {
            "model": self.model,
            "messages": messages,
            "format": RESPONSE_SCHEMA,
            "options": options,
            "keep_alive": keep_alive,
            "stream": False,
            "think": False,
        }
        try:
            kwargs = {"cancel_event": cancel_event} if cancel_event is not None else {}
            req_timeout = 30.0 if (is_gaming or image_b64) else 15.0
            res = self._post("/api/chat", payload, timeout=req_timeout, **kwargs)
            self.ollama_online = True
            parsed = json.loads(res["message"]["content"])
            if parsed.get("aksiyon") not in ACTIONS:
                parsed["aksiyon"] = "yok"
            if remember_turn:
                cand = str(parsed.get("hatirla_kural") or "").strip()
                if cand:
                    self.memory.add_rule_candidate(cand)
                self.last_chat_ts = now
                self.history.extend([
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": parsed.get("mesaj", "")},
                ])
                self.history = self.history[-HISTORY_MAX_MESSAGES:]
            return parsed
        except RuntimeError as exc:
            if str(exc) == "cancelled":
                return {"duygu": "normal", "mesaj": "", "aksiyon": "yok", "cancelled": "1"}
            self.ollama_online = False
            return {"duygu": "normal", "mesaj": "Ollama kapalı veya yanıt vermiyor.", "aksiyon": "yok"}
        except Exception:
            self.ollama_online = False
            # shortcut: Ollama kapalıysa net çevrimdışı durum döner
            return {"duygu": "normal", "mesaj": "Ollama kapalı veya yanıt vermiyor.", "aksiyon": "yok"}

    def write_diary(self, date_str: str | None = None, is_gaming: bool = False) -> str:
        """O günün gerçek olaylarını doğal günlük formatında özetleyip gunluk.md'ye yazar."""
        if is_gaming:
            return ""
        target_date = date_str or time.strftime("%Y-%m-%d")
        natural_fallback, llm_context = self.memory.build_factual_day_digest(target_date)
        if not llm_context and date_str is not None:
            return ""
        if not llm_context:
            summary = "Bugün Kaan ile henüz pek konuşmadık, sakin geçti."
            self.memory.save_diary_entry(target_date, summary)
            return summary

        prompt = (
            f"Aşağıda bugün ({target_date}) Kaan ile yaşadığın gerçek olaylar var:\n{llm_context}\n\n"
            "KURAL: SADECE bu kayıtlarda geçen gerçek konuşmaları, baktığın ekranları veya araştırdığın "
            "konuları 1-2 cümleyle samimi, doğal bir günlük cümlesi olarak anlat. "
            "Sayı veya istatistik ('5 araştırma', '7 sohbet', 'CODING ortamı' gibi) ve kayıtta olmayan "
            "hayali olay UYDURMA. Aksiyon 'yok' seç."
        )
        res = self.generate(prompt, is_gaming=False, remember_turn=False)
        msg = (res.get("mesaj") or "").strip()
        summary = natural_fallback if (not self.ollama_online or not msg or "Ollama kapalı" in msg) else msg
        self.memory.save_diary_entry(target_date, summary)
        return summary

    def run_night_reflection(self) -> list[str]:
        """İşlenmemiş loglardan 1-3 kısa davranış kuralı çıkarıp ogrendiklerim.md'ye ekler."""
        logs = self.memory.get_unprocessed_logs(25)
        if not logs:
            promoted = self.memory.promote_pending_candidates(max_promote=2)
            today = time.strftime("%Y-%m-%d")
            if self.memory.get_logs_for_date(today) and not self.memory.has_diary_for_date(today):
                self.write_diary(today)
            return promoted

        scores: dict[tuple[str, str], int] = {}
        for row in logs:
            fb = row["feedback"]
            if fb != 0:
                key = (row["context"] or "GENERAL", row["action"] or "sohbet")
                scores[key] = scores.get(key, 0) + fb

        new_rules: list[str] = []
        for (ctx, act), net in sorted(scores.items(), key=lambda x: abs(x[1]), reverse=True)[:3]:
            if net < 0:
                new_rules.append(f"{ctx} sırasında {act} azalt (negatif tepki)")
            elif net > 0:
                new_rules.append(f"{ctx} sırasında {act} yararlı")

        if not new_rules:
            new_rules.append("Etkileşim dengeli, mevcut ritmi koru")

        for rule in new_rules[:3]:
            self.memory.append_rule(rule)
        promoted = self.memory.promote_pending_candidates(max_promote=2)
        self.memory.mark_logs_processed([r["id"] for r in logs])
        self.memory.compress_rules_if_needed()
        today = time.strftime("%Y-%m-%d")
        if self.memory.get_logs_for_date(today) and not self.memory.has_diary_for_date(today):
            self.write_diary(today)
        return (new_rules[:3] + promoted)[:5]

    def _post(self, path: str, payload: dict, timeout: float, cancel_event=None) -> dict:
        if cancel_event is not None:
            payload = {**payload, "stream": True}
        req = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if cancel_event is None:
                return json.loads(resp.read().decode("utf-8"))
            parts: list[str] = []
            for raw_line in resp:
                if cancel_event.is_set():
                    resp.close()
                    raise RuntimeError("cancelled")
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "") or chunk.get("response", "")
                if content:
                    parts.append(content)
                if chunk.get("done"):
                    break
            return {"message": {"content": "".join(parts)}}
