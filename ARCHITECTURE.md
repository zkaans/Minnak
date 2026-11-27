# Minnak Mimari ve Geliştirici Rehberi

Minnak proje katmanları, kayıt tabloları (registry), çalışma akışları ve özellik ekleme/kaldırma kuralları burada.

## 1. Dosya ve Katman Haritası

| Dosya | Katman | Rol | Semboller |
|---|---|---|---|
| `main.py` | Orkestratör | Beş saniyelik ana döngüyü, tek işçili `ThreadPoolExecutor` kuyruğunu, otonom iptal mekanizmasını, `chat_commands` yönlendirme tablosunu, refleks eşleştirmeyi, `req_id` yarış korumasını ve GPU sıçrama filtresini yönetir. | `MinnakController`, `WorkerSignals`, `match_reflex` |
| `ui.py` | Katman 1 (Gövde & Fizik) | 30 FPS fizik motorunu, balistik zıplamayı (`_launch_ballistic_jump`), tırmanma ve pencereyle kayma davranışını, köşeden sarkmayı, süre korumalı animasyon katmanlarını, debug override motorunu (`trigger_debug_animation`), linkli konuşma balonunu ve koyu temalı tepsi/sağ tık menülerini (`_MENU_STYLE`) yönetir. | `MinnakWindow`, `_perch_safe_bounds` |
| `sprites.py` | Katman 1a (Görsel & Palet) | `24x24` -> `64x64` piksel kedi çizimini, 4 renk paletini, `OVERLAY_RENDERERS` animasyon katmanı tablosunu ve `DEBUG_ANIM_SPECS` hata ayıklama animasyon listesini yönetir. | `CatSpriteRenderer`, `PALETTES`, `PALETTE_NAMES`, `OVERLAY_RENDERERS`, `DEBUG_ANIM_SPECS`, `CAT_SIZE`, `DANGLE_PAD` |
| `dialogs.py` | Katman 1b (Yan Pencereler) | Canlı durum kartı (`QProgressBar` ızgarası), `🎮 Kontrol & Hafıza` ve `🛠 Animasyon & Debug` sekmelerinden oluşan Kontrol Merkezi (`MinnakMenuDialog`) ile Günlük Okuyucu (`MinnakDiaryDialog`) pencerelerini yönetir. | `MinnakMenuDialog`, `MinnakDiaryDialog` |
| `sensors.py` | Katman 2 (Duyular) | Tekilleştirilmiş Win32 DWM pencere/rect (`_get_hwnd_rect`) ve `hwnd` takibini, AFK süresini, NVML GPU yükünü, Windows GSMTC ve pencere başlıklarıyla medya algılamayı, süreç taramasını (`_find_top_process`), odak süresini, gece saatini ve `1280x720` ekran görüntüsünü okur. | `SensorSnapshot`, `read_sensors`, `detect_media_title`, `find_perch_window_with_hwnd`, `capture_low_res_screen` |
| `lifecycle.py` | Katman 3 (Yaşam) | Sıfır LLM maliyetli durum makinesini, dürtü hesaplamalarını, tek merkezli `TRIGGER_SPECS` otonom tetikleyici tablosunu (`cooldowns` sözlüğü ile) ve açık web destekli hobi keşfini yönetir. | `State`, `Drives`, `Lifecycle`, `TRIGGER_SPECS`, `OFFLINE_HOBBY_SNIPPETS` |
| `brain.py` | Katman 4 & 5 (Beyin) | Tek merkezli `ACTION_SPECS` aksiyon tablosunu (`ACTIONS`, `REFLEX_PATTERNS`, `MENU_ACTIONS`, `ACTIVE_ACTIONS_PROMPT`), olay bazlı Ollama (`gemma4:e4b`) çağrılarını, stream tabanlı iptali, DuckDuckGo açık web aramasını, `hatirla_kural` çıkarımını, doğal günlük özetlemeyi ve gece yansımasını yürütür. | `Brain`, `ACTION_SPECS`, `ACTIONS`, `REFLEX_PATTERNS`, `MENU_ACTIONS`, `RESPONSE_SCHEMA`, `search_open_web` |
| `memory.py` | Katman 6 (Hafıza) | SQLite veritabanını (`minnak.db`), tam konuşma loglarını, görülmüş kaynakları, kural aday havuzunu, `ogrendiklerim.md` kurallarını, `gunluk.md` kayıtlarını ve 5-harf kök çağrışım aramasını yönetir. | `Memory` |
| `test_core.py` | Test | Katman 1-6 birim testlerini, kayıt tablolarını (`ACTION_SPECS`, `TRIGGER_SPECS`, `OVERLAY_RENDERERS`), refleksleri, DuckDuckGo ve GSMTC mock doğrulamalarını, günlük/çağrışım testlerini ve fizik/kayma/sarkma/pati kontrollerini çalıştırır. | `test_all` |

