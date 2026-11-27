"""Katman 2: Duyular (Win32 ctypes pencere/rect/AFK + NVML GPU yükü + hibrit oyun tespiti + medya/stres/odak/gece)."""
import base64
import os
import subprocess
import threading
import time
from ctypes import Structure, WINFUNCTYPE, byref, c_int, c_uint, c_void_p, create_unicode_buffer, sizeof, windll, WinDLL
from ctypes.wintypes import BOOL, HWND, LPARAM, RECT
from dataclasses import dataclass

try:
    import psutil
except ImportError:
    psutil = None  # shortcut: psutil kurulana kadar güvenli varsayılan döner

# --- Sabit Eşikler ve Anahtar Kelime Listeleri ---
AFK_THRESHOLD_SEC = 300.0
WORK_ACTIVE_AFK_SEC = 120.0
GPU_GAME_LOAD = 50.0
GPU_FULLSCREEN_GAME_LOAD = 25.0
STRESS_CPU_RAM_PERCENT = 90.0
LATE_NIGHT_START_HOUR = 2
LATE_NIGHT_END_HOUR = 6
MIN_WIN_W = 240
MIN_WIN_H = 120
GSMTC_POLL_INTERVAL_SEC = 25.0
SCREEN_CAPTURE_W = 1280
SCREEN_CAPTURE_H = 720
SCREEN_JPEG_QUALITY = 78

GAME_KEYWORDS = ("steamapps", "epic games", "riot games", "genshin", "cyberpunk", "valorant", "cs2", "dota2", "eldenring")
PRIVACY_KEYWORDS = ("bitwarden", "1password", "keepass", "bank", "inprivate", "incognito", "gizli")
CODE_KEYWORDS = ("code", "pycharm", "studio", "vim", "terminal", "powershell", "cmd")
BROWSER_KEYWORDS = ("chrome", "msedge", "firefox", "brave", "opera")
NON_GAME_APPS = (
    "explorer.exe", "code.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "devenv.exe", "pycharm64.exe", "python.exe", "powershell.exe", "windowsterminal.exe",
)
VISION_KEYWORDS = (
    "ekrana bak", "ekranı", "ekranda", "ekran", "bu ne", "hata", "hatayı",
    "görüyorsun", "görüyor musun", "bak bakalım", "şunu oku", "oku", "ne yazıyor",
)
_MEDIA_SUFFIXES = (" - YouTube", " - VLC media player", " - SoundCloud", " - Twitch", " - Spotify", " - Deezer", " | Deezer", " • Deezer")
_MEDIA_PLAYERS = ("spotify.exe", "deezer.exe", "aimp.exe", "foobar2000.exe", "music.ui.exe")
_MEDIA_IDLE_TITLES = ("Spotify", "Spotify Free", "Spotify Premium", "Deezer")
_IGNORED_PROCESSES = ("system idle process", "memory compression")


class LASTINPUTINFO(Structure):
    _fields_ = [("cbSize", c_uint), ("dwTime", c_uint)]


class NVMLUtilization(Structure):
    _fields_ = [("gpu", c_uint), ("memory", c_uint)]


_nvml_lib = None
_nvml_failed = False


