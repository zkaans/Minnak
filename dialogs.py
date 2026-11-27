"""Katman 1b: Sekmeli Kontrol Merkezi, Canlı Durum Kartı, Animasyon Debug ve Günlük Okuyucu."""
from typing import TYPE_CHECKING
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from brain import MENU_ACTIONS
from sprites import DEBUG_ANIM_SPECS, PALETTE_NAMES

if TYPE_CHECKING:
    from ui import MinnakWindow

_DARK_DIALOG_STYLE = """
QWidget {
    background: #12141b;
    color: #eef1f7;
    font-family: 'Segoe UI';
    font-size: 9pt;
}
QFrame#statusCard {
    background: #181c26;
    border: 1px solid #2b3142;
    border-radius: 8px;
}
QLabel#sectionTitle {
    color: #9aa5be;
    font-weight: 600;
    font-size: 8.5pt;
}
QGroupBox {
    background: #161923;
    border: 1px solid #282e3f;
    border-radius: 7px;
    margin-top: 10px;
    padding: 8px 6px 6px 6px;
    font-weight: 600;
    color: #b8c2d9;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QPushButton {
    background: #212634;
    border: 1px solid #32394d;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 18px;
    color: #eef1f7;
}
QPushButton:hover {
    background: #2c3346;
    border-color: #46506b;
}
QPushButton:pressed {
    background: #1b202c;
}
QPushButton:checked {
    background: #2b4266;
    border-color: #5b8def;
    color: #ffffff;
}
QPushButton#resetBtn {
    background: #2b2129;
    border: 1px solid #593848;
    font-weight: 600;
}
QPushButton#resetBtn:hover {
    background: #3b2a35;
    border-color: #7d4c63;
}
QLineEdit, QListWidget, QComboBox, QTextEdit {
    background: #181c26;
    border: 1px solid #2e3447;
    border-radius: 5px;
    padding: 4px 6px;
    color: #eef1f7;
}
QListWidget::item {
    padding: 3px 4px;
    border-radius: 3px;
}
QLineEdit:focus, QComboBox:focus, QListWidget:focus {
    border-color: #5b8def;
}
QTabWidget::pane {
    border: 1px solid #2b3142;
    border-radius: 7px;
    background: #141720;
    top: -1px;
}
QTabBar::tab {
    background: #1a1e2a;
    color: #9aa5be;
    border: 1px solid #2b3142;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 12px;
    margin-right: 3px;
}
QTabBar::tab:selected {
    background: #23293a;
    color: #ffffff;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: #1f2433;
}
QProgressBar {
    background: #10131a;
    border: 1px solid #282e3f;
    border-radius: 4px;
    text-align: center;
    font-size: 8pt;
    color: #eef1f7;
    min-height: 14px;
    max-height: 14px;
}
"""


def _make_bar(color: str) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setTextVisible(True)
    bar.setStyleSheet(f"QProgressBar::chunk {{ background-color: {color}; border-radius: 3px; }}")
    return bar


class MinnakDiaryDialog(QWidget):
    """Minnak'ın tuttuğu gunluk.md dosyasını gösteren salt-okunur okuyucu."""

    def __init__(self, win: "MinnakWindow", memory=None, on_write_diary=None):
        super().__init__()
        self.win = win
        self.memory = memory
        self.on_write_diary = on_write_diary or (lambda: None)
        self.setWindowTitle("Minnak'ın Günlüğü")
        self.setFixedSize(380, 440)
        self.setStyleSheet(_DARK_DIALOG_STYLE)
        root = QVBoxLayout(self)
        root.setSpacing(8)
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        root.addWidget(self.text_view, 1)

        btn_row = QHBoxLayout()
        btn_write = QPushButton("✍️ Şimdi Günlüğe Yaz")
        btn_write.clicked.connect(lambda: (self.on_write_diary(), QTimer.singleShot(500, self.refresh)))
        btn_refresh = QPushButton("🔄 Yenile")
        btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(btn_write)
        btn_row.addWidget(btn_refresh)
        root.addLayout(btn_row)

    def refresh(self) -> None:
        txt = self.memory.read_diary() if self.memory is not None else ""
        self.text_view.setPlainText(txt or "# Minnak Günlüğü\nHenüz günlük kaydı yok.")
        sb = self.text_view.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def showEvent(self, event) -> None:
        self.refresh()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()