## 2. Çalışma Akışları

### 2.1 Başlatma ve Ollama Dayanıklılığı
- Uygulamayı `python main.py` komutuyla çalıştır; konsolsuz başlatmak için `pythonw.exe main.py` kullan.
- `main()` içinde `MinnakController` ve `MinnakWindow` bağla, `ctrl.start_background_services()` arka plan servislerini başlat.
- `brain.ensure_ollama(start_if_down=True)` `http://localhost:11434/api/tags` adresini kontrol eder; servis kapalıysa `%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve` sürecini `CREATE_NO_WINDOW` bayrağıyla başlatır.
- Oyun açık değilse `brain.warmup()` çalıştır; işlem boyunca `_llm_busy += 1` tut ve bitişte `INIT_COOLDOWN_SEC = 5.0` saniye bekle, böylece VRAM yüklemesi oyun sanılmaz.
- Açılışta `get_missing_diary_date()` geçmişte kayıt bulup `gunluk.md` özeti yoksa ve oyun modu yoksa eksik günlüğü otomatik tamamlar.

### 2.2 5 Saniyelik Sensör ve Yaşam Döngüsü (`ctrl.tick`)
1. `read_sensors()` aktif pencereyi (`_get_hwnd_rect` ile DWM gerçek sınırlarını), AFK süresini, CPU/RAM kullanımını, NVML GPU yükünü, medyayı, oyun dışı stresi, odak dakikasını ve gece saatini (`02:00 - 06:00`) okur; `classify_context` ile bağlamı seçer (`GAMING`, `AFK`, `CODING`, `BROWSING`, `GENERAL`).
2. `_filter_self_gpu_spike()` LLM çalışırken (`_llm_busy > 0` veya `4.0` sn beklemede) `gpu_load > 50.0` olursa GPU yükünü sıfır sayıp yeniden sınıflandırarak sahte oyunu engeller.
3. Gerçek `GAMING` olunca `brain.evacuate_vram()`, `BELOW_NORMAL_PRIORITY_CLASS` ve `Ghost Mode` (`opacity=0.35`, `WS_EX_TRANSPARENT`, köşe kilidi) açılır; çıkınca eski haline döner.
4. `lifecycle.tick(snap)` enerji (normal `-0.10`, gece `-0.15`), merak ve sıkılmayı günceller; `TRIGGER_SPECS` tablosundaki kuralları ve `self.cooldowns` sözlüğünü kontrol ederek tetiklenen eylem anahtarını döner.
5. `ctrl.tick()` dönen anahtarı `TRIGGER_SPECS[action]` üzerinden okur; varsa `overlay` katmanını açar, `use_vision` ise `1280x720` ekran görüntüsü alır, `is_hobby` ise açık web hobi gezginini başlatır veya `spec["prompt"](snap)` şablonuyla LLM'e gönderir.

### 2.3 Hibrit Komut, Refleks ve Sohbet Akışı (`on_user_message`)
1. Kullanıcı mesaj atınca `_cancel_autonomous()` bekleyen otonom işi ve aktif stream akışını `cancel_event` ile hemen keser.
2. Mesaj `"bunu hatırla:"` veya `"hatırla:"` ile başlıyorsa `memory.append_rule()` doğrudan `ogrendiklerim.md` dosyasına yazar.
3. Mesaj `self.chat_commands` tablosundaki işleyicilerden (`_cmd_diary_open`, `_cmd_diary_write`, `_cmd_system_status`, `_cmd_research`) birine uyarsa doğrudan çalıştırılır.
4. Normal mesajlarda `_req_id` artar ve balonda `"..."` gösterilir. `match_reflex(text)` soru değilse ve `<= 4` kelimeyse `ACTION_SPECS` tablosundan üretilen `REFLEX_PATTERNS` üzerinden `execute_command()` çağrısını anında çalıştırır.
5. Görsel istekse (`is_vision_prompt`) `capture_low_res_screen()` ile `1280x720` ekran JPEG görüntüsü eklenir.
6. Arka planda `brain.generate(..., remember_turn=True)` son 4 turu, son 3 günün günlük özetini (`<=600` karakter) ve 5-harf kök çağrışımıyla bulunan eski anıları (`<=3` kayıt) ekleyerek yanıt üretir. Kalıcı tercih varsa `hatirla_kural` alanı `rule_candidates` havuzuna yazılır (2 tekrarda kalıcı kural olur).

