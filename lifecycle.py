"""Katman 3: Yaşam Döngüsü (Sıfır LLM maliyetli Durum Makinesi + Otonom Tetikleyici Tablosu + Hobi Gezgini)."""
import json
import random
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from memory import Memory, USER_NAME, clamp
from sensors import SensorSnapshot, is_privacy_sensitive

# --- Durum Makinesi ve Dürtü Eşikleri ---
DEFAULT_COOLDOWN_SEC = 180.0
FORCED_SLEEP_DEFAULT_SEC = 300.0
SLEEP_ENERGY_THRESHOLD = 15.0
SLEEP_AFK_SEC = 600.0
WAKE_ENERGY_THRESHOLD = 40.0
WAKE_AFK_MAX_SEC = 5.0
SLEEP_ENERGY_GAIN = 1.5
ENERGY_DECAY_NORMAL = 0.10
ENERGY_DECAY_NIGHT = 0.15
WINDOW_CHANGE_MERAK_GAIN = 12.0
WINDOW_CHANGE_SIKILMA_DROP = 5.0
IDLE_SIKILMA_GAIN = 2.5
IDLE_MERAK_DROP = 1.0

CURIOUS_VISION_THRESHOLD = 85.0
CURIOUS_COMMENT_THRESHOLD = 72.0
BORED_RESEARCH_THRESHOLD = 65.0
COMMENT_MIN_WEIGHT = 0.35
RESEARCH_MIN_WEIGHT = 0.30
FOCUS_WORK_MIN = 90.0


class State(str, Enum):
    IDLE = "IDLE"
    CURIOUS = "CURIOUS"
    BORED = "BORED"
    SLEEPING = "SLEEPING"
    GAMING = "GAMING"
    READING = "READING"
    SPEAKING = "SPEAKING"
    VIBING = "VIBING"


@dataclass
class Drives:
    enerji: float = 100.0
    sikilma: float = 0.0
    merak: float = 0.0


OFFLINE_HOBBY_SNIPPETS = {
    "yapay zeka": "Küçük yerel modeller nicemleme (quantization) sayesinde dizüstü GPU'larda 1 sn altında yanıt üretebiliyor.",
    "bilim": "Ahtapotların üç kalbi ve sinir sistemlerinin büyük kısmı kollarında dağılmış haldedir.",
    "oyun geliştirme": "Kare süresi (frame pacing) kararlılığı ham FPS değerinden daha akıcı hissiyat verir.",
    "uzay": "Nötron yıldızlarının bir çay kaşığı maddesi Dünya'da milyarlarca ton ağırlığındadır.",
    "programlama": "Basit durum makineleri karmaşık karar ağaçlarından daha az hata üretir.",
}

# Tek merkezli otonom tetikleyici kayıt tablosu (cooldown, overlay, durum ve LLM prompt şablonu)
TRIGGER_SPECS: dict[str, dict] = {
    "sistem_stresi": {
        "cooldown": 600.0,
        "global_cd": False,
        "state": None,
        "overlay": "sweat",
        "overlay_ticks": 150,
        "log_action": "sistem_stresi",
        "prompt": lambda snap: (
            f"Sistem yük altında (CPU:%{snap.cpu_percent:.0f}, RAM:%{snap.ram_percent:.0f}, "
            f"En yoğun: {snap.top_process or 'bilinmiyor'}). {USER_NAME} için tek cümlelik esprili bir uyarı yap."
        ),
    },
    "odak_mola": {
        "cooldown": 3600.0,
        "global_cd": False,
        "state": None,
        "overlay": None,
        "overlay_ticks": 0,
        "log_action": "odak_mola",
        "prompt": lambda snap: (
            f"{USER_NAME} aralıksız {snap.continuous_work_min:.0f} dakikadır bilgisayarda çalışıyor. "
            "Su içmesi ve kısa mola vermesi için tek cümlelik tatlı bir hatırlatma yap."
        ),
    },
    "medya_yorum": {
        "cooldown": 45.0,
        "global_cd": False,
        "state": State.VIBING,
        "overlay": "vibing",
        "overlay_ticks": 150,
        "log_action": "medya_yorum",
        "prompt": lambda snap: (
            f"Şu an '{snap.media_title}' çalıyor/oynuyor. "
            "Parçaya/videoya ritim tutarak tek cümlelik kısa bir yorum yap."
        ),
    },
    "gece_uyarisi": {
        "cooldown": 1800.0,
        "global_cd": True,
        "state": None,
        "overlay": "yawn",
        "overlay_ticks": 150,
        "log_action": "gece_uyarisi",
        "prompt": lambda _snap: (
            "Gece çok geç oldu (02:00-06:00 arası). "
            f"Esneyerek {USER_NAME} için artık dinlenmesini tavsiye eden tek cümlelik uykulu bir söz söyle."
        ),
    },
    "yorum_yap_vision": {
        "cooldown": 0.0,
        "global_cd": True,
        "state": State.CURIOUS,
        "overlay": None,
        "overlay_ticks": 0,
        "use_vision": True,
        "log_action": "yorum_yap",
        "prompt": lambda snap: f"Aktif pencere: '{snap.window_title}' ({snap.context}). Ekrana bakıp kısa bir yorum yap.",
    },
    "yorum_yap": {
        "cooldown": 0.0,
        "global_cd": True,
        "state": State.CURIOUS,
        "overlay": None,
        "overlay_ticks": 0,
        "log_action": "yorum_yap",
        "prompt": lambda snap: f"Aktif pencere: '{snap.window_title}' ({snap.context}). Kısa, doğal bir laf at.",
    },
    "arastir": {
        "cooldown": 0.0,
        "global_cd": True,
        "state": State.READING,
        "overlay": "research",
        "overlay_ticks": 0,
        "is_hobby": True,
        "log_action": "arastir",
        "prompt": lambda _snap: "",
    },
}


