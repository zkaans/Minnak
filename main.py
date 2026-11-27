"""Minnak Ana Orkestratör (Asenkron İşçi + Komut/Tetikleyici Yönlendirici + Katman 1-6 Bağlantısı)."""
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication
from brain import Brain, REFLEX_PATTERNS, search_open_web
from lifecycle import Lifecycle, TRIGGER_SPECS
from memory import Memory, USER_NAME
from sensors import (
    SensorSnapshot,
    capture_low_res_screen,
    classify_context,
    get_system_status_summary,
    is_vision_prompt,
    read_sensors,
)
from ui import MinnakWindow

# --- Sohbet Komutu Kalıpları ve Zamanlama Sabitleri ---
TICK_INTERVAL_MS = 5000
LLM_COOLDOWN_SEC = 4.0
INIT_COOLDOWN_SEC = 5.0
REFLEX_MAX_WORDS = 4

_QUESTION_RE = re.compile(r"\b(mı|mi|mu|mü|musun|misin|musunuz|misiniz|nasıl|neden|niye|ne zaman|kim)\b")
_REMEMBER_PREFIXES = ("bunu hatırla:", "hatırla:")
_RESEARCH_PREFIXES = ("araştır:", "şunu araştır:", "şunu araştır", "araştır ")
_RESEARCH_PHRASES = ("bana ilginç bir şey anlat", "ilginç bir şey bul")
_SYSTEM_STATUS_PHRASES = ("sistem durumu", "ne kasıyor", "sistem yükü")


def match_reflex(text: str) -> str | None:
    low = text.strip().lower()
    if not low or "?" in low or len(low.split()) > REFLEX_MAX_WORDS or _QUESTION_RE.search(low):
        return None
    for pat, action in REFLEX_PATTERNS:
        if pat.search(low):
            return action
    return None


def _extract_research_query(text: str) -> str | None:
    stripped = text.strip()
    low = stripped.lower()
    if low == "araştır":
        return ""
    for prefix in _RESEARCH_PREFIXES:
        if low.startswith(prefix):
            return stripped[len(prefix):].strip(" :")
    if any(k in low for k in _RESEARCH_PHRASES):
        return ""
    return None


def _is_diary_open_cmd(text: str) -> bool:
    low = text.strip().lower()
    if low in ("günlük", "gunluk", "hani kitabın", "defterin nerede"):
        return True
    if "günlü" in low or "gunlu" in low:
        if any(w in low for w in ("aç", "oku", "göster", "ne yazdın", "yazdıkların", "nerede")):
            return True
        if "bak" in low and not any(w in low for w in ("yaz", "ekle", "tut")):
            return True
    return False


def _is_diary_write_cmd(text: str) -> bool:
    low = text.strip().lower()
    if low in ("writing_diary", "gunluk_yaz"):
        return True
    if "günlü" in low or "gunlu" in low:
        return any(w in low for w in ("yaz", "not", "tut", "ekle", "kaydet"))
    return False


class WorkerSignals(QObject):
    response_ready = pyqtSignal(dict, str, bool, int)