def _init_nvml():
    global _nvml_lib, _nvml_failed
    if _nvml_lib is not None or _nvml_failed:
        return _nvml_lib
    for path in ("nvml.dll", r"C:\Windows\System32\nvml.dll", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll"):
        try:
            lib = WinDLL(path)
            lib.nvmlInit_v2.restype = c_uint
            lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [c_uint, c_void_p]
            lib.nvmlDeviceGetHandleByIndex_v2.restype = c_uint
            lib.nvmlDeviceGetUtilizationRates.argtypes = [c_void_p, c_void_p]
            lib.nvmlDeviceGetUtilizationRates.restype = c_uint
            if lib.nvmlInit_v2() == 0:
                _nvml_lib = lib
                return lib
        except Exception:
            continue
    _nvml_failed = True
    return None


def get_gpu_load() -> float:
    lib = _init_nvml()
    if not lib:
        return 0.0
    try:
        handle = c_void_p()
        if lib.nvmlDeviceGetHandleByIndex_v2(0, byref(handle)) != 0:
            return 0.0
        util = NVMLUtilization()
        if lib.nvmlDeviceGetUtilizationRates(handle, byref(util)) != 0:
            return 0.0
        return float(util.gpu)
    except Exception:
        return 0.0


@dataclass
class SensorSnapshot:
    window_title: str = ""
    process_name: str = ""
    pid: int = 0
    afk_seconds: float = 0.0
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    gpu_percent: float = 0.0
    is_fullscreen: bool = False
    is_gaming: bool = False
    context: str = "GENERAL"  # GENERAL | CODING | BROWSING | GAMING | AFK
    media_title: str = ""
    system_stress: bool = False
    top_process: str = ""
    continuous_work_min: float = 0.0
    is_late_night: bool = False


def get_afk_seconds() -> float:
    lii = LASTINPUTINFO(cbSize=sizeof(LASTINPUTINFO))
    if windll.user32.GetLastInputInfo(byref(lii)):
        millis = (windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
        return millis / 1000.0
    return 0.0


def _get_hwnd_rect(hwnd: int) -> tuple[int, int, int, int]:
    """DWMWA_EXTENDED_FRAME_BOUNDS (9) ile gölge kenarlığı olmadan gerçek pencere sınırlarını döner."""
    rect = RECT()
    try:
        if windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, byref(rect), sizeof(rect)) != 0:
            windll.user32.GetWindowRect(hwnd, byref(rect))
    except Exception:
        windll.user32.GetWindowRect(hwnd, byref(rect))
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def get_active_window() -> tuple[str, str, int, tuple[int, int, int, int], bool]:
    self_pid = os.getpid()
    hwnd = windll.user32.GetForegroundWindow()
    if hwnd:
        fg_pid = c_uint(0)
        windll.user32.GetWindowThreadProcessId(hwnd, byref(fg_pid))
        if fg_pid.value == self_pid:
            hwnd = _find_top_external_window(self_pid)
    else:
        hwnd = _find_top_external_window(self_pid)
    if not hwnd:
        return "", "", 0, (0, 0, 0, 0), False
    length = windll.user32.GetWindowTextLengthW(hwnd)
    buf = create_unicode_buffer(length + 1)
    windll.user32.GetWindowTextW(hwnd, buf, length + 1)

    pid = c_uint(0)
    windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
    proc_name = ""
    if psutil and pid.value:
        try:
            proc_name = psutil.Process(pid.value).name().lower()
        except Exception:
            pass

    l, t, r, b = _get_hwnd_rect(hwnd)
    screen_w = windll.user32.GetSystemMetrics(0)
    screen_h = windll.user32.GetSystemMetrics(1)
    is_full = (
        screen_w > 0
        and l <= 0
        and t <= 0
        and (r - l) >= screen_w
        and (b - t) >= screen_h
        and proc_name not in NON_GAME_APPS
    )
    return buf.value, proc_name, pid.value, (l, t, r, b), is_full


def classify_context(
    title: str,
    proc: str,
    afk: float,
    gpu_load: float = 0.0,
    is_fullscreen: bool = False,
) -> tuple[str, bool]:
    low_t, low_p = title.lower(), proc.lower()
    is_game = (
        any(k in low_p or k in low_t for k in GAME_KEYWORDS)
        or gpu_load > GPU_GAME_LOAD
        or (is_fullscreen and gpu_load > GPU_FULLSCREEN_GAME_LOAD)
    )
    if is_game:
        return "GAMING", True
    if afk > AFK_THRESHOLD_SEC:
        return "AFK", False
    if any(k in low_p or k in low_t for k in CODE_KEYWORDS):
        return "CODING", False
    if any(k in low_p for k in BROWSER_KEYWORDS):
        return "BROWSING", False
    return "GENERAL", False


_continuous_work_sec: float = 0.0
_last_work_ts: float = 0.0
_gsmtc_cache: str = ""
_gsmtc_last_poll: float = 0.0
_gsmtc_busy: bool = False
_GSMTC_PS = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Runtime.WindowsRuntime;"
    "$g=([System.WindowsRuntimeSystemExtensions].GetMethods()|?{$_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'})[0];"
    "function A($t,$r){$n=$g.MakeGenericMethod($r).Invoke($null,@($t));$n.Wait(-1)|Out-Null;$n.Result};"
    "[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]|Out-Null;"
    "$m=A ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]);"
    "foreach($s in $m.GetSessions()){"
    "if($s.GetPlaybackInfo().PlaybackStatus -eq 'Playing'){"
    "$i=A ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]);"
    "$a=($i.Artist -replace '\\s+',' ').Trim();$t=($i.Title -replace '\\s+',' ').Trim();"
    "if($a -and $t){Write-Output \"$a - $t\"}elseif($t){Write-Output $t};break}}"
)