class MinnakMenuDialog(QWidget):
    """Canlı durum kartı, hızlı aksiyonlar, hafıza ve animasyon debug sekmeli Kontrol Merkezi."""

    def __init__(self, win: "MinnakWindow", memory=None, get_status=None, on_check_ollama=None, on_clear_history=None):
        super().__init__()
        self.win = win
        self.memory = memory
        self.get_status = get_status or (lambda: {"enerji": win.enerji_val, "state": win.state_label})
        self.on_check_ollama = on_check_ollama or (lambda: False)
        self.on_clear_history = on_clear_history or (lambda: None)
        self.debug_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("Minnak Kontrol Merkezi")
        self.setFixedSize(440, 610)
        self.setStyleSheet(_DARK_DIALOG_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Üst sabit Canlı Durum Kartı
        card = QFrame()
        card.setObjectName("statusCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 8, 10, 8)
        card_lay.setSpacing(6)

        self.status_lbl = QLabel("Durum yükleniyor...")
        self.status_lbl.setWordWrap(True)
        card_lay.addWidget(self.status_lbl)

        bars_grid = QGridLayout()
        bars_grid.setSpacing(5)
        self.bar_enerji = _make_bar("#0284c7")
        self.bar_merak = _make_bar("#d97706")
        self.bar_sikilma = _make_bar("#e11d48")
        self.bar_samimiyet = _make_bar("#7c3aed")
        bar_items = [
            ("⚡ Enerji", self.bar_enerji, 0, 0),
            ("🔍 Merak", self.bar_merak, 0, 1),
            ("🥱 Sıkılma", self.bar_sikilma, 1, 0),
            ("💜 Samimiyet", self.bar_samimiyet, 1, 1),
        ]
        for lbl_txt, bar, row_i, col_i in bar_items:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            lbl = QLabel(lbl_txt)
            lbl.setObjectName("sectionTitle")
            cell.addWidget(lbl)
            cell.addWidget(bar)
            bars_grid.addLayout(cell, row_i, col_i)
        card_lay.addLayout(bars_grid)

        pal_row = QHBoxLayout()
        pal_lbl = QLabel("🎨 Görünüm:")
        pal_lbl.setObjectName("sectionTitle")
        pal_row.addWidget(pal_lbl)
        self.pal_combo = QComboBox()
        for key, label in PALETTE_NAMES.items():
            self.pal_combo.addItem(label, key)
        self.pal_combo.setCurrentIndex(list(PALETTE_NAMES).index(self.win.palette_key))
        self.pal_combo.currentIndexChanged.connect(lambda: self.win.set_palette(self.pal_combo.currentData()))
        pal_row.addWidget(self.pal_combo, 1)
        card_lay.addLayout(pal_row)
        root.addWidget(card)

        # Altta 2 sekmeli panel
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_control_tab(), "🎮 Kontrol && Hafıza")
        self.tabs.addTab(self._build_debug_tab(), "🛠 Animasyon && Debug")
        root.addWidget(self.tabs, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def _build_control_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        grid = QGridLayout()
        grid.setSpacing(5)
        actions = [
            (label, lambda _c=False, act=act_name: self.win._run_action(act))
            for label, act_name, _in_ctx in MENU_ACTIONS
        ] + [
            ("😴 Uyu / Uyan", self._toggle_sleep),
            ("📌 Sabitle", self.win.toggle_pin),
            ("👻 Hayalet Modu", lambda: self.win.set_ghost_mode(not self.win.is_ghost)),
            ("📖 Günlüğü Oku", self.win.open_diary),
        ]
        for idx, (label, fn) in enumerate(actions):
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            grid.addWidget(btn, idx // 3, idx % 3)
        lay.addLayout(grid)

        mem_lbl = QLabel("🧠 Hafıza (ogrendiklerim.md):")
        mem_lbl.setObjectName("sectionTitle")
        lay.addWidget(mem_lbl)
        self.rule_list = QListWidget()
        lay.addWidget(self.rule_list, 1)

        add_row = QHBoxLayout()
        self.rule_input = QLineEdit()
        self.rule_input.setPlaceholderText("Yeni kural yaz...")
        btn_add = QPushButton("➕ Ekle")
        btn_add.clicked.connect(self._add_rule)
        btn_del = QPushButton("🗑 Seçiliyi Sil")
        btn_del.clicked.connect(self._del_rule)
        add_row.addWidget(self.rule_input, 1)
        add_row.addWidget(btn_add)
        add_row.addWidget(btn_del)
        lay.addLayout(add_row)

        sys_row = QHBoxLayout()
        btn_refl = QPushButton("🌙 Gece Yansıması")
        btn_refl.clicked.connect(lambda: (self.win.on_night_reflection(), self.refresh()))
        btn_ollama = QPushButton("🔌 Ollama Kontrol")
        btn_ollama.clicked.connect(self._check_ollama)
        btn_clr = QPushButton("🧹 Geçmişi Temizle")
        btn_clr.clicked.connect(lambda: (self.on_clear_history(), self.win.show_message("Sohbet geçmişi temizlendi.", self.win.state_label)))
        sys_row.addWidget(btn_refl)
        sys_row.addWidget(btn_ollama)
        sys_row.addWidget(btn_clr)
        lay.addLayout(sys_row)
        return tab

    def _build_debug_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self.debug_status_lbl = QLabel("Aktif Test: Yok (Normal Fizik)")
        self.debug_status_lbl.setObjectName("sectionTitle")
        lay.addWidget(self.debug_status_lbl)

        btn_reset = QPushButton("🔄 Normal Fiziğe Dön (Sıfırla)")
        btn_reset.setObjectName("resetBtn")
        btn_reset.clicked.connect(lambda: self.win.trigger_debug_animation("reset"))
        lay.addWidget(btn_reset)

        for title, kind in (("Piksel Katmanları (Overlays)", "overlay"), ("Fizik && Poz Zorlama (Override)", "pose")):
            grp = QGroupBox(title)
            g_lay = QGridLayout(grp)
            g_lay.setContentsMargins(6, 8, 6, 8)
            g_lay.setSpacing(6)
            items = [(key, label) for key, label, k in DEBUG_ANIM_SPECS if k == kind]
            for idx, (key, label) in enumerate(items):
                btn = QPushButton(label)
                btn.setCheckable(kind == "overlay" or key != "dodge")
                btn.clicked.connect(lambda _c=False, k=key: self.win.trigger_debug_animation(k))
                self.debug_buttons[key] = btn
                g_lay.addWidget(btn, idx // 2, idx % 2)
            lay.addWidget(grp)

        lay.addStretch()
        return tab

    def _toggle_sleep(self) -> None:
        self.win._run_action("uyan" if self.win.state_label == "SLEEPING" else "uyu")
        self.refresh()

    def _add_rule(self) -> None:
        txt = self.rule_input.text().strip()
        if txt and self.memory is not None:
            self.memory.append_rule(txt)
            self.rule_input.clear()
            self.refresh_rules()

    def _del_rule(self) -> None:
        item = self.rule_list.currentItem()
        if item and self.memory is not None:
            self.memory.remove_rule(item.text())
            self.refresh_rules()

    def _check_ollama(self) -> None:
        self.win.show_message("Ollama kontrol ediliyor...", self.win.state_label)
        self.on_check_ollama()

    def refresh_rules(self) -> None:
        self.rule_list.clear()
        if self.memory is None:
            return
        for ln in self.memory.read_rules().splitlines():
            s = ln.strip()
            if s.startswith("- "):
                self.rule_list.addItem(s[2:].strip())

    def refresh(self) -> None:
        st = self.get_status()
        ollama_txt = "🟢 Açık" if st.get("ollama_online") else "🔴 Kapalı"
        state_val = st.get("state", self.win.state_label)
        enerji = float(st.get("enerji", self.win.enerji_val))
        merak = float(st.get("merak", self.win.merak_val))
        sikilma = float(st.get("sikilma", self.win.sikilma_val))
        samimiyet = float(st.get("samimiyet", 50.0))

        active_dbg = self.win.debug_pose or (self.win.active_overlay if self.win.debug_locked else None)
        dbg_suffix = f" | 🛠 Debug: {active_dbg}" if active_dbg else ""
        self.status_lbl.setText(f"Durum: {state_val}  •  Ollama: {ollama_txt}{dbg_suffix}")

        for bar, val in (
            (self.bar_enerji, enerji),
            (self.bar_merak, merak),
            (self.bar_sikilma, sikilma),
            (self.bar_samimiyet, samimiyet),
        ):
            clamped = max(0, min(100, int(round(val))))
            bar.setValue(clamped)
            bar.setFormat(f"%{clamped}")

        self.debug_status_lbl.setText(
            f"Aktif Test: {active_dbg} (Kilitli)" if active_dbg else "Aktif Test: Yok (Normal Fizik)"
        )
        for key, btn in self.debug_buttons.items():
            if btn.isCheckable():
                btn.setChecked(key == active_dbg)

    def showEvent(self, event) -> None:
        self.refresh()
        self.refresh_rules()
        self._timer.start(1000)
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self.hide()
        event.ignore()