### 2.4 30 FPS Fizik, Tırmanma ve Etkileşim (`ui.py`, `sprites.py`, `dialogs.py`)
- **Koordinat ve Yüzey Takibi:** `cat_cx` ve `cat_bottom_y` piksel tutulur; pencere sınırları `devicePixelRatioF()` ile ölçeklenir ve `DANGLE_PAD = 16` alt boşluk patileri kesmez. Yerçekimi `GRAVITY = 1.15 px/tick²`. Balistik zıplamalar `_launch_ballistic_jump()` ile hesaplanır. `_track_perch_delta()` ile pencere taşınırsa kedi kayar, kapanırsa yere düşer.
- **Köşeden Sarkma ve İmleç Tepkisi:** Kedi tünek sol/sağ köşeye `64 px` yakın dururken `is_dangling=True` (`24x28` grid) ile patileri ve kuyruğu sarkıtır. İmleç `48 px` yaklaşınca `sikilma < 20` ve `merak < 20` ise zıplayarak kaçar (`dodge`), aksi halde pati atar (`paw_swipe`).
- **Tırmanma, Overlay Katmanı ve Debug Modu:** Yükseklik `155 px` üstündeyse `find_climb_edge` ile dikey kenara `±90°` tırmanır. `OVERLAY_RENDERERS` içindeki katmanlar (`research`, `writing_diary`, `sweat`, `yawn`) ve `vibing` durumu `overlay_timer` ile korunur. Kontrol Merkezi (`Animasyon & Debug` sekmesi) veya sağ tık `🛠 Animasyon Testi` alt menüsünden çağrılan `trigger_debug_animation(key)` (`DEBUG_ANIM_SPECS`) seçilen katmanı veya fizik pozunu (`debug_locked`/`debug_pose`) sıfırlama ya da yeni hareket komutuna kadar kilitli tutar.

### 2.5 Hafıza, Günlük ve Gece Yansıması
- `👍` (`+0.20`), `✕` (`-0.25`) ve 30 saniye yoksayma (`-0.10`) geri bildirimleri `action_weights`, `hobbies` (`0.1..3.0`) ve `samimiyet` (`10..95`) değerlerini günceller.
- `write_diary()` `build_factual_day_digest()` ile gerçek sohbetleri ve araştırmaları `gunluk.md` dosyasına doğal dille kaydeder.
- `run_night_reflection()` son 25 işlenmemiş logdan en fazla 3 kural çıkarır, bekleyen `rule_candidates` adaylarını terfi ettirir, eksik günlüğü tamamlar ve `ogrendiklerim.md` `>20` satır olunca `<=15` satıra sıkıştırır.

## 3. Özellik Ekleme, Güncelleme ve Kaldırma Kılavuzu

- **Yeni kedi aksiyonu eklemek veya kaldırmak (Tek Tablo + Fizik):**
  1. `brain.py`: `ACTION_SPECS` listesine `{"name": "...", "reflex": r"...", "menu": "...", "ctx_menu": True/False}` satırını ekle (veya kaldır). `ACTIONS`, `RESPONSE_SCHEMA`, `sys_prompt`, `REFLEX_PATTERNS`, `MinnakMenuDialog` butonları ve sağ tık menüsü bu tablodan otomatik güncellenir.
  2. `ui.py`: Fiziksel bir hareketse `MinnakWindow.execute_action()` içine dalını ekle (veya kaldır).
- **Yeni sohbet komutu eklemek veya kaldırmak:**
  1. `main.py`: `MinnakController._cmd_<ad>` metodunu yaz ve `self.chat_commands` listesine `("<ad>", self._cmd_<ad>)` olarak ekle (kaldırmak için listeden sil).
- **Yeni otonom tetikleyici (yaşam döngüsü kuralı) eklemek veya kaldırmak:**
  1. `lifecycle.py`: `TRIGGER_SPECS` sözlüğüne tetikleyicinin `cooldown`, `global_cd`, `state`, `overlay`, `overlay_ticks` ve `prompt` tanımını ekle ve `Lifecycle.tick()` içinde `self._can_fire(...)` / `self._fire(...)` koşulunu bağla. `main.py` içinde hiçbir değişiklik gerekmez.
- **Yeni piksel animasyon katmanı (overlay) eklemek veya kaldırmak:**
  1. `sprites.py`: `_overlay_<ad>(img, pal, tail_phase, put)` çizim fonksiyonunu yaz ve `OVERLAY_RENDERERS` sözlüğüne ekle (kaldırmak için sözlükten sil).
- **Yeni sensör veya eşik değiştirmek:**
  1. `sensors.py`, `lifecycle.py`, `ui.py`, `main.py` ve `brain.py` dosyalarının en üstündeki sabit bloklarından ilgili eşiği veya anahtar kelime listesini güncelle.
- **Her değişiklik sonrası doğrulama:** `python test_core.py` komutunu çalıştır.
