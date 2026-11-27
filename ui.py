"""Katman 1: Gövde (Ana Pencere, 30 FPS Fizik Motoru, Ghost Mode, Alt+M Kısayolu ve Tepsi)."""
import math
import os
import random
import time
from ctypes import c_int, c_long, c_size_t, c_void_p, sizeof, windll
from ctypes.wintypes import HWND, MSG, UINT
from PyQt6.QtCore import QPoint, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QActionGroup, QColor, QCursor, QDesktopServices, QFont, QIcon, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from brain import MENU_ACTIONS
from dialogs import MinnakDiaryDialog, MinnakMenuDialog
from sensors import find_climb_edge, find_perch_window_with_hwnd, get_window_rect_by_hwnd
from sprites import BASE_GRID, CAT_SIZE, CatSpriteRenderer, DANGLE_PAD, DEBUG_ANIM_SPECS, PALETTE_NAMES, PALETTES

_MENU_STYLE = (
    "QMenu { background-color: #14161c; color: #f0f0f5; border: 1px solid #30364a; "
    "border-radius: 8px; padding: 5px; font-family: 'Segoe UI'; font-size: 9pt; }"
    "QMenu::item { padding: 6px 20px 6px 10px; border-radius: 5px; margin: 1px 0; }"
    "QMenu::item:selected { background-color: #252b3d; color: #ffffff; }"
    "QMenu::separator { height: 1px; background: #262b38; margin: 4px 8px; }"
)

# --- Pencere, Fizik ve Etkileşim Sabitleri ---
WIN_W = 240
WIN_H = 196 + DANGLE_PAD
PHYSICS_INTERVAL_MS = 33
BUBBLE_IGNORE_MS = 30000
GRAVITY = 1.15
MAX_JUMP_DY = 155.0
DANGLE_MARGIN = 64.0
CURSOR_PROXIMITY_PX = 48.0
PERCH_SAFE_PAD = CAT_SIZE // 2 + 12  # 44 px
PERCH_WALK_PAD = 22
CLIMB_EDGE_OFFSET = 22
CLIMB_STEP_PX = 3.2
WALK_SPEED_CMD = 3.8
WALK_SPEED_IDLE = 2.4
FOLLOW_DURATION_SEC = 30.0
GHOST_OPACITY = 0.35

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
MOD_ALT = 0x0001
WM_HOTKEY = 0x0312

if sizeof(c_void_p) == 8:
    _GetWinLong = windll.user32.GetWindowLongPtrW
    _SetWinLong = windll.user32.SetWindowLongPtrW
    _LONG_PTR = c_size_t
else:
    _GetWinLong = windll.user32.GetWindowLongW
    _SetWinLong = windll.user32.SetWindowLongW
    _LONG_PTR = c_long

_GetWinLong.argtypes = [HWND, c_int]
_GetWinLong.restype = _LONG_PTR
_SetWinLong.argtypes = [HWND, c_int, _LONG_PTR]
_SetWinLong.restype = _LONG_PTR
windll.user32.RegisterHotKey.argtypes = [HWND, c_int, UINT, UINT]
windll.user32.UnregisterHotKey.argtypes = [HWND, c_int]


def _perch_safe_bounds(rect: tuple[int, int, int, int], pad: int = PERCH_SAFE_PAD) -> tuple[int, int]:
    """Tünek penceresi üstünde güvenli (sol_min, sağ_max) X aralığını döner."""
    left, _top, right, _bottom = rect
    safe_min = left + pad
    return safe_min, max(safe_min, right - pad)


