"""Katman 1a: Piksel Kedi Çizici, Renk Paletleri ve Animasyon Katmanları (Overlays)."""
import json
import math
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap, QTransform, qRgba

CAT_SIZE = 64
DANGLE_PAD = 16

PALETTE_NAMES = {
    "white_tabby": "Beyaz Tekir (Varsayılan)",
    "void_black": "Siyah Void",
    "orange_tabby": "Turuncu Tekir",
    "tuxedo": "Smokin",
}

PALETTES = {
    "white_tabby": {
        "O": "#2B2D42", "B": "#F4F5F7", "D": "#C9D1DC", "C": "#FFFFFF", "S": "#6C757D",
        "E": "#FF8FA3", "N": "#E85D75", "Y": "#6EC1E4", "P": "#1A1B26",
    },
    "void_black": {
        "O": "#08080C", "B": "#1B1C24", "D": "#11121A", "C": "#2E303D", "S": "#14151C",
        "E": "#4A3B47", "N": "#5C4B59", "Y": "#FFD166", "P": "#08080C",
    },
    "orange_tabby": {
        "O": "#3D2114", "B": "#F4A261", "D": "#D97B3E", "C": "#FFF3E6", "S": "#C85A32",
        "E": "#FF9AA2", "N": "#D95D5D", "Y": "#2A9D8F", "P": "#1E1410",
    },
    "tuxedo": {
        "O": "#0E1016", "B": "#1F222B", "D": "#14161E", "C": "#FFFFFF", "S": "#2C303D",
        "E": "#FF8FA3", "N": "#E85D75", "Y": "#72EFDD", "P": "#0E1016",
    },
}

MASTER_KEY_COLORS: dict[tuple[int, int, int], str] = {
    (0x2B, 0x2D, 0x42): "O",
    (0xF4, 0xF5, 0xF7): "B",
    (0xC9, 0xD1, 0xDC): "D",
    (0xFF, 0xFF, 0xFF): "C",
    (0x6C, 0x75, 0x7D): "S",
    (0xFF, 0x8F, 0xA3): "E",
    (0xE8, 0x5D, 0x75): "N",
    (0x4E, 0xCD, 0xC4): "Y",
    (0x1A, 0x1B, 0x26): "P",
}

# 24x24 sola bakan piksel kedi iskeleti (. = şeffaf, O = dış hat, B = gövde, C = göğüs/pati, S = tekir çizgi, E = kulak içi, N = burun)
BASE_GRID = [
    "........................",
    "......OO......OO........",
    ".....OEOO....OOEO.......",
    ".....OEEOOOOOOEEO.......",
    ".....OBSBBBBBBBBO.......",
    "....OBBBSSBBSSBBBO......",
    "....OBBBBBBBBBBBBO......",
    "....OBBBBBBBBBBBBO......",
    "....OBBBBBBBBBBBBO......",
    "....OBBBBCNNCBBBBO......",
    ".....OBBCCCCCCBBO.......",
    "......OOOOOOOOOO........",
    ".......OBCCCCBO.........",
    "......OBBCCCCBBSO.......",
    ".....OBBBCCCCBBSSO......",
    ".....OBBBCCCCBBBBO......",
    ".....OBBBCCCCBBBSO......",
    "......OBBCCCCBBBBO......",
    "......OBBBBBBBBBBO......",
    ".......OBBO..OBBO.......",
    ".......OCCO..OCCO.......",
    ".......OOO....OOO.......",
    "........................",
    "........................",
]


def _overlay_research(img: QImage, pal: dict[str, str], _tail_phase: float, _put) -> None:
    for bx, by, col in [
        (19, 1, pal["O"]), (20, 1, pal["O"]),
        (18, 2, pal["O"]), (19, 2, "#FFD166"), (20, 2, "#FFD166"), (21, 2, pal["O"]),
        (18, 3, pal["O"]), (19, 3, "#FFF3B0"), (20, 3, "#FFD166"), (21, 3, pal["O"]),
        (19, 4, pal["O"]), (20, 4, pal["O"]),
        (19, 5, pal["S"]), (20, 5, pal["S"]),
    ]:
        img.setPixelColor(bx, by, QColor(col))