def extract_media_from_window(title: str, proc_name: str = "") -> str:
    clean_t = (title or "").strip()
    if not clean_t:
        return ""
    for suffix in _MEDIA_SUFFIXES:
        if suffix in clean_t:
            part = clean_t.split(suffix)[0].strip()
            if part:
                return part
    if (" - " in clean_t or " • " in clean_t) and clean_t not in _MEDIA_IDLE_TITLES and proc_name.lower() in _MEDIA_PLAYERS:
        return clean_t
    return ""


def _query_gsmtc_playing() -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _GSMTC_PS],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=3.5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        return r.stdout.strip().splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:
        return ""


def _poll_gsmtc_async() -> None:
    global _gsmtc_busy
    if _gsmtc_busy:
        return
    _gsmtc_busy = True

    def _run():
        global _gsmtc_cache, _gsmtc_busy
        try:
            _gsmtc_cache = _query_gsmtc_playing()
        finally:
            _gsmtc_busy = False

    threading.Thread(target=_run, daemon=True).start()


def _has_media_or_browser_process() -> bool:
    if not psutil:
        return True
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in _MEDIA_PLAYERS or any(b in name for b in BROWSER_KEYWORDS):
                return True
        except Exception:
            continue
    return False


def detect_media_title(sync: bool = False) -> str:
    """Önce pencere başlıklarından hızlı medya taraması yapar, gerekirse GSMTC sorgular."""
    global _gsmtc_cache, _gsmtc_last_poll
    if sync:
        _gsmtc_cache = _query_gsmtc_playing()
        _gsmtc_last_poll = time.monotonic()
        if _gsmtc_cache:
            return _gsmtc_cache

    found: list[str] = []
    self_pid = os.getpid()

    def _cb(hwnd, _lp):
        if found or not windll.user32.IsWindowVisible(hwnd) or windll.user32.IsIconic(hwnd):
            return True
        length = windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        pid = c_uint(0)
        windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value == self_pid:
            return True
        buf = create_unicode_buffer(length + 1)
        windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        media = extract_media_from_window(title)
        if not media and (" - " in title or " • " in title) and psutil and pid.value and title not in _MEDIA_IDLE_TITLES:
            try:
                proc_name = psutil.Process(pid.value).name().lower()
                media = extract_media_from_window(title, proc_name)
            except Exception:
                pass
        if media:
            found.append(media)
            return False
        return True

    try:
        windll.user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception:
        pass
    if found:
        return found[0]
    if sync:
        return ""
    if not _has_media_or_browser_process():
        _gsmtc_cache = ""
        return ""
    now = time.monotonic()
    if now - _gsmtc_last_poll >= GSMTC_POLL_INTERVAL_SEC:
        _gsmtc_last_poll = now
        _poll_gsmtc_async()
    return _gsmtc_cache


def _find_top_process(include_cpu: bool = True) -> tuple[str, float]:
    """En yoğun kaynağı tüketen süreci (ad, skor/ram_yüzdesi) olarak döner."""
    if not psutil:
        return "", 0.0
    if not include_cpu:
        top_name, top_val = "", -1.0
        for proc in psutil.process_iter(["name", "memory_percent"]):
            try:
                name = proc.info.get("name") or ""
                if not name or name.lower() in _IGNORED_PROCESSES:
                    continue
                mem_p = float(proc.info.get("memory_percent") or 0.0)
                if mem_p > top_val:
                    top_val = mem_p
                    top_name = name
            except Exception:
                continue
        return top_name, max(0.0, top_val)

    procs: list[tuple[psutil.Process, str, float]] = []
    for proc in psutil.process_iter(["name", "memory_percent"]):
        try:
            name = proc.info.get("name") or ""
            if not name or name.lower() in _IGNORED_PROCESSES:
                continue
            proc.cpu_percent(None)
            mem_p = float(proc.info.get("memory_percent") or 0.0)
            procs.append((proc, name, mem_p))
        except Exception:
            continue
    time.sleep(0.1)
    top_name, top_val = "", -1.0
    for proc, name, mem_p in procs:
        try:
            cpu_p = float(proc.cpu_percent(None) or 0.0)
            val = cpu_p + mem_p * 2.0
            if val > top_val:
                top_val = val
                top_name = name
        except Exception:
            continue
    return top_name, max(0.0, top_val)


