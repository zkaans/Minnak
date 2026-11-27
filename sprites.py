"""Katman 1a: Piksel Kedi Çizici, Renk Paletleri ve Animasyon Katmanları (Overlays)."""
import math
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QMovie, QPixmap, QTransform, qRgba

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
        "O": "#2B2D42", "B": "#F8F9FA", "C": "#FFFFFF", "S": "#6C757D",
        "E": "#FF8FA3", "N": "#FF8FA3", "Y": "#E9ECEF", "P": "#212529",
    },
    "void_black": {
        "O": "#0D0D0F", "B": "#1A1A1D", "C": "#2E2E33", "S": "#121214",
        "E": "#3A3A40", "N": "#555560", "Y": "#FFD166", "P": "#111111",
    },
    "orange_tabby": {
        "O": "#3D2616", "B": "#F4A261", "C": "#FFF3E6", "S": "#E76F51",
        "E": "#FF8FA3", "N": "#E76F51", "Y": "#2A9D8F", "P": "#111111",
    },
    "tuxedo": {
        "O": "#101114", "B": "#1D1E22", "C": "#FFFFFF", "S": "#2B2D33",
        "E": "#FF8FA3", "N": "#FF8FA3", "Y": "#72EFDD", "P": "#111111",
    },
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
    """24x24 pikseli 64x64 crisp-pixel'e çizer; sprites/ altında özel görsel varsa onu kullanır."""

    def __init__(self, sprites_dir: Path | None = None):
        self.sprites_dir = sprites_dir or (Path(__file__).parent / "sprites")
        self.custom_pix: QPixmap | None = None
        self.custom_movie: QMovie | None = None
        if self.sprites_dir.exists():
            gif = next(self.sprites_dir.glob("*.gif"), None)
            png = next(self.sprites_dir.glob("*.png"), None)
            if gif:
                self.custom_movie = QMovie(str(gif))
                self.custom_movie.start()
            elif png:
                self.custom_pix = QPixmap(str(png))

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
    ) -> QPixmap:
        if self.custom_movie and self.custom_movie.isValid():
            pm = self.custom_movie.currentPixmap()
            if not pm.isNull():
                if facing_right:
                    pm = QPixmap.fromImage(pm.toImage().mirrored(True, False))
                return pm.scaled(CAT_SIZE, CAT_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        if self.custom_pix and not self.custom_pix.isNull():
            pm = QPixmap.fromImage(self.custom_pix.toImage().mirrored(facing_right, False))
            return pm.scaled(CAT_SIZE, CAT_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)

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