def _overlay_writing_diary(img: QImage, pal: dict[str, str], tail_phase: float, put) -> None:
    progress = 1 + int(round(abs(math.sin(tail_phase * 2.5)) * 5.0))
    for dy in range(15, 23):
        for dx in range(0, 10):
            if dy in (15, 22) or dx in (0, 9):
                col = pal["O"]
            elif dx == 4:
                col = pal["S"]
            elif dy in (17, 19, 20) and dx in range(1, min(9, progress + 2)) and dx != 4:
                col = "#2B2D42"
            else:
                col = "#FFF8E7"
            img.setPixelColor(dx, dy, QColor(col))
    px = progress
    for bx, by, col in [
        (px, 15, "#212529"),
        (px + 1, 14, "#E76F51"),
        (px + 2, 13, "#FFD166"),
        (px + 3, 12, "#FFD166"),
        (px + 4, 11, "#FFD166"),
        (px + 5, 10, "#FF6B6B"),
    ]:
        if 0 <= bx < 24 and 0 <= by < 24:
            img.setPixelColor(bx, by, QColor(col))
    if 0 <= px + 2 < 24:
        put(px + 2, 14, "C")
        put(px + 3, 14, "O")


def _overlay_sweat(img: QImage, pal: dict[str, str], _tail_phase: float, _put) -> None:
    for sx, sy, col in [
        (17, 3, pal["O"]),
        (16, 4, pal["O"]), (17, 4, "#E0FBFC"), (18, 4, pal["O"]),
        (16, 5, pal["O"]), (17, 5, "#4CC9F0"), (18, 5, pal["O"]),
        (17, 6, pal["O"]),
    ]:
        img.setPixelColor(sx, sy, QColor(col))


def _overlay_yawn(img: QImage, pal: dict[str, str], _tail_phase: float, _put) -> None:
    for mx, my, col in [
        (10, 10, "#FF8FA3"), (11, 10, "#FF8FA3"),
        (10, 11, pal["O"]), (11, 11, pal["O"]),
        (19, 2, "#A0C4FF"), (20, 2, "#A0C4FF"),
        (20, 3, "#A0C4FF"),
        (19, 4, "#A0C4FF"), (20, 4, "#A0C4FF"),
    ]:
        img.setPixelColor(mx, my, QColor(col))


OVERLAY_RENDERERS = {
    "research": _overlay_research,
    "writing_diary": _overlay_writing_diary,
    "sweat": _overlay_sweat,
    "yawn": _overlay_yawn,
}

DEBUG_ANIM_SPECS: list[tuple[str, str, str]] = [
    ("research", "💡 Araştırma (Ampul)", "overlay"),
    ("writing_diary", "✍️ Günlük Yazma (Defter)", "overlay"),
    ("sweat", "💧 Sistem Stresi (Ter)", "overlay"),
    ("yawn", "🥱 Gece Uyarısı (Esne)", "overlay"),
    ("vibing", "🎵 Ritim / Müzik (Nota)", "overlay"),
    ("dangling", "🐾 Köşeden Sarkma (Dangle)", "pose"),
    ("climbing", "🧗 Dikey Tırmanma (±90°)", "pose"),
    ("peeking", "👀 Kenardan Bakma (Peek)", "pose"),
    ("pet_hearts", "😻 Baş Okşama + Kalp", "pose"),
    ("swatting", "🐾 Pati Atma (Swat)", "pose"),
    ("hanging", "🤏 Enseden Tutma (Hang)", "pose"),
    ("sleep_curl", "😴 Kıvrılıp Uyuma", "pose"),
    ("dodge", "⚡ Kaçma Zıplaması (Dodge)", "pose"),
]