class MinnakController:
    def __init__(self):
        self.memory = Memory()
        self.lifecycle = Lifecycle(self.memory)
        self.brain = Brain(self.memory)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.signals = WorkerSignals()
        self.signals.response_ready.connect(self._on_llm_response)

        self.last_ctx = "GENERAL"
        self.last_action = "yorum_yap"
        self.was_gaming = False
        self.window: MinnakWindow | None = None
        self._llm_busy = 0
        self._llm_cooldown_until = 0.0
        self._req_id = 0
        self._reflex_handled_ids: set[int] = set()
        self._auto_future = None
        self._auto_cancel: threading.Event | None = None
        self._active_bubble_meta: tuple[int, str, str] | None = None

        # Tek merkezli sohbet komutu yönlendirme tablosu (yeni komut eklemek/kaldırmak için tek liste)
        self.chat_commands = [
            ("diary_open", self._cmd_diary_open),
            ("diary_write", self._cmd_diary_write),
            ("system_status", self._cmd_system_status),
            ("research", self._cmd_research),
        ]

    def _cancel_autonomous(self) -> None:
        if self._auto_cancel is not None:
            self._auto_cancel.set()
        if self._auto_future is not None and not self._auto_future.done():
            self._auto_future.cancel()

    def start_background_services(self) -> None:
        self._llm_busy += 1

        def init_task():
            try:
                online = self.brain.ensure_ollama(start_if_down=True)
                if not online:
                    self.signals.response_ready.emit(
                        {"duygu": "normal", "mesaj": "Ollama kapalı. Sohbet için Ollama'yı başlat.", "aksiyon": "yok"},
                        self.lifecycle.state.value, False, 0,
                    )
                elif not self.was_gaming:
                    self.brain.warmup()
                missing_date = self.memory.get_missing_diary_date()
                if missing_date and not self.was_gaming:
                    self.brain.write_diary(missing_date, is_gaming=False)
            finally:
                self._llm_busy = max(0, self._llm_busy - 1)
                self._llm_cooldown_until = time.monotonic() + INIT_COOLDOWN_SEC

        self.executor.submit(init_task)

    def get_status_snapshot(self) -> dict:
        traits = self.memory.get_personality()
        return {
            "enerji": self.lifecycle.drives.enerji,
            "merak": self.lifecycle.drives.merak,
            "sikilma": self.lifecycle.drives.sikilma,
            "samimiyet": traits.get("samimiyet", 50.0),
            "state": self.lifecycle.state.value,
            "ollama_online": self.brain.ollama_online,
        }

    def execute_command(self, action: str) -> None:
        if not action or action == "yok":
            return
        if action == "gunluk_yaz":
            self.trigger_diary_write()
            return
        if self.was_gaming:
            if self.window:
                self.window.show_message("Oyun modunda hareket kapalı.", self.lifecycle.state.value, autonomous=False)
            return
        if action == "uyu":
            self.lifecycle.set_forced_sleep(True)
        elif action == "uyan":
            self.lifecycle.set_forced_sleep(False)
        if self.window:
            self.window.execute_action(action)

    def _filter_self_gpu_spike(self, snap: SensorSnapshot) -> SensorSnapshot:
        """Kendi LLM çıkarımı GPU'yu yükselttiğinde sahte oyun modu tetiklenmesini engeller."""
        if snap.is_gaming and (self._llm_busy > 0 or time.monotonic() < self._llm_cooldown_until):
            ctx_no_gpu, game_no_gpu = classify_context(
                snap.window_title, snap.process_name, snap.afk_seconds, gpu_load=0.0, is_fullscreen=snap.is_fullscreen
            )
            if not game_no_gpu:
                snap.is_gaming = False
                snap.context = ctx_no_gpu
        return snap

    def _on_llm_response(self, res: dict, state_str: str, autonomous: bool, req_id: int) -> None:
        if (autonomous or req_id > 0) and req_id != self._req_id:
            return
        if self.window:
            self.window.set_overlay(None)
        if not autonomous and req_id > 0 and req_id == self._req_id and req_id not in self._reflex_handled_ids:
            act = res.get("aksiyon", "yok")
            if act and act != "yok":
                self.execute_command(act)
        log_id = int(res.get("_log_id", 0) or 0)
        if log_id > 0:
            self._active_bubble_meta = (
                log_id,
                str(res.get("_ctx", self.last_ctx)),
                str(res.get("_act", self.last_action)),
            )
            allow_fb = True
        else:
            self._active_bubble_meta = None
            allow_fb = False
        if self.window:
            self.window.show_message(
                res.get("mesaj", "..."),
                state=self.lifecycle.state.value,
                autonomous=autonomous,
                source_url=res.get("source_url", ""),
                allow_feedback=allow_fb,
            )
            if self.window.diary_dialog and self.window.diary_dialog.isVisible():
                self.window.diary_dialog.refresh()

    def _submit_brain(
        self, prompt: str, is_gaming: bool, state_str: str, ctx: str, act: str,
        image_b64: str | None = None, autonomous: bool = True, feedback: int = 0,
        req_id: int = 0, remember_turn: bool = False, user_msg: str = "",
    ) -> None:
        self._llm_busy += 1
        cancel_ev = threading.Event() if autonomous else None
        if autonomous:
            self._auto_cancel = cancel_ev
        target_req = self._req_id if autonomous else req_id

        def task():
            try:
                res = self.brain.generate(
                    prompt, is_gaming=is_gaming, image_b64=image_b64,
                    remember_turn=remember_turn, cancel_event=cancel_ev,
                )
                if cancel_ev is not None and (cancel_ev.is_set() or res.get("cancelled")):
                    return
                log_id = self.memory.log_event(ctx, act, res.get("mesaj", ""), feedback=feedback, user_msg=user_msg)
                res["_log_id"] = log_id
                res["_ctx"] = ctx
                res["_act"] = act
                self.signals.response_ready.emit(res, state_str, autonomous, target_req)
            finally:
                self._llm_busy = max(0, self._llm_busy - 1)
                self._llm_cooldown_until = time.monotonic() + LLM_COOLDOWN_SEC

        fut = self.executor.submit(task)
        if autonomous:
            self._auto_future = fut

    def _submit_autonomous_hobby(self, snap: SensorSnapshot, state_str: str) -> None:
        self._llm_busy += 1
        cancel_ev = threading.Event()
        self._auto_cancel = cancel_ev
        start_req = self._req_id

        def hobby_task():
            try:
                topic, snippet, url = self.lifecycle.explore_hobby_with_url()
                if cancel_ev.is_set():
                    return
                res = self.brain.generate(
                    f"İlgi alanın '{topic}' hakkında şunu okudun: '{snippet}'. {USER_NAME} için tek cümleyle ilginç bir şey paylaş.",
                    is_gaming=snap.is_gaming,
                    cancel_event=cancel_ev,
                )
                if cancel_ev.is_set() or res.get("cancelled"):
                    return
                if url:
                    res["source_url"] = url
                log_id = self.memory.log_event(snap.context, "arastir", res.get("mesaj", ""))
                res["_log_id"] = log_id
                res["_ctx"] = snap.context
                res["_act"] = "arastir"
                self.signals.response_ready.emit(res, state_str, True, start_req)
            finally:
                self._llm_busy = max(0, self._llm_busy - 1)
                self._llm_cooldown_until = time.monotonic() + LLM_COOLDOWN_SEC

        self._auto_future = self.executor.submit(hobby_task)

    def trigger_diary_write(self, date_str: str | None = None) -> None:
        if self.was_gaming:
            if self.window:
                self.window.show_message("Oyun modunda günlük yazımı ertelendi.", self.lifecycle.state.value, autonomous=False)
            return
        if self.window:
            self.window.show_thinking()
            self.window.set_overlay("writing_diary", min_ticks=150)
        self._llm_busy += 1

        def diary_task():
            try:
                summary = self.brain.write_diary(date_str, is_gaming=self.was_gaming)
                msg = f"Günlüğe yazdım: {summary}" if summary else "Günlüğe yazılacak yeni bir şey bulamadım."
                self.signals.response_ready.emit(
                    {"duygu": "normal", "mesaj": msg, "aksiyon": "yok"},
                    self.lifecycle.state.value,
                    False,
                    0,
                )
            finally:
                self._llm_busy = max(0, self._llm_busy - 1)
                self._llm_cooldown_until = time.monotonic() + LLM_COOLDOWN_SEC

        self.executor.submit(diary_task)

    def tick(self) -> None:
        snap = self._filter_self_gpu_spike(read_sensors())
        self.last_ctx = snap.context

        if self.window:
            self.window.update_environment(
                None,
                snap.is_gaming,
                same_pid=(snap.pid == os.getpid()),
                enerji=self.lifecycle.drives.enerji,
                sikilma=self.lifecycle.drives.sikilma,
                merak=self.lifecycle.drives.merak,
            )

        if not self.was_gaming and snap.is_gaming:
            self.was_gaming = True
            self.executor.submit(self.brain.evacuate_vram)
            self.executor.submit(self.brain.set_ollama_priority, True)
            if self.window:
                self.window.set_ghost_mode(True)
                self.window.show_message("Oyun modu: VRAM boşaltıldı.", "GAMING", autonomous=False)
            return

        if self.was_gaming and not snap.is_gaming:
            self.was_gaming = False
            self.executor.submit(self.brain.set_ollama_priority, False)
            if self.window:
                self.window.set_ghost_mode(False)

        action = self.lifecycle.tick(snap)
        if self.window:
            self.window.set_state(self.lifecycle.state.value)
            if snap.system_stress:
                self.window.set_overlay("sweat", min_ticks=150)
        if not action:
            return

        spec = TRIGGER_SPECS.get(action)
        if not spec:
            return
        self.last_action = action
        state_str = self.lifecycle.state.value

        if self.window and spec.get("overlay"):
            self.window.set_overlay(spec["overlay"], min_ticks=int(spec.get("overlay_ticks", 0)))

        if spec.get("is_hobby"):
            self._submit_autonomous_hobby(snap, state_str)
            return

        img_b64 = capture_low_res_screen(snap.window_title) if spec.get("use_vision") else None
        self._submit_brain(
            spec["prompt"](snap),
            snap.is_gaming,
            state_str,
            snap.context,
            spec.get("log_action", action),
            image_b64=img_b64,
            autonomous=True,
        )

    # --- Sohbet Komutu İşleyicileri (chat_commands tablosundan çağrılır) ---
    def _cmd_diary_open(self, text: str, _low: str, _snap: SensorSnapshot, _req_id: int) -> bool:
        if not _is_diary_open_cmd(text):
            return False
        if self.window:
            self.window.open_diary()
            self.window.show_message("Günlüğümü açtım.", self.lifecycle.state.value, autonomous=False)
        return True

    def _cmd_diary_write(self, text: str, _low: str, _snap: SensorSnapshot, _req_id: int) -> bool:
        if not _is_diary_write_cmd(text):
            return False
        self.trigger_diary_write()
        return True

    def _cmd_system_status(self, _text: str, low: str, snap: SensorSnapshot, _req_id: int) -> bool:
        if not any(k in low for k in _SYSTEM_STATUS_PHRASES):
            return False
        summary = get_system_status_summary()
        self.memory.log_event(snap.context, "sistem_durumu", summary, feedback=0)
        if self.window:
            self.window.set_overlay(None)
            self.window.show_message(summary, self.lifecycle.state.value, autonomous=False)
        return True

    def _cmd_research(self, text: str, _low: str, snap: SensorSnapshot, req_id: int) -> bool:
        research_query = _extract_research_query(text)
        if research_query is None:
            return False
        if self.window:
            self.window.show_thinking()
            self.window.set_overlay("research")
        self._llm_busy += 1

        def research_task():
            try:
                url = ""
                if research_query:
                    web_res = search_open_web(research_query, self.memory)
                    if web_res:
                        url = web_res["url"]
                        prompt_text = (
                            f"{USER_NAME} '{research_query}' konusunu araştırmanı istedi. "
                            f"Şu kaynak bulundu ({web_res['title']}): '{web_res['snippet']}'. "
                            f"{USER_NAME} için 1-2 cümleyle ilginç ve net özetle."
                        )
                    else:
                        prompt_text = (
                            f"{USER_NAME} '{research_query}' konusunu araştırmanı istedi ama internetten sayfa çekilemedi. "
                            f"Mevcut bilginle 1-2 cümleyle net cevapla."
                        )
                else:
                    topic, snippet, url = self.lifecycle.explore_hobby_with_url()
                    prompt_text = (
                        f"İlgi alanın '{topic}' hakkında şu bulundu: '{snippet}'. "
                        f"{USER_NAME} için 1-2 cümleyle ilginç ve net paylaş."
                    )
                res = self.brain.generate(prompt_text, is_gaming=snap.is_gaming, remember_turn=True)
                if url:
                    res["source_url"] = url
                log_id = self.memory.log_event(snap.context, "arastir", res.get("mesaj", ""), feedback=0, user_msg=text)
                res["_log_id"] = log_id
                res["_ctx"] = snap.context
                res["_act"] = "arastir"
                self.signals.response_ready.emit(res, self.lifecycle.state.value, False, req_id)
            finally:
                self._llm_busy = max(0, self._llm_busy - 1)
                self._llm_cooldown_until = time.monotonic() + LLM_COOLDOWN_SEC

        self.executor.submit(research_task)
        return True

    def on_user_message(self, text: str) -> None:
        self._cancel_autonomous()
        low = text.strip().lower()
        for prefix in _REMEMBER_PREFIXES:
            if low.startswith(prefix):
                rule = text.strip()[len(prefix):].strip()
                added = self.memory.append_rule(rule)
                if self.window:
                    msg = "Hafızaya eklendi." if added else "Bunu zaten biliyorum."
                    self.window.show_message(msg, self.lifecycle.state.value, autonomous=False)
                return

        snap = self._filter_self_gpu_spike(read_sensors())
        self.memory.update_trait("samimiyet", delta=1.5)
        self._req_id += 1
        req_id = self._req_id

        for _cmd_name, handler in self.chat_commands:
            if handler(text, low, snap, req_id):
                return

        if self.window:
            self.window.show_thinking()
        reflex = match_reflex(text)
        if reflex:
            self._reflex_handled_ids.add(req_id)
            self.execute_command(reflex)

        use_vision = not snap.is_gaming and is_vision_prompt(text)
        img_b64 = (
            capture_low_res_screen(snap.window_title, force_user_request=True)
            if use_vision
            else None
        )
        prompt_text = f"[Aktif Pencere: {snap.window_title}]\n{text}" if img_b64 and snap.window_title else text
        self._submit_brain(
            prompt_text, snap.is_gaming, self.lifecycle.state.value, snap.context, "sohbet",
            image_b64=img_b64, autonomous=False, feedback=0, req_id=req_id, remember_turn=True, user_msg=text,
        )

    def on_feedback(self, score: int, ignored: bool = False) -> None:
        if self._active_bubble_meta is not None:
            log_id, ctx, act = self._active_bubble_meta
            self._active_bubble_meta = None
        else:
            log_id, ctx, act = 0, self.last_ctx, self.last_action
        reward = -0.10 if ignored else (0.20 if score > 0 else -0.25)
        act_key = "yorum_yap" if act == "yorum_yap_vision" else act
        self.memory.reinforce(ctx, act_key, reward)
        if act == "arastir" and self.lifecycle.last_hobby_topic:
            self.memory.reinforce_hobby(self.lifecycle.last_hobby_topic, reward)
        if ignored:
            if log_id > 0:
                self.memory.update_log_feedback(log_id, -1)
        else:
            self.memory.update_trait("samimiyet", delta=1.0 if score > 0 else -1.5)
            if log_id > 0:
                self.memory.update_log_feedback(log_id, score)
            else:
                self.memory.update_last_log_feedback(score)

    def on_pet(self) -> None:
        self.memory.update_trait("samimiyet", delta=0.02)
        self.lifecycle.drives.enerji = min(100.0, self.lifecycle.drives.enerji + 1.5)

    def on_night_reflection(self) -> None:
        def task():
            added = self.brain.run_night_reflection()
            msg = f"Gece yansıması: {len(added)} yeni kural." if added else "Yeni yansıma verisi yok."
            self.signals.response_ready.emit({"duygu": "normal", "mesaj": msg, "aksiyon": "yok"}, self.lifecycle.state.value, False, 0)

        self.executor.submit(task)