def _get_top_process_name() -> str:
    return _find_top_process(include_cpu=True)[0]


def _update_continuous_work(afk: float, is_gaming: bool, elapsed_sec: float | None = None) -> float:
    global _continuous_work_sec, _last_work_ts
    now = time.monotonic()
    if elapsed_sec is None:
        dt = 0.0 if _last_work_ts == 0.0 else min(60.0, max(0.0, now - _last_work_ts))
        _last_work_ts = now
    else:
        dt = max(0.0, elapsed_sec)
    if is_gaming or afk > AFK_THRESHOLD_SEC:
        _continuous_work_sec = 0.0
        return 0.0
    if afk < WORK_ACTIVE_AFK_SEC:
        _continuous_work_sec += dt
    return _continuous_work_sec / 60.0


def check_is_late_night(hour: int | None = None) -> bool:
    h = time.localtime().tm_hour if hour is None else hour
    return LATE_NIGHT_START_HOUR <= h < LATE_NIGHT_END_HOUR


def read_sensors() -> SensorSnapshot:
    title, proc, pid, _rect, is_full = get_active_window()
    afk = get_afk_seconds()
    cpu = psutil.cpu_percent(interval=None) if psutil else 0.0
    ram = psutil.virtual_memory().percent if psutil else 0.0
    gpu = get_gpu_load()
    ctx, gaming = classify_context(title, proc, afk, gpu, is_full)
    media = "" if gaming else detect_media_title()
    stress = bool(not gaming and (cpu > STRESS_CPU_RAM_PERCENT or ram > STRESS_CPU_RAM_PERCENT))
    top_proc = _get_top_process_name() if stress else ""
    work_min = _update_continuous_work(afk, gaming)
    late_night = check_is_late_night()
    return SensorSnapshot(
        title, proc, pid, afk, cpu, ram, gpu, is_full, gaming, ctx,
        media, stress, top_proc, work_min, late_night,
    )


def is_privacy_sensitive(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in PRIVACY_KEYWORDS)


def is_vision_prompt(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in VISION_KEYWORDS)


_WNDENUMPROC = WINFUNCTYPE(BOOL, HWND, LPARAM)


def _iter_external_windows(exclude_pid: int) -> list[tuple[int, int, int, int, int]]:
    """Z-sırasıyla görünür, gizlenmemiş >=240x120 pencere (hwnd, l, t, r, b) listesini döner."""
    wins: list[tuple[int, int, int, int, int]] = []

    def _cb(hwnd, _lp):
        if not windll.user32.IsWindowVisible(hwnd) or windll.user32.IsIconic(hwnd):
            return True
        pid = c_uint(0)
        windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value == exclude_pid:
            return True
        if windll.user32.GetWindowLongW(hwnd, -20) & 0x00000080:  # WS_EX_TOOLWINDOW
            return True
        cloaked = c_int(0)
        try:
            if windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, byref(cloaked), sizeof(cloaked)) == 0 and cloaked.value:
                return True
        except Exception:
            pass

        l, t, r, b = _get_hwnd_rect(hwnd)
        if r - l >= MIN_WIN_W and b - t >= MIN_WIN_H:
            wins.append((int(hwnd), l, t, r, b))
        return True

    windll.user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return wins


def _find_top_external_window(exclude_pid: int) -> int:
    wins = _iter_external_windows(exclude_pid)
    return wins[0][0] if wins else 0


def _enum_visible_windows(exclude_pid: int) -> list[tuple[int, int, int, int]]:
    """Z-sırasıyla görünür, gizlenmemiş >=240x120 pencere sınırlarını döner."""
    return [(l, t, r, b) for _hwnd, l, t, r, b in _iter_external_windows(exclude_pid)]