class MinnakWindow(QWidget):
    def __init__(
        self,
        on_user_message,
        on_feedback,
        on_pet=None,
        on_night_reflection=None,
        on_action=None,
        get_status=None,
        memory=None,
        on_check_ollama=None,
        on_clear_history=None,
        on_write_diary=None,
    ):
        super().__init__()
        self.setWindowTitle("Minnak")
        self.on_user_message = on_user_message
        self.on_feedback = on_feedback
        self.on_pet = on_pet or (lambda: None)
        self.on_night_reflection = on_night_reflection or (lambda: None)
        self.on_action = on_action
        self.get_status = get_status
        self.memory = memory
        self.on_check_ollama = on_check_ollama
        self.on_clear_history = on_clear_history
        self.on_write_diary = on_write_diary or (lambda: None)

        self.renderer = CatSpriteRenderer()
        self.palette_key = "white_tabby"
        self.state_label = "IDLE"
        self.pinned = False
        self.is_ghost = False
        self.enerji_val = 100.0
        self.sikilma_val = 0.0
        self.merak_val = 0.0

        avail = QApplication.primaryScreen().availableGeometry()
        self.cat_cx = float(avail.right() - 72)
        self.cat_bottom_y = float(avail.bottom() + 1)
        self.vx = 0.0
        self.vy = 0.0
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.drag_angle = 0.0
        self.facing_right = False
        self.perch_rect: tuple[int, int, int, int] | None = None
        self.perch_hwnd: int = 0
        self._perch_tick = 0
        self.walk_target_x: float | None = None
        self.walk_phase = 0
        self.tail_phase = 0.0
        self.swat_timer = 0
        self.pet_timer = 0
        self.hearts: list[list[float]] = []

        self.command_mode: str | None = None
        self.follow_until: float = 0.0
        self.climb_edge: tuple[float, float, float, str] | None = None
        self.climb_target_y: float | None = None
        self.peek_timer: int = 0
        self.peek_side: str | None = None
        self.menu_dialog: MinnakMenuDialog | None = None
        self.diary_dialog: MinnakDiaryDialog | None = None

        self.dragging = False
        self._press_pos = QPoint()
        self._last_drag_global = QPoint()
        self._drag_vx = 0.0
        self._drag_vy = 0.0
        self._last_cursor = QCursor.pos()
        self._cursor_idle_frames = 0
        self._dodge_cooldown = 0
        self._pet_accumulator = 0.0
        self._last_hover_x = 0.0
        self._autonomous_bubble = False
        self.active_overlay: str | None = None
        self.overlay_timer: int = 0
        self.debug_pose: str | None = None
        self.debug_locked: bool = False
        self._current_source_url: str = ""

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFixedSize(WIN_W, WIN_H)

        self._build_ui()
        self._build_tray()

        self._ignore_timer = QTimer(self)
        self._ignore_timer.setSingleShot(True)
        self._ignore_timer.timeout.connect(self._on_bubble_ignored)

        self._phys_timer = QTimer(self)
        self._phys_timer.timeout.connect(self._physics_tick)
        self._phys_timer.start(PHYSICS_INTERVAL_MS)

        windll.user32.RegisterHotKey(int(self.winId()), 1, MOD_ALT, ord("M"))
        self._sync_window_pos()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, CAT_SIZE + 6 + DANGLE_PAD)
        layout.setSpacing(4)
        layout.addStretch()

        self.bubble = QWidget(self)
        self.bubble.setStyleSheet("background: rgba(20,22,28,225); border-radius: 8px; color: #f0f0f5;")
        b_layout = QVBoxLayout(self.bubble)
        b_layout.setContentsMargins(8, 6, 8, 6)
        self.msg_label = QLabel("Hazır.")
        self.msg_label.setWordWrap(True)
        self.msg_label.setFont(QFont("Segoe UI", 9))

        btn_row = QHBoxLayout()
        self.btn_link = QPushButton("🔗 Kaynağı Aç")
        self.btn_link.setStyleSheet("font-size: 8pt; padding: 2px 6px;")
        self.btn_link.clicked.connect(self._open_source_url)
        self.btn_link.hide()
        self.btn_ok = QPushButton("👍")
        self.btn_no = QPushButton("✕")
        self.btn_ok.setFixedSize(26, 22)
        self.btn_no.setFixedSize(26, 22)
        self.btn_ok.clicked.connect(lambda: self._handle_feedback(1))
        self.btn_no.clicked.connect(lambda: self._handle_feedback(-1))
        btn_row.addWidget(self.btn_link)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_ok)
        btn_row.addWidget(self.btn_no)

        b_layout.addWidget(self.msg_label)
        b_layout.addLayout(btn_row)
        self.bubble.hide()
        layout.addWidget(self.bubble)

        self.input_box = QLineEdit(self)
        self.input_box.setPlaceholderText("Minnak'a yaz (Alt+M)...")
        self.input_box.setStyleSheet("background: rgba(20,22,28,215); color: #fff; border-radius: 6px; padding: 4px;")
        self.input_box.returnPressed.connect(self._send_input)
        self.input_box.hide()
        layout.addWidget(self.input_box)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(QIcon(self.renderer.render(self.palette_key)))
        menu = QMenu()
        menu.setStyleSheet(_MENU_STYLE)

        menu.addAction("🎛 Kontrol Merkezi", self.open_menu)
        menu.addAction("📖 Günlüğü Oku", self.open_diary)
        menu.addSeparator()

        pal_menu = menu.addMenu("🎨 Kedi Görünümü")
        grp = QActionGroup(self)
        for key, label in PALETTE_NAMES.items():
            act = QAction(label, self, checkable=True, checked=(key == self.palette_key))
            act.triggered.connect(lambda _c, k=key: self.set_palette(k))
            grp.addAction(act)
            pal_menu.addAction(act)

        self.pin_action = QAction("📌 Sabitle (Pin)", self, checkable=True)
        self.pin_action.triggered.connect(self.toggle_pin)
        menu.addAction(self.pin_action)

        menu.addAction("💬 Sohbet Kutusu (Alt+M)", self.toggle_chat_input)
        menu.addAction("🌙 Gece Yansıması Çalıştır", lambda: self.on_night_reflection())
        menu.addSeparator()
        menu.addAction("🚪 Çıkış", QApplication.quit)

        self.tray.setContextMenu(menu)
        self.tray.show()

    def open_menu(self) -> None:
        if self.menu_dialog is None:
            self.menu_dialog = MinnakMenuDialog(
                self,
                memory=self.memory,
                get_status=self.get_status,
                on_check_ollama=self.on_check_ollama,
                on_clear_history=self.on_clear_history,
            )
        self.menu_dialog.show()
        self.menu_dialog.raise_()
        self.menu_dialog.activateWindow()

    def open_diary(self) -> None:
        if self.diary_dialog is None:
            self.diary_dialog = MinnakDiaryDialog(
                self, memory=self.memory, on_write_diary=self.on_write_diary
            )
        self.diary_dialog.show()
        self.diary_dialog.raise_()
        self.diary_dialog.activateWindow()

    def _run_action(self, action: str) -> None:
        if self.on_action:
            self.on_action(action)
        else:
            self.execute_action(action)

    def set_state(self, state: str) -> None:
        if self.debug_locked:
            return
        if self.command_mode != "peek":
            self.state_label = state

    def set_overlay(self, overlay: str | None, min_ticks: int = 0) -> None:
        if self.debug_locked:
            return
        if overlay is not None:
            self.active_overlay = overlay
            self.overlay_timer = max(self.overlay_timer, min_ticks)
            if overlay == "writing_diary":
                self.walk_target_x = None
                self.vx = 0.0
        else:
            if self.overlay_timer > 0:
                return
            self.active_overlay = None
        self.update()

    def trigger_debug_animation(self, key: str | None) -> None:
        """Hata ayıklama amacıyla belirli bir overlay katmanını veya fizik pozunu zorlar/sıfırlar."""
        if key is None or key == "reset":
            self.debug_locked = False
            self.debug_pose = None
            self.overlay_timer = 0
            self.active_overlay = None
            self.swat_timer = 0
            self.pet_timer = 0
            self.state_label = "IDLE"
        else:
            kind = next((k for name, _lbl, k in DEBUG_ANIM_SPECS if name == key), None)
            if kind == "overlay":
                self.debug_locked = True
                self.debug_pose = None
                self.active_overlay = key
                self.overlay_timer = 999999
                self.walk_target_x = None
                self.vx = 0.0
                self.vy = 0.0
                self.state_label = "VIBING" if key == "vibing" else "IDLE"
            elif key == "dodge":
                self.debug_locked = False
                self.debug_pose = None
                self.overlay_timer = 0
                self.active_overlay = None
                self.state_label = "IDLE"
                self.vy = -9.5
                self.vx = -7.0 if self.facing_right else 7.0
                self.facing_right = self.vx > 0
            elif kind == "pose":
                self.debug_locked = True
                self.debug_pose = key
                self.active_overlay = None
                self.overlay_timer = 0
                self.walk_target_x = None
                self.vx = 0.0
                self.vy = 0.0
                if key == "pet_hearts":
                    self.hearts.append([0.0, -4.0, -1.3, 1.0])
                    self.state_label = "IDLE"
                elif key == "sleep_curl":
                    self.state_label = "SLEEPING"
                else:
                    self.state_label = "IDLE"
        self.update()
        if self.menu_dialog and self.menu_dialog.isVisible():
            self.menu_dialog.refresh()

    def _open_source_url(self) -> None:
        if self._current_source_url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(self._current_source_url))

    def show_thinking(self) -> None:
        self.msg_label.setText("...")
        self._current_source_url = ""
        self.btn_link.hide()
        self.btn_ok.hide()
        self.btn_no.hide()
        self._ignore_timer.stop()
        self._autonomous_bubble = False
        self.bubble.show()
        self.update()

    def set_palette(self, key: str) -> None:
        self.palette_key = key
        self.tray.setIcon(QIcon(self.renderer.render(key)))
        if self.menu_dialog and self.menu_dialog.isVisible():
            idx = self.menu_dialog.pal_combo.findData(key)
            if idx >= 0:
                self.menu_dialog.pal_combo.blockSignals(True)
                self.menu_dialog.pal_combo.setCurrentIndex(idx)
                self.menu_dialog.pal_combo.blockSignals(False)
        self.update()

    def toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.pin_action.setChecked(self.pinned)

    def toggle_chat_input(self) -> None:
        show = not self.input_box.isVisible()
        self.input_box.setVisible(show)
        if show:
            if self.is_ghost:
                self._apply_win32_transparent(False)
            self.activateWindow()
            self.input_box.setFocus()
        elif self.is_ghost:
            self._apply_win32_transparent(True)

    def _apply_win32_transparent(self, enable: bool) -> None:
        hwnd = int(self.winId())
        style = _GetWinLong(hwnd, GWL_EXSTYLE)
        new_style = (style | WS_EX_LAYERED | WS_EX_TRANSPARENT) if enable else ((style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT)
        _SetWinLong(hwnd, GWL_EXSTYLE, new_style)

    def set_ghost_mode(self, enabled: bool) -> None:
        self.is_ghost = enabled
        self.setWindowOpacity(GHOST_OPACITY if enabled else 1.0)
        self._apply_win32_transparent(enabled)
        if enabled:
            self.input_box.hide()
            self.command_mode = None
            self.climb_edge = None
            self.peek_timer = 0
            avail = QApplication.primaryScreen().availableGeometry()
            self.cat_cx = float(avail.right() - 48)
            self.cat_bottom_y = float(avail.bottom() + 1)
            self.vx = self.vy = 0.0
            self._sync_window_pos()

    def update_environment(
        self,
        window_rect: tuple[int, int, int, int] | None,
        is_gaming: bool,
        same_pid: bool = False,
        enerji: float = 100.0,
        sikilma: float = 0.0,
        merak: float = 0.0,
    ) -> None:
        self.enerji_val = enerji
        self.sikilma_val = sikilma
        self.merak_val = merak
        if is_gaming:
            self.perch_rect = None
            self.perch_hwnd = 0
            return
        if same_pid or window_rect is None:
            return
        dpr = self.devicePixelRatioF() or 1.0
        left, top, right, bottom = (int(round(v / dpr)) for v in window_rect)
        self.perch_rect = (left, top, right, bottom) if (right - left >= 240 and bottom - top >= 120) else None

    def show_message(
        self, text: str, state: str = "IDLE", autonomous: bool = False, source_url: str = ""
    ) -> None:
        self.btn_ok.show()
        self.btn_no.show()
        clean_url = (source_url or "").strip()
        self._current_source_url = clean_url if clean_url.startswith(("http://", "https://")) else ""
        self.btn_link.setVisible(bool(self._current_source_url))
        self.state_label = state
        self.msg_label.setText(text)
        self.bubble.show()
        self._autonomous_bubble = autonomous
        self._ignore_timer.stop()
        if autonomous:
            self._ignore_timer.start(BUBBLE_IGNORE_MS)
        self.update()

    def _on_bubble_ignored(self) -> None:
        self.bubble.hide()
        if self._autonomous_bubble:
            self._autonomous_bubble = False
            self.on_feedback(0, ignored=True)

    def _handle_feedback(self, score: int) -> None:
        self._ignore_timer.stop()
        self._autonomous_bubble = False
        if score < 0:
            self.bubble.hide()
        self.on_feedback(score, ignored=False)

    def _send_input(self) -> None:
        text = self.input_box.text().strip()
        self._ignore_timer.stop()
        self._autonomous_bubble = False
        if text:
            self.input_box.clear()
            self.on_user_message(text)
        if self.is_ghost:
            self.input_box.hide()
            self._apply_win32_transparent(True)

    def _sync_window_pos(self) -> None:
        self.move(int(self.cat_cx - WIN_W // 2), int(self.cat_bottom_y - WIN_H + DANGLE_PAD))

    def _launch_ballistic_jump(
        self, target_x: float, dy: float, extra_vy: float = 1.5, max_vx: float = 10.0
    ) -> None:
        """Hedef X ve yükseklik farkı (dy) için balistik zıplama hızlarını (vx, vy) hesaplar."""
        self.vy = -math.sqrt(2.0 * GRAVITY * max(12.0, dy)) - extra_vy
        flight = max(10.0, 2.0 * abs(self.vy) / GRAVITY)
        self.vx = max(-max_vx, min(max_vx, (target_x - self.cat_cx) / flight))
        self.facing_right = target_x >= self.cat_cx

    def _refresh_perch(self, ignore_height: bool = False, ignore_x: bool = False) -> tuple[int, int, int, int] | None:
        if self.is_ghost:
            self.perch_rect = None
            self.perch_hwnd = 0
            return None
        dpr = self.devicePixelRatioF() or 1.0
        raw = find_perch_window_with_hwnd(
            int(self.cat_cx * dpr), int(self.cat_bottom_y * dpr), os.getpid(),
            ignore_height=ignore_height, ignore_x=ignore_x,
        )
        if raw:
            self.perch_hwnd = raw[0]
            self.perch_rect = tuple(int(round(v / dpr)) for v in raw[1:])
        else:
            self.perch_hwnd = 0
            self.perch_rect = None
        return self.perch_rect

    def _track_perch_delta(self) -> None:
        if not self.perch_rect or self.perch_hwnd <= 0:
            return
        if abs(self.cat_bottom_y - self.perch_rect[1]) > 6.0 or self.vy < 0.0:
            return
        raw = get_window_rect_by_hwnd(self.perch_hwnd)
        if not raw:
            self.perch_rect = None
            self.perch_hwnd = 0
            return
        dpr = self.devicePixelRatioF() or 1.0
        new_rect = tuple(int(round(v / dpr)) for v in raw)
        dx = new_rect[0] - self.perch_rect[0]
        self.perch_rect = new_rect
        self.cat_cx += dx
        self.cat_bottom_y = float(new_rect[1])
        if self.walk_target_x is not None:
            self.walk_target_x += dx

    def _is_dangling(self) -> bool:
        if not self.perch_rect or self.dragging or (self.command_mode == "climb" and getattr(self, "_is_climbing", False)):
            return False
        if abs(self.cat_bottom_y - self.perch_rect[1]) > 6.0:
            return False
        if self.walk_target_x is not None or abs(self.vx) >= 0.5:
            return False
        l, _t, r, _b = self.perch_rect
        return self.cat_cx <= l + DANGLE_MARGIN or self.cat_cx >= r - DANGLE_MARGIN

    def _handle_cursor_proximity(self, cur_x: float, cur_y: float, cursor_speed: float = 30.0) -> str | None:
        if self.dragging or self._dodge_cooldown > 0:
            return None
        dist_cursor = math.hypot(cur_x - self.cat_cx, cur_y - (self.cat_bottom_y - 32))
        if dist_cursor >= CURSOR_PROXIMITY_PX:
            return None
        if self.sikilma_val < 20.0 and self.merak_val < 20.0:
            if cursor_speed > 10.0:
                self.vy = -9.5
                self.vx = -7.0 if cur_x > self.cat_cx else 7.0
                self.facing_right = self.vx > 0
                self._dodge_cooldown = 45
                return "dodge"
            return None
        self.facing_right = cur_x >= self.cat_cx
        self.swat_timer = 18
        self._dodge_cooldown = 35
        self.walk_target_x = None
        return "paw_swipe"

    def _refresh_climb_edge(self) -> tuple[float, float, float, str] | None:
        dpr = self.devicePixelRatioF() or 1.0
        raw = find_climb_edge(int(self.cat_cx * dpr), int(self.cat_bottom_y * dpr), os.getpid())
        if not raw:
            return None
        return (raw[0] / dpr, raw[1] / dpr, raw[2] / dpr, raw[3])

    def _resolve_climb_facing(self, edge_x: float, side: str, avail) -> bool:
        if edge_x <= avail.left() + 45:
            return False
        if edge_x >= avail.right() - 45:
            return True
        return side == "left"

    def execute_action(self, action: str) -> None:
        if self.debug_locked or self.debug_pose is not None:
            self.debug_locked = False
            self.debug_pose = None
            if self.overlay_timer >= 999000:
                self.overlay_timer = 0
                self.active_overlay = None
        if action == "dur":
            self.command_mode = None
            self.walk_target_x = None
            self.climb_edge = None
            self.climb_target_y = None
            self.peek_timer = 0
            self.vx = 0.0
            return
        if action == "uyu":
            self.command_mode = None
            self.walk_target_x = None
            self.climb_edge = None
            self.peek_timer = 0
            self.state_label = "SLEEPING"
            return
        if action == "uyan":
            self.state_label = "IDLE"
            return
        if self.is_ghost:
            return
        if self.pinned:
            self.toggle_pin()
        if self.state_label == "SLEEPING":
            self.state_label = "IDLE"
        self.peek_timer = 0

        avail = QApplication.primaryScreen().availableGeometry()
        screen_geo = QApplication.primaryScreen().geometry()
        floor_y = float(avail.bottom() + 1)
        cur = QCursor.pos()

        if action == "zipla":
            self.climb_edge = None
            self.command_mode = None
            self.vy = -14.5
        elif action == "pati_at":
            self.swat_timer = 20
        elif action == "dans_et":
            self.state_label = "VIBING"
            self.set_overlay("vibing", min_ticks=180)
        elif action == "gorev_cubuguna_in":
            self.command_mode = None
            self.climb_edge = None
            self.walk_target_x = None
            if self.cat_bottom_y < floor_y - 4.0:
                self.cat_bottom_y += 18.0
                self.vy = 2.0
        elif action == "pencereye_cik":
            rect = self._refresh_perch(ignore_height=True, ignore_x=True)
            if not rect:
                self.show_message("Çıkılacak pencere bulamadım.", self.state_label)
                return
            l, t, r, b = rect
            safe_min, safe_max = _perch_safe_bounds(rect)
            safe_x = float(max(safe_min, min(safe_max, self.cat_cx)))
            dy = self.cat_bottom_y - t
            if dy <= MAX_JUMP_DY:
                self.climb_edge = None
                self.command_mode = "walk_to_cursor"
                self.walk_target_x = safe_x
                self._launch_ballistic_jump(safe_x, dy, extra_vy=2.0, max_vx=10.0)
            else:
                l_edge, r_edge = float(l - CLIMB_EDGE_OFFSET), float(r + CLIMB_EDGE_OFFSET)
                edge_x, side = (l_edge, "left") if abs(self.cat_cx - l_edge) <= abs(self.cat_cx - r_edge) else (r_edge, "right")
                if avail.left() + 16 <= edge_x <= avail.right() - 16 and self.cat_bottom_y - b <= MAX_JUMP_DY:
                    self.climb_edge = (edge_x, float(t), float(b), side)
                else:
                    self.climb_edge = self._refresh_climb_edge()
                self.climb_target_y = float(t)
                self.walk_target_x = safe_x
                self.command_mode = "climb"
        elif action == "imlece_gel":
            if not screen_geo.contains(cur):
                self.show_message("İmleç diğer ekranda, oraya geçemiyorum.", self.state_label)
                return
            offset = -40 if cur.x() > self.cat_cx else 40
            target_x = float(max(avail.left() + 36, min(avail.right() - 36, cur.x() + offset)))
            target_y = float(max(avail.top() + 48, min(floor_y, cur.y())))
            dy = self.cat_bottom_y - target_y
            self.climb_edge = None
            if dy <= 50:
                self.command_mode = "walk_to_cursor"
                self.walk_target_x = target_x
            elif dy <= MAX_JUMP_DY:
                self.command_mode = "walk_to_cursor"
                self.walk_target_x = target_x
                self._launch_ballistic_jump(target_x, dy, extra_vy=1.5, max_vx=10.0)
            else:
                edge = self._refresh_climb_edge()
                if edge:
                    self.climb_edge = edge
                    self.climb_target_y = target_y
                    self.walk_target_x = target_x
                    self.command_mode = "climb"
                else:
                    self.command_mode = "walk_to_cursor"
                    self.walk_target_x = target_x
                    self.vy = -18.5
                    self.vx = max(-8.0, min(8.0, (target_x - self.cat_cx) / 24.0))
                    self.show_message("O kadar yükseğe yetişemiyorum!", self.state_label)
        elif action == "imleci_takip_et":
            self.climb_edge = None
            self.command_mode = "follow_cursor"
            self.follow_until = time.monotonic() + FOLLOW_DURATION_SEC
            self._cursor_idle_frames = 0

    def _physics_tick(self) -> None:
        avail = QApplication.primaryScreen().availableGeometry()
        floor_y = float(avail.bottom() + 1)
        self.tail_phase += 0.22 if self.state_label != "SLEEPING" else 0.05
        if self.debug_locked:
            self.walk_phase += 1
        self._perch_tick += 1
        if self._perch_tick % 10 == 0 and not self.dragging and self.command_mode != "climb":
            self._refresh_perch(ignore_height=(self.vy < 0))

        if self.debug_pose == "pet_hearts" and self._perch_tick % 18 == 0:
            self.hearts.append([random.uniform(-16, 16), -4.0, -1.3, 1.0])

        if self.swat_timer > 0:
            self.swat_timer -= 1
        if self.pet_timer > 0:
            self.pet_timer -= 1
        if self._dodge_cooldown > 0:
            self._dodge_cooldown -= 1
        if self.overlay_timer > 0:
            self.overlay_timer -= 1
            if self.overlay_timer == 0:
                self.active_overlay = None

        for h in self.hearts[:]:
            h[1] += h[2]
            h[3] -= 0.04
            if h[3] <= 0.0:
                self.hearts.remove(h)

        if self.is_ghost:
            self.update()
            return

        if self.debug_pose is not None:
            self.scale_x += (1.0 - self.scale_x) * 0.25
            self.scale_y += (1.0 - self.scale_y) * 0.25
            self._sync_window_pos()
            self.update()
            return

        if self.dragging:
            self.drag_angle *= 0.85
            self.update()
            return

        self.drag_angle *= 0.72
        self._is_climbing = False

        # Tırmanma modu fiziği (dikey kenarda yerçekimi devre dışı)
        if self.command_mode == "climb" and self.climb_edge is not None:
            edge_x, edge_top, edge_bottom, side = self.climb_edge
            target_y = self.climb_target_y if self.climb_target_y is not None else edge_top
            if abs(self.cat_cx - edge_x) <= 6.0 and self.cat_bottom_y <= edge_bottom + 14.0:
                self._is_climbing = True
                self.cat_cx = edge_x
                self.vx = 0.0
                self.vy = 0.0
                self.scale_x += (1.0 - self.scale_x) * 0.25
                self.scale_y += (1.0 - self.scale_y) * 0.25
                self.facing_right = self._resolve_climb_facing(edge_x, side, avail)
                if self._perch_tick % 10 == 0 and (avail.left() + 45 < edge_x < avail.right() - 45):
                    cur_edge = self._refresh_climb_edge()
                    if not cur_edge or abs(cur_edge[0] - edge_x) > 32:
                        self.command_mode = None
                        self.climb_edge = None
                        self.climb_target_y = None
                        self.vy = 1.0
                        self._is_climbing = False
                if self._is_climbing:
                    dy_step = target_y - self.cat_bottom_y
                    if abs(dy_step) <= 3.5:
                        self.cat_bottom_y = target_y
                        if self.perch_rect and abs(target_y - self.perch_rect[1]) <= 14:
                            l, _t, r, _ = self.perch_rect
                            inward = 1.0 if self.cat_cx < (l + r) / 2.0 else -1.0
                            safe_min, safe_max = _perch_safe_bounds(self.perch_rect)
                            dest_x = (
                                self.walk_target_x
                                if self.walk_target_x is not None
                                else (safe_min + 12 if inward > 0 else safe_max - 12)
                            )
                            self.vy = -5.0
                            self.vx = 4.5 * inward
                            self.facing_right = inward > 0
                            self.walk_target_x = float(max(safe_min, min(safe_max, dest_x)))
                            self.command_mode = "walk_to_cursor"
                            self.climb_edge = None
                            self.climb_target_y = None
                        else:
                            dest_x = (
                                self.walk_target_x
                                if self.walk_target_x is not None
                                else (self.cat_cx + (48.0 if self.cat_cx < avail.center().x() else -48.0))
                            )
                            self.vx = max(-10.0, min(10.0, (dest_x - self.cat_cx) / 18.0))
                            self.vy = -5.5
                            self.facing_right = dest_x >= self.cat_cx
                            self.command_mode = "walk_to_cursor" if self.walk_target_x is not None else None
                            self.climb_edge = None
                            self.climb_target_y = None
                    else:
                        self.cat_bottom_y += CLIMB_STEP_PX if dy_step > 0 else -CLIMB_STEP_PX
                        self.walk_phase += 1
                    self._sync_window_pos()
                    self.update()
                    return
            elif abs(self.cat_cx - edge_x) <= 6.0 and self.cat_bottom_y > edge_bottom + 14.0 and self.vy >= 0:
                jump_h = self.cat_bottom_y - edge_bottom
                if jump_h <= MAX_JUMP_DY:
                    self.vy = -math.sqrt(2.0 * GRAVITY * jump_h) - 2.0
                else:
                    s_side = "left" if self.cat_cx < avail.center().x() else "right"
                    sx = float(avail.left() + 28) if s_side == "left" else float(avail.right() - 28)
                    self.climb_edge = (sx, float(avail.top() + 40), floor_y, s_side)
            else:
                self.walk_target_x = edge_x

        if self.command_mode == "peek" and self.peek_timer > 0:
            self.peek_timer -= 1
            self.facing_right = self.peek_side == "left"
            if self.peek_timer == 0:
                self.command_mode = None
                self.walk_target_x = float(avail.left() + 48) if self.peek_side == "left" else float(avail.right() - 48)
                self.peek_side = None
            self._sync_window_pos()
            self.update()
            return

        self._track_perch_delta()
        surface_y = floor_y
        perch_top = None
        if self.perch_rect:
            left, top, right, _ = self.perch_rect
            if 20 <= top <= floor_y - 40 and (left + 12) <= self.cat_cx <= (right - 12):
                perch_top = float(top)
                if self.cat_bottom_y <= perch_top + 14 and self.vy >= 0:
                    surface_y = perch_top

        if self.cat_bottom_y < surface_y or self.vy < 0.0:
            prev_bottom_y = self.cat_bottom_y
            self.vy += GRAVITY
            self.cat_cx += self.vx
            self.cat_bottom_y += self.vy
            if self.command_mode != "peek":
                if self.cat_cx < avail.left() + 32:
                    self.cat_cx = float(avail.left() + 32)
                    self.vx = abs(self.vx) * 0.6
                elif self.cat_cx > avail.right() - 32:
                    self.cat_cx = float(avail.right() - 32)
                    self.vx = -abs(self.vx) * 0.6
            if self.vy > 2.5:
                self.scale_x += (0.84 - self.scale_x) * 0.25
                self.scale_y += (1.22 - self.scale_y) * 0.25
            landed_y = None
            if perch_top is not None and self.vy >= 0.0 and prev_bottom_y <= perch_top + 14 and self.cat_bottom_y >= perch_top:
                landed_y = perch_top
            elif self.cat_bottom_y >= surface_y and self.vy > 0.0:
                landed_y = surface_y
            if landed_y is not None:
                impact = self.vy
                self.cat_bottom_y = landed_y
                self.vy = 0.0
                self.vx *= 0.35
                if impact > 4.0:
                    self.scale_x = min(1.34, 1.0 + impact * 0.024)
                    self.scale_y = max(0.68, 1.0 - impact * 0.024)
        else:
            self.scale_x += (1.0 - self.scale_x) * 0.25
            self.scale_y += (1.0 - self.scale_y) * 0.25
            self._autonomous_move(avail, floor_y)

        self._sync_window_pos()
        self.update()

    def _autonomous_move(self, avail, floor_y: float) -> None:
        if self.debug_locked or self.pinned or self.state_label == "SLEEPING" or self.active_overlay == "writing_diary":
            return
        if self.command_mode is None and self.input_box.hasFocus():
            return

        cur = QCursor.pos()
        c_dx = cur.x() - self._last_cursor.x()
        c_dy = cur.y() - self._last_cursor.y()
        cursor_speed = math.hypot(c_dx, c_dy)
        self._last_cursor = cur
        self._cursor_idle_frames = self._cursor_idle_frames + 1 if cursor_speed < 4.0 else 0

        if self.command_mode == "follow_cursor":
            if time.monotonic() > self.follow_until or self._cursor_idle_frames > 90:
                self.command_mode = None
                self.walk_target_x = None
                return
            offset = -40 if cur.x() > self.cat_cx else 40
            self.walk_target_x = float(max(avail.left() + 36, min(avail.right() - 36, cur.x() + offset)))

        if self.perch_rect and abs(self.cat_bottom_y - self.perch_rect[1]) < 4 and self.walk_target_x is None:
            safe_min_x, safe_max_x = _perch_safe_bounds(self.perch_rect, pad=PERCH_WALK_PAD)
            if self.cat_cx < safe_min_x or self.cat_cx > safe_max_x:
                self.walk_target_x = float(max(safe_min_x, min(safe_max_x, self.cat_cx)))

        if self.command_mode is None:
            if self._handle_cursor_proximity(cur.x(), cur.y(), cursor_speed):
                return

            if self._cursor_idle_frames > 120 and abs(cur.y() - (self.cat_bottom_y - 24)) < 120:
                self.walk_target_x = float(cur.x())
                if abs(cur.x() - self.cat_cx) < 38.0:
                    self.facing_right = cur.x() > self.cat_cx
                    self.swat_timer = 14
                    self._cursor_idle_frames = 0
                    self.walk_target_x = None
                    return

            if self.walk_target_x is None and random.random() < 0.015:
                on_perch = bool(self.perch_rect and abs(self.cat_bottom_y - self.perch_rect[1]) < 4)
                if on_perch and self.perch_rect:
                    safe_min, safe_max = _perch_safe_bounds(self.perch_rect, pad=PERCH_WALK_PAD)
                    if safe_max - safe_min > 30:
                        roll = random.random()
                        if roll < 0.55:
                            self.walk_target_x = float(safe_min + 4 if random.random() < 0.5 else safe_max - 4)
                            return
                        if roll < 0.80:
                            self.walk_target_x = float(random.uniform(safe_min, safe_max))
                            return
                    self.cat_bottom_y += 18.0
                    self.vy = -4.0
                    return
                if self.perch_rect and abs(self.cat_bottom_y - floor_y) < 5.0 and random.random() < 0.35:
                    _left, top, _right, _ = self.perch_rect
                    safe_min, safe_max = _perch_safe_bounds(self.perch_rect)
                    dy = self.cat_bottom_y - top
                    if 20 <= top <= floor_y - 40 and safe_max - safe_min > 40 and dy <= MAX_JUMP_DY:
                        tx = float(random.uniform(safe_min, safe_max))
                        self._launch_ballistic_jump(tx, dy, extra_vy=1.5, max_vx=9.0)
                        return
                if self.state_label in ("IDLE", "CURIOUS") and abs(self.cat_bottom_y - floor_y) < 5.0 and random.random() < 0.20:
                    self.peek_side = "left" if self.cat_cx < avail.center().x() else "right"
                    self.command_mode = "peek"
                    self.walk_target_x = float(avail.left() - 4) if self.peek_side == "left" else float(avail.right() + 4)
                    return
                self.walk_target_x = float(random.randint(avail.left() + 48, avail.right() - 48))

        if self.walk_target_x is not None:
            diff = self.walk_target_x - self.cat_cx
            tol = 4.0 if self.command_mode else 3.0
            if abs(diff) <= tol:
                self.walk_target_x = None
                self.walk_phase = 0
                if self.command_mode == "walk_to_cursor":
                    self.command_mode = None
                    if math.hypot(cur.x() - self.cat_cx, cur.y() - (self.cat_bottom_y - 24)) < 90:
                        self.facing_right = cur.x() >= self.cat_cx
                        self.swat_timer = 10
                elif self.command_mode == "peek":
                    self.peek_timer = 75
            else:
                speed = WALK_SPEED_CMD if self.command_mode in ("walk_to_cursor", "follow_cursor", "climb") else WALK_SPEED_IDLE
                step = speed if diff > 0 else -speed
                self.facing_right = step > 0
                self.cat_cx += step
                self.walk_phase += 1

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

        cur = QCursor.pos()
        pdx = 1 if cur.x() > self.cat_cx + 10 else (-1 if cur.x() < self.cat_cx - 10 else 0)
        pdy = 1 if cur.y() > (self.cat_bottom_y - 24) + 10 else 0
        if self.active_overlay == "writing_diary":
            pdx, pdy = -1, 1

        eye_state = "normal"
        if self.debug_pose == "sleep_curl" or self.state_label == "SLEEPING":
            eye_state = "closed_sleep"
        elif self.debug_pose == "pet_hearts" or self.pet_timer > 0:
            eye_state = "squint_happy"

        is_climbing = getattr(self, "_is_climbing", False) or self.debug_pose == "climbing"
        is_peeking = (self.command_mode == "peek" and self.peek_timer > 0) or self.debug_pose == "peeking"
        is_perched = bool(self.perch_rect and abs(self.cat_bottom_y - self.perch_rect[1]) < 4)
        is_vibing = self.state_label == "VIBING" or self.active_overlay == "vibing"
        pix = self.renderer.render(
            palette_key=self.palette_key,
            facing_right=self.facing_right,
            walk_phase=self.walk_phase // 3,
            tail_phase=self.tail_phase,
            pupil_dx=pdx,
            pupil_dy=pdy,
            eye_state=eye_state,
            is_hanging=self.dragging or self.debug_pose == "hanging",
            is_perched=is_perched,
            is_swatting=self.swat_timer > 0 or self.debug_pose == "swatting",
            is_climbing=is_climbing,
            is_peeking=is_peeking,
            overlay=self.active_overlay,
            is_vibing=is_vibing,
            is_dangling=self._is_dangling() or self.debug_pose == "dangling",
        )

        w = max(24, int(CAT_SIZE * self.scale_x))
        h_base = max(24, int(CAT_SIZE * self.scale_y))
        h = max(24, int(round(pix.height() * self.scale_y)))
        scaled = pix.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)

        cx = WIN_W // 2
        y_bob = int(round(math.sin(self.tail_phase * 2.2) * 2.0)) if (is_vibing and not self.dragging) else 0
        top_y = (WIN_H - DANGLE_PAD) - h_base + 5
        if self.dragging:
            p.save()
            p.translate(cx, top_y)
            p.rotate(self.drag_angle)
            p.drawPixmap(-w // 2, 0, scaled)
            p.restore()
        else:
            p.drawPixmap(cx - w // 2, top_y + y_bob, scaled)

        for hx, hy, _hvy, alpha in self.hearts:
            p.setPen(QColor(255, 77, 109, int(max(0, min(255, alpha * 255)))))
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.drawText(int(cx + hx), int(WIN_H - DANGLE_PAD - 64 + hy), "♥")

    def _show_context_menu(self, gpos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)
        menu.addAction("🎛 Kontrol Merkezi", self.open_menu)
        menu.addAction("📖 Günlüğü Oku", self.open_diary)
        menu.addAction("💬 Sohbet Kutusu (Alt+M)", self.toggle_chat_input)
        menu.addSeparator()
        for label, act_name, in_ctx in MENU_ACTIONS:
            if in_ctx:
                menu.addAction(label, lambda _c=False, a=act_name: self._run_action(a))
        menu.addAction("☀️ Uyan" if self.state_label == "SLEEPING" else "😴 Uyu", lambda: self._run_action("uyan" if self.state_label == "SLEEPING" else "uyu"))
        menu.addSeparator()

        debug_menu = menu.addMenu("🛠 Animasyon Testi")
        for key, label, _kind in DEBUG_ANIM_SPECS:
            debug_menu.addAction(label, lambda _c=False, k=key: self.trigger_debug_animation(k))
        debug_menu.addSeparator()
        debug_menu.addAction("🔄 Normal Fiziğe Dön (Sıfırla)", lambda: self.trigger_debug_animation("reset"))

        menu.addSeparator()
        pin_act = menu.addAction("📌 Sabitle (Pin)", self.toggle_pin)
        pin_act.setCheckable(True)
        pin_act.setChecked(self.pinned)
        ghost_act = menu.addAction("👻 Hayalet Modu", lambda: self.set_ghost_mode(not self.is_ghost))
        ghost_act.setCheckable(True)
        ghost_act.setChecked(self.is_ghost)
        pal_menu = menu.addMenu("🎨 Kedi Görünümü")
        for key, label in PALETTE_NAMES.items():
            act = pal_menu.addAction(label, lambda _c=False, k=key: self.set_palette(k))
            act.setCheckable(True)
            act.setChecked(key == self.palette_key)
        menu.addSeparator()
        menu.addAction("🚪 Çıkış", QApplication.quit)
        menu.exec(gpos)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.y() >= (WIN_H - DANGLE_PAD) - CAT_SIZE - 8:
                self.dragging = True
                self.command_mode = None
                self.climb_edge = None
                self.climb_target_y = None
                self.peek_timer = 0
                self._press_pos = event.globalPosition().toPoint()
                self._last_drag_global = self._press_pos
                self._drag_vx = self._drag_vy = 0.0

    def mouseMoveEvent(self, event) -> None:
        gpos = event.globalPosition().toPoint()
        if self.dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            dx = gpos.x() - self._last_drag_global.x()
            dy = gpos.y() - self._last_drag_global.y()
            self._drag_vx = 0.6 * self._drag_vx + 0.4 * dx
            self._drag_vy = 0.6 * self._drag_vy + 0.4 * dy
            self.drag_angle = max(-32.0, min(32.0, -dx * 1.8))
            self.cat_cx += dx
            self.cat_bottom_y += dy
            self._last_drag_global = gpos
            self._sync_window_pos()
            self.update()
            return

        pos = event.position().toPoint()
        base_bot = WIN_H - DANGLE_PAD
        if abs(pos.x() - WIN_W // 2) < 34 and (base_bot - CAT_SIZE) <= pos.y() <= (base_bot - 34):
            self._pet_accumulator += abs(pos.x() - self._last_hover_x)
            self._last_hover_x = float(pos.x())
            if self._pet_accumulator > 48.0:
                self._pet_accumulator = 0.0
                self.pet_timer = 35
                self.hearts.append([random.uniform(-16, 16), -4.0, -1.3, 1.0])
                self.on_pet()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.dragging:
            self.dragging = False
            dist = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if dist < 5:
                self.toggle_chat_input()
            else:
                self.vx = max(-22.0, min(22.0, self._drag_vx))
                self.vy = max(-22.0, min(22.0, self._drag_vy))
                self._refresh_perch()
                if self.perch_rect and self.perch_rect[1] < self.cat_bottom_y <= self.perch_rect[1] + 42 and self.vy >= -3.0:
                    self.cat_bottom_y = float(self.perch_rect[1])
                    self.vy = 0.0
                    self.vx *= 0.35
                    self._sync_window_pos()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.toggle_pin()

    def nativeEvent(self, eventType, message):
        if bytes(eventType) == b"windows_generic_MSG":
            msg = MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == 1:
                will_open = not self.input_box.isVisible()
                self.toggle_chat_input()
                if will_open:
                    self._run_action("imlece_gel")
                return True, 0
        return False, 0

    def closeEvent(self, event) -> None:
        windll.user32.UnregisterHotKey(int(self.winId()), 1)
        if self.menu_dialog:
            self.menu_dialog.hide()
        if self.diary_dialog:
            self.diary_dialog.hide()
        self.tray.hide()
        super().closeEvent(event)
