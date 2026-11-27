"""Katman 6: Hafıza ve Adaptasyon (SQLite + Kişilik Matrisi + Koşullanma + Hobiler + ogrendiklerim.md)."""
import os
import random
import re
import sqlite3
import threading
from pathlib import Path

USER_NAME = os.environ.get("MINNAK_USER", "").strip() or "kullanıcı"

DB_PATH = Path(__file__).parent / "minnak.db"
RULES_PATH = Path(__file__).parent / "ogrendiklerim.md"
DIARY_PATH = Path(__file__).parent / "gunluk.md"

_STOP_WORDS = {
    "bana", "bunu", "şunu", "nasıl", "neden", "nedir", "için", "gibi",
    "daha", "olan", "biraz", "hakkında", "söyle", "anlat", "veya", "çünkü",
}

clamp = lambda v, lo, hi: max(lo, min(hi, v))

DEFAULT_HOBBIES = [
    ("yapay zeka", "hn", 1.2),
    ("bilim", "wiki", 1.0),
    ("oyun geliştirme", "reddit", 1.1),
    ("uzay", "wiki", 1.0),
    ("programlama", "hn", 1.1),
]


class Memory:
    def __init__(self, db_path: Path = DB_PATH, rules_path: Path = RULES_PATH, diary_path: Path = DIARY_PATH):
        self.db_path = db_path
        self.rules_path = rules_path
        self.diary_path = diary_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            with self.conn:
                self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS personality (
                    trait TEXT PRIMARY KEY,
                    val REAL NOT NULL,
                    base REAL NOT NULL,
                    min_v REAL NOT NULL,
                    max_v REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_weights (
                    context TEXT NOT NULL,
                    action TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (context, action)
                );
                CREATE TABLE IF NOT EXISTS hobbies (
                    topic TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                    context TEXT,
                    action TEXT,
                    user_msg TEXT DEFAULT '',
                    response TEXT,
                    feedback INTEGER DEFAULT 0,
                    processed INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS seen_sources (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    ts DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS rule_candidates (
                    norm_key TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    ts DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
                for alter_sql in (
                    "ALTER TABLE logs ADD COLUMN processed INTEGER DEFAULT 0",
                    "ALTER TABLE logs ADD COLUMN user_msg TEXT DEFAULT ''",
                ):
                    try:
                        self.conn.execute(alter_sql)
                    except sqlite3.OperationalError:
                        pass

                for trait, val, base, lo, hi in [
                    ("samimiyet", 50.0, 50.0, 10.0, 95.0),
                    ("mizah", 50.0, 50.0, 10.0, 90.0),
                    ("merak_esigi", 50.0, 50.0, 20.0, 90.0),
                ]:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO personality VALUES (?, ?, ?, ?, ?)",
                        (trait, val, base, lo, hi),
                    )

                if self.conn.execute("SELECT COUNT(*) FROM hobbies").fetchone()[0] == 0:
                    self.conn.executemany("INSERT INTO hobbies VALUES (?, ?, ?)", DEFAULT_HOBBIES)

            if not self.rules_path.exists():
                self.rules_path.write_text(
                    "# Öğrendiklerim (Kişisel Kurallar)\n"
                    "- Kod yazarken kısa ve net cümle kur.\n"
                    "- Oyun sırasında asla bölme.\n",
                    encoding="utf-8",
                )
            if not self.diary_path.exists():
                self.diary_path.write_text("# Minnak Günlüğü\n", encoding="utf-8")

    def get_personality(self) -> dict[str, float]:
        with self._lock:
            rows = self.conn.execute("SELECT trait, val FROM personality").fetchall()
            return dict(rows)

    def update_trait(self, trait: str, delta: float = 0.0, decay_rate: float = 0.05) -> float:
        with self._lock:
            row = self.conn.execute(
                "SELECT val, base, min_v, max_v FROM personality WHERE trait = ?", (trait,)
            ).fetchone()
            if not row:
                return 50.0
            val, base, lo, hi = row
            new_val = clamp(val + (base - val) * decay_rate + delta, lo, hi)
            with self.conn:
                self.conn.execute("UPDATE personality SET val = ? WHERE trait = ?", (new_val, trait))
            return new_val

    def get_weight(self, context: str, action: str) -> float:
        with self._lock:
            row = self.conn.execute(
                "SELECT weight FROM action_weights WHERE context = ? AND action = ?", (context, action)
            ).fetchone()
            return row[0] if row else 1.0

    def reinforce(self, context: str, action: str, reward: float) -> float:
        with self._lock:
            w = clamp(self.get_weight(context, action) + reward, 0.1, 3.0)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO action_weights VALUES (?, ?, ?) ON CONFLICT(context, action) DO UPDATE SET weight = ?",
                    (context, action, w, w),
                )
            return w

    def get_hobbies(self) -> list[tuple[str, str, float]]:
        with self._lock:
            return self.conn.execute("SELECT topic, source, weight FROM hobbies").fetchall()

    def pick_hobby(self) -> tuple[str, str, float]:
        hobbies = self.get_hobbies()
        if not hobbies:
            return DEFAULT_HOBBIES[0]
        return random.choices(hobbies, weights=[max(0.05, h[2]) for h in hobbies], k=1)[0]

    def reinforce_hobby(self, topic: str, reward: float) -> float:
        with self._lock:
            row = self.conn.execute("SELECT weight FROM hobbies WHERE topic = ?", (topic,)).fetchone()
            cur = row[0] if row else 1.0
            new_w = clamp(cur + reward, 0.1, 3.0)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO hobbies (topic, source, weight) VALUES (?, 'wiki', ?) "
                    "ON CONFLICT(topic) DO UPDATE SET weight = ?",
                    (topic, new_w, new_w),
                )
            return new_w

    def log_event(self, context: str, action: str, response: str, feedback: int = 0, user_msg: str = "") -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO logs (context, action, user_msg, response, feedback, processed) VALUES (?, ?, ?, ?, ?, 0)",
                (context, action, user_msg, response, feedback),
            )
            return cur.lastrowid or 0

    def load_recent_turns(self, max_age_sec: float = 600.0, limit: int = 4) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT user_msg, response, (strftime('%s', 'now') - strftime('%s', ts)) "
                "FROM logs WHERE action = 'sohbet' AND user_msg != '' AND response != '' "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        if not rows or rows[0][2] is None or rows[0][2] > max_age_sec:
            return []
        turns: list[dict] = []
        for u, r, _age in reversed(rows):
            turns.append({"role": "user", "content": u})
            turns.append({"role": "assistant", "content": r})
        return turns

    def get_logs_for_date(self, date_str: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, ts, context, action, user_msg, response, feedback FROM logs "
                "WHERE date(ts, 'localtime') = ? ORDER BY id ASC",
                (date_str,),
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "context": r[2], "action": r[3], "user_msg": r[4] or "", "response": r[5] or "", "feedback": r[6]}
            for r in rows
        ]

    def read_diary(self) -> str:
        with self._lock:
            return self.diary_path.read_text(encoding="utf-8") if self.diary_path.exists() else ""

    def has_diary_for_date(self, date_str: str) -> bool:
        return f"## {date_str}" in self.read_diary()

    def save_diary_entry(self, date_str: str, summary: str) -> bool:
        clean = re.sub(r"\s+", " ", (summary or "").strip())
        if not clean:
            return False
        with self._lock:
            text = self.read_diary() or "# Minnak Günlüğü\n"
            header = f"## {date_str}"
            block = f"{header}\n- {clean}\n"
            pattern = re.compile(rf"(?ms)^## {re.escape(date_str)}\s*\n.*?(?=^## |\Z)")
            if pattern.search(text):
                updated = pattern.sub(block + "\n", text).rstrip() + "\n"
            else:
                updated = text.rstrip() + f"\n\n{block}"
            self.diary_path.write_text(updated, encoding="utf-8")
            return True

    def build_factual_day_digest(self, date_str: str) -> tuple[str, str]:
        """O günkü loglardan sayı/istatistik içermeyen doğal özet + güncel olay dökümünü üretir."""
        logs = self.get_logs_for_date(date_str)
        clean_logs = [
            r for r in logs
            if not ((r.get("response") or "").startswith("[Çevrimdışı]") or "Ollama kapalı" in (r.get("response") or ""))
        ]
        if not clean_logs:
            return "", ""

        meta_tokens = ("günlü", "gunlu", "writing_diary", "hani kitabın", "defterin nerede")
        ctx_map = {
            "CODING": "kod yazarken",
            "BROWSING": "internette gezinirken",
            "GAMING": "oyun oynarken",
            "AFK": "bilgisayar başından kalktığında",
            "GENERAL": "bilgisayarda takılırken",
        }
        ctxs = [r.get("context") or "" for r in clean_logs if r.get("context")]
        main_ctx = next((c for c in ("CODING", "BROWSING", "GAMING") if c in ctxs), (ctxs[-1] if ctxs else "GENERAL"))
        ctx_phrase = ctx_map.get(main_ctx, "bilgisayardayken")

        real_chats = [
            r for r in clean_logs
            if (r.get("user_msg") or "").strip() and not any(t in r["user_msg"].lower() for t in meta_tokens)
        ]
        researches = [r for r in clean_logs if r.get("action") == "arastir"]
        topics = list(dict.fromkeys(r["user_msg"].strip()[:32] for r in reversed(real_chats)))[:3]
        topics.reverse()

        parts: list[str] = [f"Bugün {USER_NAME} {ctx_phrase} yanındaydım"]
        if topics:
            joined_topics = ", ".join(f"'{t}'" for t in topics)
            parts.append(f"beraber {joined_topics} üzerine konuştuk")
        if researches:
            last_res = (researches[-1].get("response") or "").strip()[:90]
            if last_res:
                parts.append(f"ayrıca yeni bir şeyler araştırıp şunu öğrendim: {last_res}")
            else:
                parts.append("arada yeni bilgiler araştırdım")
        natural_fallback = "; ".join(parts).rstrip(".") + "."

        filtered_for_llm = [
            r for r in clean_logs
            if not ((r.get("user_msg") or "").strip() and any(t in r["user_msg"].lower() for t in meta_tokens))
        ] or clean_logs

        llm_lines: list[str] = []
        total_len = 0
        for r in reversed(filtered_for_llm):
            u, resp = (r.get("user_msg") or "").strip(), (r.get("response") or "").strip()
            act, ctx = r.get("action") or "olay", ctx_map.get(r.get("context") or "GENERAL", "bilgisayardayken")
            if u:
                line = f"{USER_NAME} ({ctx}) yazdı: '{u[:90]}' -> Minnak: '{resp[:110]}'"
            elif act == "arastir":
                line = f"Araştırma bulgusu: {resp[:140]}"
            else:
                line = f"({ctx}) {resp[:110]}"
            if total_len + len(line) > 1000:
                break
            llm_lines.append(line)
            total_len += len(line)
        llm_lines.reverse()
        return natural_fallback, "\n".join(llm_lines)

    def recent_diary(self, days: int = 3, max_chars: int = 600) -> str:
        text = self.read_diary()
        if not text:
            return ""
        sections = [s.strip() for s in re.split(r"(?m)^(?=## )", text) if s.strip().startswith("## ")]
        if not sections:
            return ""
        joined = "\n".join(sections[-days:]).strip()
        return joined[:max_chars]

    def get_missing_diary_date(self) -> str | None:
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT date(ts, 'localtime') as d FROM logs "
                "WHERE date(ts, 'localtime') < date('now', 'localtime') ORDER BY d DESC LIMIT 7"
            ).fetchall()
            for (d,) in rows:
                if d and not self.has_diary_for_date(d):
                    return d
            return None

    def search_relevant_memories(self, text: str, limit: int = 3) -> str:
        words = [w for w in re.findall(r"[^\W\d_]{4,}", (text or "").casefold()) if w not in _STOP_WORDS]
        stems = list(dict.fromkeys(w[:5] for w in words))[:5]
        if not stems:
            return ""
        matches: list[str] = []
        seen_norm: set[str] = set()

        with self._lock:
            diary_lines = self.read_diary().splitlines()
            for ln in diary_lines:
                clean = ln.strip()
                if not clean or clean.startswith("#"):
                    continue
                low = clean.casefold()
                if any(s in low for s in stems):
                    norm = low[:80]
                    if norm not in seen_norm:
                        seen_norm.add(norm)
                        matches.append(clean[:200])
                        if len(matches) >= limit:
                            return "\n".join(matches)

            rows = self.conn.execute(
                "SELECT user_msg, response FROM logs WHERE user_msg != '' OR response != '' ORDER BY id DESC LIMIT 120"
            ).fetchall()
        for u, r in rows:
            snippet = f"{u or ''} -> {r or ''}".strip(" ->")
            low = snippet.casefold()
            if snippet and any(s in low for s in stems):
                norm = low[:80]
                if norm not in seen_norm:
                    seen_norm.add(norm)
                    matches.append(snippet[:200])
                    if len(matches) >= limit:
                        break
        return "\n".join(matches)

    def add_rule_candidate(self, rule: str) -> bool:
        clean = (rule or "").strip().lstrip("- ").strip()
        if len(clean) < 5 or len(clean) > 140:
            return False
        norm_key = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", clean.casefold())).strip()
        if not norm_key:
            return False
        with self._lock:
            existing = {
                re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", ln.strip()[2:].casefold())).strip()
                for ln in self.read_rules().splitlines()
                if ln.strip().startswith("- ")
            }
            if norm_key in existing:
                return False
            row = self.conn.execute("SELECT count, status FROM rule_candidates WHERE norm_key = ?", (norm_key,)).fetchone()
            if row:
                cnt, status = row
                if status == "approved":
                    return False
                new_cnt = cnt + 1
                new_status = "approved" if new_cnt >= 2 else "pending"
                with self.conn:
                    self.conn.execute(
                        "UPDATE rule_candidates SET count = ?, status = ?, text = ?, ts = CURRENT_TIMESTAMP WHERE norm_key = ?",
                        (new_cnt, new_status, clean, norm_key),
                    )
                return self.append_rule(clean) if new_status == "approved" else False
            with self.conn:
                self.conn.execute(
                    "INSERT INTO rule_candidates (norm_key, text, count, status) VALUES (?, ?, 1, 'pending')",
                    (norm_key, clean),
                )
            return False

    def promote_pending_candidates(self, max_promote: int = 2) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT norm_key, text FROM rule_candidates WHERE status = 'pending' ORDER BY count DESC, ts DESC LIMIT ?",
                (max_promote,),
            ).fetchall()
            promoted: list[str] = []
            for nk, txt in rows:
                if self.append_rule(txt):
                    promoted.append(txt)
                with self.conn:
                    self.conn.execute("UPDATE rule_candidates SET status = 'approved' WHERE norm_key = ?", (nk,))
            return promoted

    def update_last_log_feedback(self, feedback: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE logs SET feedback = ? WHERE id = (SELECT MAX(id) FROM logs)", (feedback,)
            )

    def update_log_feedback(self, log_id: int, feedback: int) -> None:
        if log_id <= 0:
            return
        with self._lock, self.conn:
            self.conn.execute("UPDATE logs SET feedback = ? WHERE id = ?", (feedback, log_id))

    def get_unprocessed_logs(self, limit: int = 25) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, ts, context, action, response, feedback FROM logs WHERE processed = 0 ORDER BY id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "context": r[2], "action": r[3], "response": r[4], "feedback": r[5]}
            for r in rows
        ]

    def mark_logs_processed(self, log_ids: list[int]) -> None:
        if not log_ids:
            return
        placeholders = ",".join("?" for _ in log_ids)
        with self._lock, self.conn:
            self.conn.execute(f"UPDATE logs SET processed = 1 WHERE id IN ({placeholders})", log_ids)

    def is_source_seen(self, url: str, title: str = "") -> bool:
        clean_url = (url or "").strip()
        norm_title = re.sub(r"\s+", " ", (title or "").strip().lower())
        with self._lock:
            if clean_url and self.conn.execute("SELECT 1 FROM seen_sources WHERE url = ?", (clean_url,)).fetchone():
                return True
            if norm_title:
                rows = self.conn.execute("SELECT title FROM seen_sources").fetchall()
                for (t,) in rows:
                    if re.sub(r"\s+", " ", (t or "").strip().lower()) == norm_title:
                        return True
            return False

    def mark_source_seen(self, url: str, title: str) -> None:
        clean_url = (url or "").strip()
        if not clean_url:
            return
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO seen_sources (url, title) VALUES (?, ?)",
                (clean_url, (title or clean_url).strip()),
            )

    def read_rules(self) -> str:
        with self._lock:
            return self.rules_path.read_text(encoding="utf-8") if self.rules_path.exists() else ""

    def append_rule(self, rule: str) -> bool:
        clean = rule.strip().lstrip("- ").strip()
        if not clean:
            return False
        key = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", clean.lower())).strip()
        with self._lock:
            existing = {
                re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", ln.strip()[2:].lower())).strip()
                for ln in self.read_rules().splitlines()
                if ln.strip().startswith("- ")
            }
            if key in existing:
                return False
            with self.rules_path.open("a", encoding="utf-8") as f:
                f.write(f"- {clean}\n")
            return True

    def remove_rule(self, rule_text: str) -> bool:
        target = rule_text.strip().lstrip("- ").strip()
        if not target:
            return False
        with self._lock:
            if not self.rules_path.exists():
                return False
            lines = self.rules_path.read_text(encoding="utf-8").splitlines()
            new_lines = [ln for ln in lines if not (ln.strip().startswith("- ") and ln.strip()[2:].strip() == target)]
            if len(new_lines) == len(lines):
                return False
            self.rules_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
            return True

    def compress_rules_if_needed(self, max_lines: int = 20, target_max: int = 15) -> bool:
        """ogrendiklerim.md >20 madde olursa manuel kuralları koruyarak <=15 maddeye sıkıştırır."""
        with self._lock:
            text = self.read_rules()
            bullets = [ln.strip()[2:].strip() for ln in text.splitlines() if ln.strip().startswith("- ")]
            if len(bullets) <= max_lines:
                return False

            fillers = re.compile(r"\b(kullanıcıya|kullanıcı|lütfen|her zaman|kesinlikle|mutlaka|bir şekilde)\b", re.I)
            auto_re = re.compile(r" sırasında .+ (azalt \(negatif tepki\)|yararlı)$")
            seen: dict[str, str] = {}
            for item in bullets:
                short = re.sub(r"\s+", " ", fillers.sub("", item)).strip(" .,:;")
                key = short.lower()
                if key and key not in seen:
                    seen[key] = short
            manual = [line for line in seen.values() if not auto_re.search(line)]
            auto = [line for line in seen.values() if auto_re.search(line)]
            kept_manual = manual[:target_max]
            rem = target_max - len(kept_manual)
            kept_auto = auto[-rem:] if rem > 0 else []
            compact = kept_manual + kept_auto
            out = "# Öğrendiklerim (Kişisel Kurallar)\n" + "".join(f"- {line}\n" for line in compact)
            self.rules_path.write_text(out, encoding="utf-8")
            return True