class Lifecycle:
    def __init__(self, memory: Memory, cooldown_sec: float = DEFAULT_COOLDOWN_SEC):
        self.memory = memory
        self.state = State.IDLE
        self.drives = Drives()
        self.cooldown_sec = cooldown_sec
        self.last_action_ts = 0.0
        self.cooldowns: dict[str, float] = {}
        self.last_window = ""
        self.last_action: str | None = None
        self.last_context = "GENERAL"
        self.last_hobby_topic: str | None = None
        self.force_sleep_until: float = 0.0
        self.last_media_title: str = ""

    def set_forced_sleep(self, sleeping: bool, duration_sec: float = FORCED_SLEEP_DEFAULT_SEC) -> None:
        if sleeping:
            self.state = State.SLEEPING
            self.force_sleep_until = time.time() + duration_sec
        else:
            self.force_sleep_until = 0.0
            self.state = State.IDLE

    def _can_fire(self, key: str, now: float, in_global_cooldown: bool) -> bool:
        spec = TRIGGER_SPECS.get(key)
        if not spec:
            return False
        if spec.get("global_cd") and in_global_cooldown:
            return False
        cd = float(spec.get("cooldown", 0.0))
        return (now - self.cooldowns.get(key, 0.0)) >= cd

    def _fire(self, key: str, now: float) -> str:
        spec = TRIGGER_SPECS[key]
        if spec.get("state") is not None:
            self.state = spec["state"]
        self.cooldowns[key] = now
        self.last_action_ts = now
        self.last_action = key
        return key

    def tick(self, snap: SensorSnapshot) -> str | None:
        """Durumları günceller; otonom aksiyon gerekiyorsa TRIGGER_SPECS anahtarını döner."""
        self.last_context = snap.context
        if snap.is_gaming:
            self.state = State.GAMING
            return None

        if self.state == State.SLEEPING:
            self.drives.enerji = clamp(self.drives.enerji + SLEEP_ENERGY_GAIN, 0.0, 100.0)
            if time.time() < self.force_sleep_until:
                return None
            if snap.afk_seconds < WAKE_AFK_MAX_SEC and self.drives.enerji > WAKE_ENERGY_THRESHOLD:
                self.state = State.IDLE
            return None

        decay = ENERGY_DECAY_NIGHT if snap.is_late_night else ENERGY_DECAY_NORMAL
        self.drives.enerji = clamp(self.drives.enerji - decay, 0.0, 100.0)
        if snap.window_title != self.last_window and snap.window_title:
            self.drives.merak = clamp(self.drives.merak + WINDOW_CHANGE_MERAK_GAIN, 0.0, 100.0)
            self.drives.sikilma = clamp(self.drives.sikilma - WINDOW_CHANGE_SIKILMA_DROP, 0.0, 100.0)
            self.last_window = snap.window_title
        else:
            self.drives.sikilma = clamp(self.drives.sikilma + IDLE_SIKILMA_GAIN, 0.0, 100.0)
            self.drives.merak = clamp(self.drives.merak - IDLE_MERAK_DROP, 0.0, 100.0)

        if self.drives.enerji < SLEEP_ENERGY_THRESHOLD or snap.afk_seconds > SLEEP_AFK_SEC:
            self.state = State.SLEEPING
            return None

        now = time.time()
        in_cooldown = (now - self.last_action_ts) < self.cooldown_sec

        if snap.system_stress and self._can_fire("sistem_stresi", now, in_cooldown):
            return self._fire("sistem_stresi", now)

        if snap.continuous_work_min >= FOCUS_WORK_MIN and self._can_fire("odak_mola", now, in_cooldown):
            return self._fire("odak_mola", now)

        if snap.media_title:
            if snap.media_title != self.last_media_title and self._can_fire("medya_yorum", now, in_cooldown):
                self.last_media_title = snap.media_title
                return self._fire("medya_yorum", now)
        else:
            self.last_media_title = ""

        if snap.is_late_night and self._can_fire("gece_uyarisi", now, in_cooldown):
            return self._fire("gece_uyarisi", now)

        if (
            self.drives.merak > CURIOUS_VISION_THRESHOLD
            and not is_privacy_sensitive(snap.window_title)
            and self.memory.get_weight(snap.context, "yorum_yap") > COMMENT_MIN_WEIGHT
            and self._can_fire("yorum_yap_vision", now, in_cooldown)
        ):
            self.drives.merak = 25.0
            return self._fire("yorum_yap_vision", now)

        if (
            self.drives.merak > CURIOUS_COMMENT_THRESHOLD
            and self.memory.get_weight(snap.context, "yorum_yap") > COMMENT_MIN_WEIGHT
            and self._can_fire("yorum_yap", now, in_cooldown)
        ):
            self.drives.merak = 25.0
            return self._fire("yorum_yap", now)

        if (
            self.drives.sikilma > BORED_RESEARCH_THRESHOLD
            and self.memory.get_weight(snap.context, "arastir") > RESEARCH_MIN_WEIGHT
            and self._can_fire("arastir", now, in_cooldown)
        ):
            self.drives.sikilma = 20.0
            return self._fire("arastir", now)

        if self.drives.merak > CURIOUS_COMMENT_THRESHOLD:
            self.state = State.CURIOUS
        elif self.drives.sikilma > BORED_RESEARCH_THRESHOLD:
            self.state = State.BORED
        elif snap.media_title:
            self.state = State.VIBING
        else:
            self.state = State.IDLE
        return None

    def explore_hobby_with_url(self) -> tuple[str, str, str]:
        """Önce açık web araması (DuckDuckGo), bulamazsa Wikipedia/HN/Reddit ve kaynak URL döner."""
        from brain import build_discovery_query, search_open_web

        topic, query = build_discovery_query(self.memory)
        self.last_hobby_topic = topic
        web_res = search_open_web(query, self.memory)
        if web_res:
            return topic, f"{web_res['title']}: {web_res['snippet']}", web_res["url"]

        source = dict((h[0], h[1]) for h in self.memory.get_hobbies()).get(topic, "wiki")
        headers = {"User-Agent": "MinnakDesktopPet/1.0"}
        try:
            if source == "wiki":
                req = urllib.request.Request(
                    "https://tr.wikipedia.org/api/rest_v1/page/random/summary", headers=headers
                )
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    title = data.get("title", "")
                    extract = data.get("extract", "")[:140]
                    url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                    if title and not self.memory.is_source_seen(url, title):
                        self.memory.mark_source_seen(url, title)
                        return topic, f"{title}: {extract}", url
            elif source == "hn":
                req = urllib.request.Request(
                    "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=8", headers=headers
                )
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    hits = json.loads(resp.read().decode("utf-8")).get("hits", [])
                    random.shuffle(hits)
                    for hit in hits:
                        title = hit.get("title", "")
                        url = hit.get("url") or (f"https://news.ycombinator.com/item?id={hit.get('objectID')}" if hit.get("objectID") else "")
                        if title and not self.memory.is_source_seen(url, title):
                            self.memory.mark_source_seen(url, title)
                            return topic, title, url
            elif source == "reddit":
                req = urllib.request.Request(
                    "https://www.reddit.com/r/technology/hot.json?limit=8", headers=headers
                )
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    children = json.loads(resp.read().decode("utf-8")).get("data", {}).get("children", [])
                    random.shuffle(children)
                    for child in children:
                        d = child.get("data", {})
                        title = d.get("title", "")
                        perm = d.get("permalink", "")
                        url = f"https://www.reddit.com{perm}" if perm else d.get("url", "")
                        if title and not self.memory.is_source_seen(url, title):
                            self.memory.mark_source_seen(url, title)
                            return topic, title, url
        except Exception:
            pass
        # shortcut: ağ kapalıysa çevrimdışı hobi özeti döner
        return topic, OFFLINE_HOBBY_SNIPPETS.get(topic, f"{topic} hakkında ilginç bir teknik not okudum."), ""