def get_window_rect_by_hwnd(hwnd: int) -> tuple[int, int, int, int] | None:
    if hwnd <= 0 or not windll.user32.IsWindow(hwnd) or not windll.user32.IsWindowVisible(hwnd) or windll.user32.IsIconic(hwnd):
        return None
    l, t, r, b = _get_hwnd_rect(hwnd)
    return (l, t, r, b) if (r - l >= MIN_WIN_W and b - t >= MIN_WIN_H) else None


def find_perch_window_with_hwnd(
    cx_phys: int, bottom_y_phys: int, exclude_pid: int, ignore_height: bool = False, ignore_x: bool = False
) -> tuple[int, int, int, int, int] | None:
    """Z-sırasına göre kedinin altındaki/önündeki en üst görünür pencereyi (hwnd, l, t, r, b) döner."""
    occluders: list[tuple[int, int, int, int]] = []
    screen_h = windll.user32.GetSystemMetrics(1)
    for hwnd, l, t, r, b in _iter_external_windows(exclude_pid):
        x_ok = True if ignore_x else ((l + 12) <= cx_phys <= (r - 12))
        y_ok = True if ignore_height else (bottom_y_phys <= t + 42)
        if 40 <= t <= screen_h - 60 and x_ok and y_ok:
            check_x = (l + r) // 2 if ignore_x else cx_phys
            if not any(ol <= check_x <= or_ and ot <= t <= ob for ol, ot, or_, ob in occluders):
                return (hwnd, l, t, r, b)
        occluders.append((l, t, r, b))
    return None


def find_climb_edge(cx_phys: int, bottom_y_phys: int, exclude_pid: int) -> tuple[int, int, int, str] | None:
    """Kediye yakın görünür pencere dikey kenarını veya ekran kenarını (edge_x, top, bottom, side) döner."""
    sw = windll.user32.GetSystemMetrics(0)
    sh = windll.user32.GetSystemMetrics(1)
    if sw <= 0 or sh <= 0:
        return None
    occluders: list[tuple[int, int, int, int]] = []
    win_edges: list[tuple[int, int, int, str]] = []
    for l, t, r, b in _enum_visible_windows(exclude_pid):
        if 40 <= t <= sh - 60:
            mid_y = (t + b) // 2
            for border_x, ex, side in ((l, l - 22, "left"), (r, r + 22, "right")):
                if 16 <= ex <= sw - 16 and b >= bottom_y_phys - 160:
                    if not any(ol < border_x < or_ and ot <= mid_y <= ob for ol, ot, or_, ob in occluders):
                        win_edges.append((ex, t, b, side))
        occluders.append((l, t, r, b))

    if win_edges:
        nearest = min(win_edges, key=lambda e: abs(e[0] - cx_phys))
        if abs(nearest[0] - cx_phys) <= 280:
            return nearest
    return (22, 40, sh - 40, "left") if cx_phys < sw // 2 else (sw - 22, 40, sh - 40, "right")


def get_system_status_summary() -> str:
    """Anlık CPU, RAM, GPU ve en yoğun süreci kısa Türkçe özet olarak döner."""
    if not psutil:
        return f"GPU: %{get_gpu_load():.0f} (psutil yok)"
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    gpu = get_gpu_load()
    top_name, top_mem = _find_top_process(include_cpu=False)
    top_name = top_name or "bilinmiyor"
    return f"CPU: %{cpu:.0f} | RAM: %{ram:.0f} | GPU: %{gpu:.0f} | En yoğun: {top_name} (%{top_mem:.1f} RAM)"


def capture_low_res_screen(title: str) -> str | None:
    """Gizlilik kontrolünden geçerse bellekte 1280x720 (KeepAspectRatio) JPEG Base64 döner."""
    if is_privacy_sensitive(title):
        return None
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            pix = screen.grabWindow(0).scaled(
                SCREEN_CAPTURE_W, SCREEN_CAPTURE_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pix.save(buf, "JPEG", SCREEN_JPEG_QUALITY)
            return base64.b64encode(bytes(ba)).decode("ascii")
    except Exception:
        pass
    return None