class CatSpriteRenderer:
    """32x32 Aseprite spritesheet'i 64x64 crisp-pixel'e çizer; yoksa 24x24 prosedürel fallback kullanır."""

    def __init__(self, sprites_dir: Path | None = None):
        self.sprites_dir = sprites_dir or (Path(__file__).parent / "sprites")
        self.sheet_tags: dict[str, tuple[list[QImage], str]] = {}
        self.overlay_tags: dict[str, tuple[list[QImage], str]] = {}
        self._palette_cache: dict[tuple[str, str, int], QImage] = {}
        self._load_spritesheet()

    def _parse_sheet(self, sheet_png: Path, sheet_json: Path) -> dict[str, tuple[list[QImage], str]]:
        if not (sheet_png.exists() and sheet_json.exists()):
            return {}
        sheet = QImage(str(sheet_png)).convertToFormat(QImage.Format.Format_RGBA8888)
        if sheet.isNull():
            return {}
        try:
            data = json.loads(sheet_json.read_text(encoding="utf-8"))
            frames_raw = data.get("frames", [])
            if isinstance(frames_raw, dict):
                frames_raw = list(frames_raw.values())
            frames = [sheet.copy(e["frame"]["x"], e["frame"]["y"], e["frame"]["w"], e["frame"]["h"]) for e in frames_raw]
            tags: dict[str, tuple[list[QImage], str]] = {}
            for tag in data.get("meta", {}).get("frameTags", []):
                start, end = int(tag["from"]), int(tag["to"])
                if 0 <= start <= end < len(frames):
                    tags[tag["name"]] = (frames[start : end + 1], tag.get("direction", "forward"))
            return tags
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _load_spritesheet(self) -> None:
        self.sheet_tags = self._parse_sheet(
            self.sprites_dir / "minnak_cat.png", self.sprites_dir / "minnak_cat.json"
        )
        self.overlay_tags = self._parse_sheet(
            self.sprites_dir / "minnak_overlays.png", self.sprites_dir / "minnak_overlays.json"
        )

    @staticmethod
    def _frame_index(count: int, step: int, direction: str) -> int:
        if count <= 1:
            return 0
        if direction == "pingpong":
            period = 2 * (count - 1)
            pos = step % period
            return pos if pos < count else period - pos
        return step % count

    def _remap_frame(self, palette_key: str, tag: str, idx: int) -> QImage:
        cache_key = (palette_key, tag, idx)
        cached = self._palette_cache.get(cache_key)
        if cached is not None:
            return cached
        pal = PALETTES.get(palette_key, PALETTES["white_tabby"])
        rgb_map = {rgb: QColor(pal[code]) for rgb, code in MASTER_KEY_COLORS.items() if code in pal}
        src = self.sheet_tags[tag][0][idx].copy()
        w, h = src.width(), src.height()
        for y in range(h):
            for x in range(w):
                c = src.pixelColor(x, y)
                if c.alpha() == 0:
                    continue
                target = rgb_map.get((c.red(), c.green(), c.blue()))
                if target is not None:
                    target_c = QColor(target)
                    target_c.setAlpha(c.alpha())
                    src.setPixelColor(x, y, target_c)
        self._palette_cache[cache_key] = src
        return src

    def _composite_overlay(self, img: QImage, name: str, tail_phase: float) -> bool:
        entry = self.overlay_tags.get(name)
        if entry is None:
            return False
        ov_frames, ov_dir = entry
        ov_idx = self._frame_index(len(ov_frames), int(tail_phase * 2.2), ov_dir)
        p = QPainter(img)
        p.drawImage(0, 0, ov_frames[ov_idx])
        p.end()
        return True

    def _render_sheet(
        self,
        palette_key: str,
        facing_right: bool,
        walk_phase: int,
        tail_phase: float,
        pupil_dx: int,
        pupil_dy: int,
        eye_state: str,
        is_hanging: bool,
        is_swatting: bool,
        is_climbing: bool,
        is_peeking: bool,
        overlay: str | None,
        is_vibing: bool,
        is_dangling: bool,
        vy: float = 0.0,
        is_airborne: bool = False,
        is_landing: bool = False,
        is_walking: bool = False,
        is_crouching: bool = False,
    ) -> QPixmap:
        vibing = is_vibing or overlay == "vibing"
        forced_idx: int | None = None
        if is_hanging and "hanging" in self.sheet_tags:
            tag, step = "hanging", int(tail_phase * 2.0)
        elif is_climbing and "climbing" in self.sheet_tags:
            tag, step = "climbing", walk_phase
        elif is_peeking and "peeking" in self.sheet_tags:
            tag, step = "peeking", int(tail_phase * 1.4)
        elif is_crouching and "jump" in self.sheet_tags:
            tag, step, forced_idx = "jump", 0, 0
        elif is_airborne and "jump" in self.sheet_tags:
            tag, step = "jump", 0
            forced_idx = 1 if vy < -5.0 else (2 if vy < -1.5 else (3 if vy <= 1.5 else 4))
        elif is_landing and "jump" in self.sheet_tags:
            tag, step, forced_idx = "jump", 0, 5
        elif is_swatting and "swatting" in self.sheet_tags:
            tag, step = "swatting", int(tail_phase * 3.5)
        elif is_dangling and "dangling" in self.sheet_tags:
            tag, step = "dangling", int(tail_phase * 1.8)
        elif eye_state == "closed_sleep" and "sleep" in self.sheet_tags:
            tag, step = "sleep", int(tail_phase * 2.5)
        elif (eye_state == "squint_happy" or vibing) and "happy" in self.sheet_tags:
            tag, step = "happy", int(tail_phase * 2.2)
        elif is_walking and "walk" in self.sheet_tags:
            tag, step = "walk", walk_phase
        else:
            tag, step = "idle", int(tail_phase * 1.1)

        frames, direction = self.sheet_tags[tag]
        idx = forced_idx if forced_idx is not None else self._frame_index(len(frames), step, direction)
        img = self._remap_frame(palette_key, tag, idx).copy()

        pal = PALETTES.get(palette_key, PALETTES["white_tabby"])
        if tag == "idle" and idx != 5 and (pupil_dx != 0 or pupil_dy != 0):
            dx = -pupil_dx if facing_right else pupil_dx
            py = 9 if pupil_dy < 0 else 10
            p_col, y_col = QColor(pal["P"]), QColor(pal["Y"])
            for ex in (7, 13):
                img.setPixelColor(ex, 10, y_col)
                img.setPixelColor(ex + (1 if dx > 0 else (-1 if dx < 0 else 0)), py, p_col)

        if overlay:
            self._composite_overlay(img, overlay, tail_phase)
        if vibing and overlay not in ("research", "writing_diary", "vibing"):
            self._composite_overlay(img, "vibing", tail_phase)

        if is_dangling:
            padded = QImage(32, 37, QImage.Format.Format_RGBA8888)
            padded.fill(qRgba(0, 0, 0, 0))
            p = QPainter(padded)
            p.drawImage(0, 5, img)
            p.end()
            img = padded

        if facing_right:
            img = img.mirrored(True, False)
        out_h = CAT_SIZE * img.height() // 32
        pm = QPixmap.fromImage(img).scaled(
            CAT_SIZE, out_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation
        )
        if is_climbing:
            pm = pm.transformed(
                QTransform().rotate(-90 if facing_right else 90), Qt.TransformationMode.FastTransformation
            )
        return pm

    def render(
        self,
        palette_key: str = "white_tabby",
        facing_right: bool = False,
        walk_phase: int = 0,
        tail_phase: float = 0.0,
        pupil_dx: int = 0,
        pupil_dy: int = 0,
        eye_state: str = "normal",
        is_hanging: bool = False,
        is_perched: bool = False,
        is_swatting: bool = False,
        is_climbing: bool = False,
        is_peeking: bool = False,
        overlay: str | None = None,
        is_vibing: bool = False,
        is_dangling: bool = False,
        vy: float = 0.0,
        is_airborne: bool = False,
        is_landing: bool = False,
        is_walking: bool = False,
        is_crouching: bool = False,
    ) -> QPixmap:
        if self.sheet_tags:
            return self._render_sheet(
                palette_key=palette_key,
                facing_right=facing_right,
                walk_phase=walk_phase,
                tail_phase=tail_phase,
                pupil_dx=pupil_dx,
                pupil_dy=pupil_dy,
                eye_state=eye_state,
                is_hanging=is_hanging,
                is_swatting=is_swatting,
                is_climbing=is_climbing,
                is_peeking=is_peeking,
                overlay=overlay,
                is_vibing=is_vibing,
                is_dangling=is_dangling,
                vy=vy,
                is_airborne=is_airborne,
                is_landing=is_landing,
                is_walking=is_walking,
                is_crouching=is_crouching,
            )

        pal = PALETTES.get(palette_key, PALETTES["white_tabby"])
        grid_h = 28 if is_dangling else 24
        img = QImage(24, grid_h, QImage.Format.Format_RGBA8888)
        img.fill(qRgba(0, 0, 0, 0))

        def put(x: int, y: int, code: str) -> None:
            if 0 <= x < 24 and 0 <= y < grid_h:
                img.setPixelColor(x, y, QColor(pal[code]))

        for y, row in enumerate(BASE_GRID):
            for x, ch in enumerate(row):
                if ch in pal:
                    put(x, y, ch)

        is_sleep_curl = eye_state == "closed_sleep" and not is_hanging and not is_climbing and not is_peeking

        if is_hanging:
            for x in range(6, 18):
                img.setPixelColor(x, 19, QColor(0, 0, 0, 0))
                img.setPixelColor(x, 20, QColor(0, 0, 0, 0))
                img.setPixelColor(x, 21, QColor(0, 0, 0, 0))
            for lx in (7, 14):
                for dy in range(19, 23):
                    put(lx, dy, "O")
                    put(lx + 1, dy, "C" if dy == 22 else "B")
                    put(lx + 2, dy, "O")
        elif is_climbing:
            lift_x = 7 if walk_phase % 2 == 0 else 13
            for dy in (18, 19, 20):
                put(lift_x, dy, "O")
                put(lift_x + 1, dy, "C")
                put(lift_x + 2, dy, "O")
            img.setPixelColor(lift_x + 1, 21, QColor(0, 0, 0, 0))
        elif is_dangling:
            for x in range(6, 18):
                img.setPixelColor(x, 20, QColor(0, 0, 0, 0))
                img.setPixelColor(x, 21, QColor(0, 0, 0, 0))
            for lx in (6, 12):
                for dy in range(20, 26):
                    put(lx, dy, "O")
                    put(lx + 1, dy, "C" if dy >= 23 else "B")
                    put(lx + 2, dy, "O")
                put(lx + 1, 25, "O")
        elif is_perched and walk_phase == 0:
            put(8, 22, "C")
            put(9, 22, "O")
        elif is_sleep_curl:
            for x in range(6, 17):
                img.setPixelColor(x, 1, QColor(0, 0, 0, 0))
            for cx, code in [(14, "O"), (15, "S"), (16, "B"), (17, "S"), (18, "O")]:
                put(cx, 19, "O")
                put(cx, 20, code)
                put(cx, 21, "O")
        elif walk_phase % 2 == 1:
            put(8, 19, "C")
            put(9, 19, "C")
            img.setPixelColor(8, 21, QColor(0, 0, 0, 0))
            img.setPixelColor(9, 21, QColor(0, 0, 0, 0))

        if is_swatting:
            for sx, sy, code in [(3, 14, "O"), (4, 14, "C"), (5, 14, "C"), (6, 15, "B"), (4, 15, "O"), (5, 15, "O")]:
                put(sx, sy, code)

        vibing = is_vibing or overlay == "vibing"
        for ex in (7, 13):
            if eye_state == "squint_happy" or (vibing and eye_state != "closed_sleep"):
                put(ex, 7, "O")
                put(ex + 1, 7, "O")
                put(ex, 8, "B")
                put(ex + 1, 8, "B")
            elif eye_state == "closed_sleep":
                put(ex, 8, "O")
                put(ex + 1, 8, "O")
            elif overlay == "yawn":
                put(ex, 7, "B")
                put(ex + 1, 7, "B")
                put(ex, 8, "O")
                put(ex + 1, 8, "O")
            else:
                put(ex, 7, "Y")
                put(ex + 1, 7, "Y")
                put(ex, 8, "Y")
                put(ex + 1, 8, "Y")
                px = max(ex, min(ex + 1, ex + (1 if pupil_dx > 0 else 0)))
                py = 8 if pupil_dy > 0 else 7
                put(px, py, "P")

        if is_peeking:
            for y in range(24):
                for x in range(14, 24):
                    img.setPixelColor(x, y, QColor(0, 0, 0, 0))
            for px, py, code in [(12, 13, "O"), (13, 13, "C"), (12, 14, "C"), (13, 14, "O")]:
                put(px, py, code)
        elif not is_sleep_curl:
            if is_dangling:
                swing = int(round(math.sin(tail_phase) * 1.5))
                for step in range(8):
                    cx = max(16, min(22, 17 + (step // 2) + (swing if step >= 3 else 0)))
                    cy = min(27, 19 + step)
                    put(cx - 1, cy, "O")
                    put(cx, cy, "S" if step % 2 == 1 else "B")
                    put(cx + 1, cy, "O")
                put(max(16, min(22, 20 + swing)), 27, "O")
            else:
                tail_offset = int(round(math.sin(tail_phase) * 2.0))
                if is_perched:
                    tail_offset = 2 + int(round(math.sin(tail_phase) * 1.5))
                tx, ty = 18, max(10, min(20, 15 + tail_offset))
                for step in range(4):
                    cx = min(22, tx + step)
                    cy = max(9, min(22, ty - (step // 2 if tail_offset <= 0 else -step // 2)))
                    put(cx, cy - 1, "O")
                    put(cx, cy, "S" if step % 2 == 1 else "B")
                    put(cx, cy + 1, "O")

        overlay_fn = OVERLAY_RENDERERS.get(overlay or "")
        if overlay_fn is not None:
            overlay_fn(img, pal, tail_phase, put)

        if vibing and overlay not in ("research", "writing_diary"):
            ny = max(0, min(2, 1 + int(round(math.sin(tail_phase * 2.2)))))
            for nx, ny_off, col in [
                (20, 0, "#FFD166"), (21, 0, "#FFD166"), (22, 1, "#72EFDD"),
                (20, 1, "#FFD166"),
                (20, 2, "#FFD166"),
                (18, 3, "#72EFDD"), (19, 3, "#72EFDD"), (20, 3, "#FFD166"),
                (18, 4, "#72EFDD"), (19, 4, "#72EFDD"),
            ]:
                if 0 <= ny + ny_off < 24:
                    img.setPixelColor(nx, ny + ny_off, QColor(col))

        if facing_right:
            img = img.mirrored(True, False)
        out_h = int(round(CAT_SIZE * grid_h / 24))
        pm = QPixmap.fromImage(img).scaled(CAT_SIZE, out_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        if is_climbing:
            pm = pm.transformed(QTransform().rotate(-90 if facing_right else 90), Qt.TransformationMode.FastTransformation)
        return pm
