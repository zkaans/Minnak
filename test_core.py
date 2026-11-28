"""Minnak Katman 1-6 Kapsamlı Doğrulama Testi."""
import base64
import io
import threading
from pathlib import Path
from unittest.mock import patch
from PyQt6.QtCore import QByteArray
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication
from brain import ACTION_SPECS, ACTIONS, Brain, MENU_ACTIONS, search_open_web
from dialogs import MinnakDiaryDialog, MinnakMenuDialog
from lifecycle import Lifecycle, State, TRIGGER_SPECS
from main import MinnakController, _extract_research_query, _is_diary_open_cmd, _is_diary_write_cmd, match_reflex
from memory import Memory
from sensors import (
    SensorSnapshot,
    _update_continuous_work,
    capture_low_res_screen,
    check_is_late_night,
    classify_context,
    detect_media_title,
    extract_media_from_window,
    find_climb_edge,
    get_active_window,
    get_system_status_summary,
    is_vision_prompt,
    read_sensors,
)
from sprites import CAT_SIZE, CatSpriteRenderer, DEBUG_ANIM_SPECS, OVERLAY_RENDERERS, PALETTES
from ui import MinnakWindow


def test_all() -> None:
    app = QApplication.instance() or QApplication([])
    tmp_db = Path(__file__).parent / "_test_minnak.db"
    tmp_md = Path(__file__).parent / "_test_ogrendiklerim.md"
    tmp_diary = Path(__file__).parent / "_test_gunluk.md"
    tmp_db.unlink(missing_ok=True)
    tmp_md.unlink(missing_ok=True)
    tmp_diary.unlink(missing_ok=True)
    mem = None
    ctrl = None
    try:
        # 1. Katman 2: Hibrit oyun tespiti & rect okuma & gizlilik ekran filtresi
        snap = read_sensors()
        assert snap.afk_seconds >= 0.0 and snap.gpu_percent >= 0.0
        _t, _p, _pid, rect, _fs = get_active_window()
        assert isinstance(rect, tuple) and len(rect) == 4

        ctx_code, gm_code = classify_context("main.py - Visual Studio Code", "code.exe", 1.0, gpu_load=5.0, is_fullscreen=False)
        assert ctx_code == "CODING" and not gm_code
        ctx_gpu, gm_gpu = classify_context("Blender Render", "blender.exe", 0.0, gpu_load=65.0, is_fullscreen=False)
        assert ctx_gpu == "GAMING" and gm_gpu
        ctx_val, gm_val = classify_context("VALORANT", "valorant.exe", 0.0, gpu_load=10.0, is_fullscreen=True)
        assert ctx_val == "GAMING" and gm_val
        assert capture_low_res_screen("Bitwarden Vault - Chrome") is None
        assert is_vision_prompt("ekrana bak bu hata ne")
        assert "CPU:" in get_system_status_summary()

        b64_720 = capture_low_res_screen("Test Editor")
        assert b64_720
        img_720 = QImage.fromData(QByteArray(base64.b64decode(b64_720)))
        assert not img_720.isNull() and img_720.width() <= 1280 and img_720.height() <= 720

        # 2. Katman 6: Hobi ağırlığı, Kişilik clamp, >20 kural sıkıştırma
        mem = Memory(tmp_db, tmp_md, tmp_diary)
        hobbies = mem.get_hobbies()
        assert len(hobbies) >= 5
        topic, _src, _w = mem.pick_hobby()
        assert mem.reinforce_hobby(topic, 5.0) == 3.0
        assert mem.reinforce_hobby(topic, -10.0) == 0.1
        assert abs(mem.reinforce("CODING", "yorum_yap", -0.10) - 0.90) < 1e-6
        assert mem.update_trait("samimiyet", delta=200.0) == 95.0

        for i in range(25):
            mem.append_rule(f"Kullanıcı için kural numarası {i} her zaman kısa tut.")
        # Tekilleştirme + silme kontrolü
        assert mem.append_rule("Kısa cevap ver.") is True
        assert mem.append_rule("kisa cevap ver") is True
        assert mem.append_rule("Kısa cevap ver!") is False
        assert mem.append_rule("İyi test kuralı") is True
        assert mem.append_rule("iyi test kuralı") is False
        assert mem.remove_rule("İyi test kuralı") is True
        assert mem.remove_rule("Kısa cevap ver.") is True
        assert mem.remove_rule("Olmayan kural") is False
        assert mem.compress_rules_if_needed(max_lines=20, target_max=15) is True
        bullets = [ln for ln in mem.read_rules().splitlines() if ln.strip().startswith("- ")]
        assert 1 <= len(bullets) <= 15

        # Otomatik kurallar >20 satırı aşınca manuel kurallar korunmalı
        tmp_rules2 = Path(__file__).parent / "_test_rules_preserve.md"
        tmp_rules2.unlink(missing_ok=True)
        mem_r = None
        try:
            mem_r = Memory(tmp_db, tmp_rules2, tmp_diary)
            mem_r.append_rule("Manuel kural korunmalı")
            for idx in range(25):
                mem_r.append_rule(f"CTX{idx} sırasında act_{idx} yararlı")
            assert mem_r.compress_rules_if_needed(max_lines=20, target_max=15) is True
            rules_txt = mem_r.read_rules()
            assert "Oyun sırasında asla bölme" in rules_txt and "Manuel kural korunmalı" in rules_txt
        finally:
            if mem_r is not None:
                mem_r.conn.close()
            tmp_rules2.unlink(missing_ok=True)

        # 3. Katman 3: Durum makinesi (GAMING, READING, CURIOUS vision, force_sleep)
        lc = Lifecycle(mem, cooldown_sec=0.0)
        assert lc.tick(SensorSnapshot(window_title="CS2", is_gaming=True, context="GAMING")) is None
        assert lc.state == State.GAMING

        lc_sleep = Lifecycle(mem, cooldown_sec=0.0)
        lc_sleep.set_forced_sleep(True, duration_sec=60.0)
        assert lc_sleep.tick(SensorSnapshot(afk_seconds=0.0, context="GENERAL")) is None
        assert lc_sleep.state == State.SLEEPING
        lc_sleep.set_forced_sleep(False)
        assert lc_sleep.state == State.IDLE

        lc2 = Lifecycle(mem, cooldown_sec=0.0)
        lc2.drives.merak = 90.0
        assert lc2.tick(SensorSnapshot(window_title="main.py - Code", context="CODING")) == "yorum_yap_vision"
        lc2.drives.sikilma = 85.0
        assert lc2.tick(SensorSnapshot(window_title="main.py - Code", context="CODING")) == "arastir"
        assert lc2.state == State.READING

        # 4. Katman 4 & 5: Parametreler, VRAM tahliye mock, aksiyon şeması, 4-tur geçmiş, Gece yansıması
        brain = Brain(mem)
        gpu_opts, gpu_ka = brain.build_options(is_gaming=False)
        cpu_opts, cpu_ka = brain.build_options(is_gaming=True)
        assert gpu_opts["num_gpu"] > 0 and gpu_ka == "10m"
        assert cpu_opts["num_gpu"] == 0 and cpu_opts["num_thread"] == 3 and cpu_ka == "5m"

        with patch.object(brain, "_post", return_value={"done": True}) as mock_post:
            assert brain.evacuate_vram() is True
            assert mock_post.call_args[0][1]["keep_alive"] == 0
            assert brain.warmup() is True
            assert mock_post.call_args[0][1]["options"]["num_ctx"] == gpu_opts["num_ctx"]

        with patch.object(
            brain,
            "_post",
            return_value={"message": {"content": '{"duygu":"heyecanli","mesaj":"Geldim!","aksiyon":"imlece_gel"}'}},
        ):
            for idx in range(6):
                r = brain.generate(f"selam {idx}", remember_turn=True)
                assert r["aksiyon"] in ACTIONS and r["aksiyon"] == "imlece_gel"
            assert len(brain.history) == 8  # son 4 tur (user+assistant)

        with patch.object(brain, "check_ollama", return_value=True):
            assert brain.ensure_ollama(start_if_down=False) is True

        mem.log_event("CODING", "yorum_yap", "Deneme mesaj", feedback=-1)
        mem.log_event("CODING", "yorum_yap", "Deneme mesaj 2", feedback=-1)
        added_rules = brain.run_night_reflection()
        assert len(added_rules) >= 1 and len(mem.get_unprocessed_logs()) == 0

        # Faz 1: seen_sources + DuckDuckGo web arama (mock) + anti-bot fallback + stream iptal
        assert not mem.is_source_seen("https://example.com/a", "Ornek Baslik")
        mem.mark_source_seen("https://example.com/a", "Ornek Baslik")
        assert mem.is_source_seen("https://example.com/a")
        assert mem.is_source_seen("https://baska.com", "ornek   baslik")

        ddg_html = (
            '<div class="result__body">'
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fyeni">Yeni Bulgu</a>'
            '<a class="result__snippet">Kisa ozet metni burada yer aliyor.</a>'
            '</div>'
        ).encode("utf-8")
        page_html = b"<html><body><p>Bu sayfa icerigi kirk karakterden uzun detayli teknik bir bilgi icerir.</p></body></html>"

        class _FakeResp:
            def __init__(self, body: bytes, ctype: str = "text/html"):
                self._bio = io.BytesIO(body)
                self.headers = {"Content-Type": ctype}
            def read(self, n: int = -1) -> bytes:
                return self._bio.read(n)
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                return False

        def _fake_urlopen(req, timeout=5.0):
            u = req.full_url if hasattr(req, "full_url") else str(req)
            if "duckduckgo.com" in u:
                return _FakeResp(ddg_html)
            return _FakeResp(page_html)

        with patch("brain.urllib.request.urlopen", side_effect=_fake_urlopen):
            web_res = search_open_web("python test", mem)
            assert web_res is not None and web_res["url"] == "https://example.com/yeni"
            assert "teknik bir bilgi" in web_res["snippet"]
            assert search_open_web("python test", mem) is None  # aynı URL tekrar edilmez

        with patch("brain.urllib.request.urlopen", return_value=_FakeResp(b'<div class="anomaly-modal">captcha</div>')):
            assert search_open_web("bot test", mem) is None

        with patch("brain.search_open_web", return_value=None), patch(
            "lifecycle.urllib.request.urlopen",
            return_value=_FakeResp(b'{"title":"WikiTest","extract":"Aciklama","content_urls":{"desktop":{"page":"https://tr.wikipedia.org/wiki/WikiTest"}}}', "application/json"),
        ):
            mem.reinforce_hobby("bilim", 5.0)
            _t, _snip, fb_url = lc2.explore_hobby_with_url()
            assert fb_url == "" or fb_url.startswith("https://")

        # Stream iptal testi
        ev = threading.Event()
        class _StreamResp:
            def __iter__(self):
                yield b'{"message":{"content":"parca1"},"done":false}\n'
                ev.set()
                yield b'{"message":{"content":"parca2"},"done":true}\n'
            def close(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_a):
                pass

        with patch("brain.urllib.request.urlopen", return_value=_StreamResp()):
            c_res = brain.generate("merhaba", cancel_event=ev)
            assert c_res.get("cancelled") == "1"

        assert _extract_research_query("araştır: kara delikler") == "kara delikler"
        assert _extract_research_query("bana ilginç bir şey anlat") == ""
        assert _extract_research_query("normal sohbet") is None

        # Faz 2: tam konuşma kaydı, <10dk geçmiş yükleme, gunluk.md + telafi, kök çağrışımı, kural aday havuzu
        mem.log_event("CODING", "sohbet", "Miyav, buradayım!", user_msg="kedimi besledim")
        assert len(mem.load_recent_turns(max_age_sec=600.0)) == 2
        brain_reloaded = Brain(mem)
        assert len(brain_reloaded.history) == 2

        # >10 dk eski sohbet yüklenmemeli
        mem.conn.execute("UPDATE logs SET ts = datetime('now', '-20 minutes') WHERE action = 'sohbet'")
        mem.conn.commit()
        assert mem.load_recent_turns(max_age_sec=600.0) == []

        # Türkçe 5-harf kök çağrışımı ("kedim" -> "kedimi besledim")
        recalled = mem.search_relevant_memories("kedim nerede")
        assert "kedimi" in recalled.lower()

        # Eksik geçmiş günlük telafisi + recent_diary 600 karakter sınırı
        mem.conn.execute(
            "INSERT INTO logs (ts, context, action, user_msg, response) VALUES (datetime('now', '-1 day'), 'CODING', 'sohbet', 'dün test yazdık', 'Harikaydı!')"
        )
        mem.conn.commit()
        missing_d = mem.get_missing_diary_date()
        assert missing_d is not None
        with patch.object(
            brain,
            "_post",
            return_value={"message": {"content": '{"duygu":"normal","mesaj":"Dün Kullanıcı ile harika kod yazdık.","aksiyon":"yok"}'}},
        ):
            summary = brain.write_diary(missing_d, is_gaming=False)
            assert "kod yazdık" in summary
            assert "Yaşananlar:" not in mem.read_diary() and "kod yazdık" in mem.read_diary()
            nat_fb, llm_ctx = mem.build_factual_day_digest(missing_d)
            assert "dün test yazdık" in llm_ctx and "Yaşananlar:" not in nat_fb and "kod yazarken" in nat_fb
        assert mem.get_missing_diary_date() is None

        assert _is_diary_write_cmd("günlüğüne bir şeyler yazsana") is True
        assert _is_diary_write_cmd("günlüğe yaz") is True
        assert _is_diary_open_cmd("günlüğünü aç") is True
        assert _is_diary_open_cmd("günlüğüne ne yazdın") is True

        for day_idx, d_str in enumerate(["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]):
            mem.save_diary_entry(d_str, f"Gün {day_idx} özeti " + ("uzun metin " * 25))
        assert len(mem.recent_diary(days=3, max_chars=600)) <= 600

        # Kural aday havuzu: 1. gelişte beklemede, 2. gelişte ogrendiklerim.md'ye geçer
        assert mem.add_rule_candidate("Kullanıcı sabahları filtre kahve içer.") is False
        assert "filtre kahve" not in mem.read_rules()
        assert mem.add_rule_candidate("Kullanıcı sabahları filtre kahve içer.") is True
        assert "filtre kahve" in mem.read_rules()
        mem.add_rule_candidate("Kullanıcı gece koyu tema sever.")
        promoted = mem.promote_pending_candidates(max_promote=2)
        assert any("koyu tema" in p for p in promoted)
        assert "koyu tema" in mem.read_rules()

        # Kayıt tabloları (ACTION_SPECS, MENU_ACTIONS, TRIGGER_SPECS, OVERLAY_RENDERERS, DEBUG_ANIM_SPECS) bütünlük kontrolü
        assert [s["name"] for s in ACTION_SPECS] == ACTIONS
        assert len(MENU_ACTIONS) >= 7
        assert set(TRIGGER_SPECS.keys()) >= {
            "sistem_stresi", "odak_mola", "medya_yorum", "gece_uyarisi", "yorum_yap_vision", "yorum_yap", "arastir"
        }
        assert set(OVERLAY_RENDERERS.keys()) == {"research", "writing_diary", "sweat", "yawn"}
        assert len(DEBUG_ANIM_SPECS) >= 13 and all(k in ("overlay", "pose") for _n, _l, k in DEBUG_ANIM_SPECS)

        # Refleks eşleştirme & yanlış pozitif testleri
        assert match_reflex("gel") == "imlece_gel"
        assert match_reflex("peşimden gel") == "imleci_takip_et"
        assert match_reflex("dur") == "dur"
        assert match_reflex("pencereye çık") == "pencereye_cik"
        assert match_reflex("aşağı in") == "gorev_cubuguna_in"
        assert match_reflex("uyu") == "uyu"
        assert match_reflex("uyan") == "uyan"
        assert match_reflex("zıpla") == "zipla"
        assert match_reflex("dans et") == "dans_et"
        assert match_reflex("geliştirme yapıyorum") is None
        assert match_reflex("durum nasıl?") is None
        assert match_reflex("uyuyor musun?") is None
        assert match_reflex("şimdi bana uzun bir hikaye anlat gel") is None

        # Faz 3: medya, sistem stresi, sürekli odak, gece kuşu sensör & lifecycle testleri
        assert _update_continuous_work(5.0, False, elapsed_sec=3600.0) == 60.0
        assert _update_continuous_work(5.0, False, elapsed_sec=2400.0) == 100.0
        assert _update_continuous_work(350.0, False, elapsed_sec=5.0) == 0.0
        assert _update_continuous_work(5.0, True, elapsed_sec=60.0) == 0.0
        assert check_is_late_night(3) is True and check_is_late_night(14) is False
        assert extract_media_from_window("Daft Punk - One More Time - Deezer - Google Chrome") == "Daft Punk - One More Time"
        assert extract_media_from_window("Artist - Track", "deezer.exe") == "Artist - Track"
        assert extract_media_from_window("Deezer", "deezer.exe") == ""
        with patch("sensors._query_gsmtc_playing", return_value="Madem"):
            assert detect_media_title(sync=True) == "Madem"

        lc3 = Lifecycle(mem, cooldown_sec=0.0)
        assert lc3.tick(SensorSnapshot(window_title="Code", system_stress=True, top_process="chrome.exe")) == "sistem_stresi"
        assert lc3.tick(SensorSnapshot(window_title="Code", system_stress=True, top_process="chrome.exe")) is None  # 10dk cooldown
        assert lc3.tick(SensorSnapshot(window_title="Code", continuous_work_min=95.0)) == "odak_mola"
        assert lc3.tick(SensorSnapshot(window_title="Spotify", media_title="Daft Punk - Harder")) == "medya_yorum"
        assert lc3.state == State.VIBING

        lc_night = Lifecycle(mem, cooldown_sec=0.0)
        assert lc_night.tick(SensorSnapshot(window_title="Code", is_late_night=True)) == "gece_uyarisi"
        assert abs(lc_night.drives.enerji - 99.85) < 1e-6

        # 5. Katman 1 & Orkestratör: 4 Piksel-Art Paleti, Squash&Stretch, kenar sınırı, 30s yoksayma cezası
        renderer = CatSpriteRenderer()
        for key in PALETTES:
            pm = renderer.render(key, facing_right=(key == "tuxedo"), is_perched=True, is_swatting=True)
            assert pm.width() == CAT_SIZE and pm.height() == CAT_SIZE
        pm_climb = renderer.render("white_tabby", is_climbing=True, walk_phase=1)
        pm_peek = renderer.render("white_tabby", is_peeking=True)
        pm_sleep = renderer.render("white_tabby", eye_state="closed_sleep")
        pm_normal = renderer.render("white_tabby")
        pm_research = renderer.render("white_tabby", overlay="research")
        pm_diary_1 = renderer.render("white_tabby", overlay="writing_diary", tail_phase=0.1)
        pm_diary_2 = renderer.render("white_tabby", overlay="writing_diary", tail_phase=0.8)
        pm_vibing = renderer.render("white_tabby", is_vibing=True)
        pm_sweat = renderer.render("white_tabby", overlay="sweat")
        pm_yawn = renderer.render("white_tabby", overlay="yawn")
        pm_dangle = renderer.render("white_tabby", is_dangling=True)
        assert pm_climb.width() == CAT_SIZE and pm_peek.toImage() != pm_sleep.toImage()
        assert pm_research.toImage() != pm_normal.toImage()
        assert pm_diary_1.toImage() != pm_normal.toImage() and pm_diary_1.toImage() != pm_diary_2.toImage()
        assert pm_vibing.toImage() != pm_normal.toImage()
        assert pm_sweat.toImage() != pm_normal.toImage()
        assert pm_yawn.toImage() != pm_normal.toImage()
        assert pm_dangle.height() > CAT_SIZE and pm_dangle.toImage() != pm_normal.toImage()
        jump_imgs = [
            renderer.render("white_tabby", is_crouching=True).toImage(),
            renderer.render("white_tabby", is_airborne=True, vy=-7.0).toImage(),
            renderer.render("white_tabby", is_airborne=True, vy=4.0).toImage(),
            renderer.render("white_tabby", is_landing=True).toImage(),
        ]
        assert len({tuple(im.bits().asarray(im.sizeInBytes())) for im in jump_imgs}) == 4
        assert renderer.render("white_tabby", is_hanging=True, is_airborne=True, vy=-5.0).toImage() == renderer.render("white_tabby", is_hanging=True).toImage()
        assert renderer.render("white_tabby", is_walking=True, walk_phase=1).toImage() != renderer.render("white_tabby", is_walking=False, walk_phase=1).toImage()
        assert renderer.render("white_tabby", pupil_dx=1).toImage() != pm_normal.toImage()

        ctrl = MinnakController()
        ctrl.memory = mem
        ctrl.lifecycle = lc2
        ctrl.brain = brain
        ctrl.last_ctx = "BROWSING"
        ctrl.last_action = "arastir"
        before_w = mem.get_weight("BROWSING", "arastir")
        ctrl.on_feedback(0, ignored=True)
        assert abs(mem.get_weight("BROWSING", "arastir") - (before_w - 0.10)) < 1e-6

        ctrl._llm_busy = 1
        fake_llm_snap = ctrl._filter_self_gpu_spike(
            SensorSnapshot(window_title="main.py - Code", process_name="code.exe", gpu_percent=88.0, is_gaming=True, context="GAMING")
        )
        assert not fake_llm_snap.is_gaming and fake_llm_snap.context == "CODING"
        real_game_snap = ctrl._filter_self_gpu_spike(
            SensorSnapshot(window_title="VALORANT", process_name="valorant.exe", gpu_percent=88.0, is_fullscreen=True, is_gaming=True, context="GAMING")
        )
        assert real_game_snap.is_gaming and real_game_snap.context == "GAMING"
        ctrl._llm_busy = 1
        with patch("main.read_sensors", return_value=SensorSnapshot(window_title="Code", context="CODING", system_stress=True)), patch.object(
            ctrl, "_submit_brain"
        ) as mock_sub:
            ctrl.tick()
            assert mock_sub.call_count == 0
        ctrl._llm_busy = 0

        edge = find_climb_edge(400, 600, exclude_pid=0)
        assert edge is not None and edge[3] in ("left", "right")

        win = MinnakWindow(
            ctrl.on_user_message,
            ctrl.on_feedback,
            ctrl.on_pet,
            ctrl.on_night_reflection,
            on_action=ctrl.execute_command,
            get_status=ctrl.get_status_snapshot,
            memory=mem,
            on_check_ollama=lambda: True,
            on_clear_history=brain.clear_history,
        )
        ctrl.window = win
        try:
            # Eski req_id (otonom ve kullanıcı) yanıtları güncel balonu ezmemeli
            win.show_message("güncel", "IDLE", autonomous=False)
            ctrl._req_id = 5
            ctrl._on_llm_response({"mesaj": "eski", "aksiyon": "uyu"}, "IDLE", autonomous=False, req_id=4)
            assert win.state_label != "SLEEPING" and win.msg_label.text() == "güncel"
            win.show_message("temiz", "IDLE", autonomous=False)
            ctrl._on_llm_response({"mesaj": "eski otonom"}, "IDLE", autonomous=True, req_id=4)
            assert win.msg_label.text() == "temiz"

            # Bilgi balonu ok/no gizlemeli; kapatma tuşu geri bildirim tetiklememeli
            assert win.btn_ok.isHidden() and win.btn_no.isHidden() and not win.btn_close.isHidden()
            log_fb_id = mem.log_event("CODING", "yorum_yap", "Balon mesajı", feedback=0)
            other_log_id = mem.log_event("GENERAL", "sistem_durumu", "Bilgi", feedback=0)
            ctrl.last_ctx, ctrl.last_action = "GENERAL", "sistem_durumu"
            ctrl._on_llm_response(
                {"mesaj": "oylama açık", "aksiyon": "yok", "_log_id": log_fb_id, "_ctx": "CODING", "_act": "yorum_yap"},
                "IDLE", autonomous=True, req_id=5,
            )
            assert not win.btn_ok.isHidden() and not win.btn_no.isHidden()
            win.btn_close.click()
            assert win.bubble.isHidden()
            row_fb = mem.conn.execute("SELECT feedback FROM logs WHERE id = ?", (log_fb_id,)).fetchone()[0]
            assert row_fb == 0
            ctrl.on_feedback(1)
            row_fb_after = mem.conn.execute("SELECT feedback FROM logs WHERE id = ?", (log_fb_id,)).fetchone()[0]
            row_other_after = mem.conn.execute("SELECT feedback FROM logs WHERE id = ?", (other_log_id,)).fetchone()[0]
            assert row_fb_after == 1 and row_other_after == 0

            # Link şeması kontrolü (http/https kabul, javascript/file ret)
            win.show_message("kaynak var", "READING", autonomous=True, source_url="https://example.com/haber")
            assert win._current_source_url == "https://example.com/haber" and not win.btn_link.isHidden()
            win.show_message("guvensiz", "READING", autonomous=True, source_url="javascript:alert(1)")
            assert win._current_source_url == "" and win.btn_link.isHidden()

            dpr = win.devicePixelRatioF() or 1.0
            win.perch_rect = tuple(int(round(v / dpr)) for v in (100, 200, 900, 700))
            win.update_environment(is_gaming=False)
            assert win.perch_rect is not None
            win.update_environment(is_gaming=True)
            assert win.perch_rect is None
            win.perch_rect = tuple(int(round(v / dpr)) for v in (100, 200, 900, 700))
            win._click_timer.start(200)
            assert win._click_timer.isActive()
            win._click_timer.stop()
            assert not win._click_timer.isActive()
            win.cat_cx = 400.0
            win.cat_bottom_y = 145.0
            win.vy = 18.0
            win._physics_tick()
            assert abs(win.cat_bottom_y - (200 / (win.devicePixelRatioF() or 1.0))) <= 1.0
            assert win.scale_y < 1.0 and win.scale_x > 1.0

            p1 = renderer.render("white_tabby", tail_phase=0.0, is_perched=True).toImage()
            p2 = renderer.render("white_tabby", tail_phase=1.5, is_perched=True).toImage()
            assert p1 != p2
            safe_max_x = 900 - CAT_SIZE // 2 - 12
            assert safe_max_x == 856

            # Aksiyon yürütücü + tırmanma + menü ekranı testi
            win.execute_action("uyu")
            assert win.state_label == "SLEEPING"
            win.execute_action("uyan")
            assert win.state_label == "IDLE"
            win.execute_action("zipla")
            assert win.vy < -10.0
            win.command_mode = "climb"
            win.climb_edge = (200.0, 120.0, 600.0, "left")
            win.climb_target_y = 120.0
            win.cat_cx = 200.0
            win.cat_bottom_y = 300.0
            win._physics_tick()
            assert win.cat_bottom_y < 300.0 and getattr(win, "_is_climbing", False) is True

            # Tırmanma bitişi: pencere üstüne ışınlanmadan içe zıplama
            pt = win.perch_rect[1]
            pl = win.perch_rect[0]
            win.command_mode = "climb"
            win.climb_edge = (float(pl - 22), float(pt), float(win.perch_rect[3]), "left")
            win.climb_target_y = float(pt)
            win.walk_target_x = None
            win.cat_cx = float(pl - 22)
            win.cat_bottom_y = float(pt + 2.0)
            win._physics_tick()
            assert win.vy < 0.0 and win.vx > 0.0 and win.cat_cx == float(pl - 22) and win.command_mode == "walk_to_cursor"

            win.open_menu()
            assert isinstance(win.menu_dialog, MinnakMenuDialog)
            assert win.menu_dialog.rule_list.count() >= 1
            assert win.menu_dialog.tabs.count() == 2 and len(win.menu_dialog.debug_buttons) == len(DEBUG_ANIM_SPECS)
            win.menu_dialog.close()
            win.open_diary()
            assert isinstance(win.diary_dialog, MinnakDiaryDialog)
            assert "Minnak Günlüğü" in win.diary_dialog.text_view.toPlainText()
            win.diary_dialog.close()

            # Animasyon Debug Override & Kilit Testi
            win.trigger_debug_animation("research")
            assert win.debug_locked is True and win.active_overlay == "research" and win.debug_pose is None
            win.set_overlay(None)
            win.set_state("SLEEPING")
            assert win.active_overlay == "research" and win.state_label != "SLEEPING"

            win.trigger_debug_animation("dangling")
            assert win.debug_locked is True and win.debug_pose == "dangling" and win.active_overlay is None
            win._is_airborne = True
            win._physics_tick()
            assert win.debug_pose == "dangling" and win._is_airborne is False

            win.trigger_debug_animation("reset")
            assert win.debug_locked is False and win.debug_pose is None and win.active_overlay is None and win.state_label == "IDLE"

            # writing_diary min_ticks koruması: süre bitmeden set_overlay(None) animasyonu silmemeli
            win.set_overlay("writing_diary", min_ticks=150)
            win.set_overlay(None)
            assert win.active_overlay == "writing_diary" and win.overlay_timer == 150
            win.overlay_timer = 0
            win.set_overlay(None)
            assert win.active_overlay is None

            win.execute_action("dans_et")
            assert win.state_label == "VIBING" and win.active_overlay == "vibing"
            win.overlay_timer = 0
            win.set_overlay(None)

            # Faz 4: dodge (sikilma < 20 ve merak < 20) vs pati atma (>= 20)
            win.dragging = False
            win.cat_cx, win.cat_bottom_y = 400.0, 500.0
            win.sikilma_val, win.merak_val, win._dodge_cooldown = 10.0, 10.0, 0
            win.vy = 0.0
            assert win._handle_cursor_proximity(420.0, 480.0, cursor_speed=30.0) == "dodge"
            assert win.vy < 0.0

            win.sikilma_val, win.merak_val, win._dodge_cooldown, win.swat_timer = 25.0, 10.0, 0, 0
            assert win._handle_cursor_proximity(420.0, 480.0, cursor_speed=30.0) == "paw_swipe"
            assert win.swat_timer > 0

            # Faz 4: pencereyle kayma, kapaninca dusme ve koseden sarkma (dangle)
            dpr = win.devicePixelRatioF() or 1.0
            win.perch_hwnd = 9999
            win.perch_rect = (100, 200, 900, 700)
            win.cat_cx, win.cat_bottom_y, win.vy, win.vx = 150.0, 200.0, 0.0, 0.0
            win.walk_target_x = None
            assert win._is_dangling() is True
            win.cat_cx = 500.0
            assert win._is_dangling() is False
            win.cat_cx = 150.0
            with patch("ui.get_window_rect_by_hwnd", return_value=(int(160 * dpr), int(230 * dpr), int(960 * dpr), int(730 * dpr))):
                win._track_perch_delta()
                assert win.cat_cx == 210.0 and win.cat_bottom_y == 230.0
            with patch("ui.get_window_rect_by_hwnd", return_value=None):
                win._track_perch_delta()
                assert win.perch_rect is None and win.perch_hwnd == 0
        finally:
            win.close()

        ctrl.executor.shutdown(wait=False)
        mem.conn.close()
        app.processEvents()
        print("OK: Katman 1-6 tum birim ve piksel-kedi fizigi testleri gecti.")
    finally:
        if ctrl is not None:
            ctrl.executor.shutdown(wait=False)
        if mem is not None:
            mem.conn.close()
        tmp_db.unlink(missing_ok=True)
        tmp_md.unlink(missing_ok=True)
        tmp_diary.unlink(missing_ok=True)


if __name__ == "__main__":
    test_all()