def _run_visual_demo(ctrl: MinnakController, win: MinnakWindow) -> None:
    ctrl.tick()
    win.show_message("Test 1: Pencere üstüne zıplıyorum!", "CURIOUS", False)

    def step_drop():
        if not win.perch_rect or win.perch_rect[1] < 20:
            win.perch_rect = (180, 200, 960, 720)
        l, t, r, _ = win.perch_rect
        win.cat_cx = float((l + 60 + r - 180) // 2)
        win.cat_bottom_y = float(t - 80)
        win.vy = 2.0

    def step_walk():
        win.show_message("Test 2: Pencere üstünde yürüyüp kuyruk sallıyorum!", "IDLE", False)
        if win.perch_rect:
            l, _t, r, _b = win.perch_rect
            win.walk_target_x = float(min(r - 185, max(l + 55, win.cat_cx + 110)))

    def step_done():
        win.show_message("Test tamam! Beni tutup pencereye bırakabilirsin.", "IDLE", False)

    QTimer.singleShot(800, step_drop)
    QTimer.singleShot(2500, step_walk)
    QTimer.singleShot(5200, step_done)


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    ctrl = MinnakController()
    win = MinnakWindow(
        ctrl.on_user_message,
        ctrl.on_feedback,
        ctrl.on_pet,
        ctrl.on_night_reflection,
        on_action=ctrl.execute_command,
        get_status=ctrl.get_status_snapshot,
        memory=ctrl.memory,
        on_check_ollama=lambda: ctrl.brain.ensure_ollama(True),
        on_clear_history=ctrl.brain.clear_history,
        on_write_diary=ctrl.trigger_diary_write,
    )
    ctrl.window = win
    win.show()
    ctrl.start_background_services()

    if "--demo" in sys.argv:
        _run_visual_demo(ctrl, win)

    timer = QTimer()
    timer.timeout.connect(ctrl.tick)
    timer.start(TICK_INTERVAL_MS)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
