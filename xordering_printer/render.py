"""The ticket as a black-and-white picture, exactly as it goes to the printer.

Used by the picture modes (Star raster for TSP100-class printers, ESC/POS GS v 0 for others) and
by the preview (the same picture as a PNG). The font ships with the program (Noto Sans SC
SemiBold, SIL Open Font License, fonts/OFL.txt), so Chinese in customer notes prints on any
computer and the picture is the same everywhere.

Width: 576 dots for 80 mm paper (72 mm printable at 203 dpi), 384 for 58 mm.
"""
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from .ticket import LEAD_COLS, Line, width as text_width

FONT_FILE = Path(__file__).resolve().parent / "fonts" / "NotoSansSC-SemiBold.ttf"
SYSTEM_FONTS = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
MARGIN = 6
# Font size (px) per Line.size: normal (≈46 characters a line), large, largest (shrunk to fit one line).
SIZES = {1: 25, 2: 38, 3: 54}
MIN_HEADLINE = 34
MIN_BANNER = 28  # inverted banners shrink to one line down to this size
BANNER_GAP = 6  # white space under an inverted banner, so two banners don't merge
THRESHOLD = 150  # anti-aliased grey darker than this prints black


def dots_for(paper_width: int) -> int:
    return 384 if int(paper_width or 80) == 58 else 576


def font_path() -> Optional[str]:
    if FONT_FILE.exists():
        return str(FONT_FILE)
    windir = os.environ.get("WINDIR")
    for c in SYSTEM_FONTS:
        if windir and sys.platform == "win32":
            c = c.replace(r"C:\Windows", windir)
        if os.path.exists(c):
            return c
    return None


@lru_cache(maxsize=32)
def font(px: int):
    from PIL import ImageFont

    path = font_path()
    return ImageFont.truetype(path, px) if path else ImageFont.load_default()


def _scale(paper_width: int) -> float:
    return dots_for(paper_width) / 576


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    word = ""
    for ch in text:
        if ch == " " or text_width(ch) == 2:
            if word:
                out.append(word)
                word = ""
            out.append(ch)
        else:
            word += ch
    if word:
        out.append(word)
    return out


def wrap_px(text: str, f, max_w: float) -> List[str]:
    """Greedy wrap by the font's own widths; Latin words stay whole when they fit."""
    rows: List[str] = []
    for para in (text or "").replace("\r", "").split("\n"):
        line = ""
        for tok in _tokens(para):
            if f.getlength(line + tok) <= max_w:
                line += tok
                continue
            if line.strip():
                rows.append(line.rstrip())
            line = "" if tok == " " else tok
            while f.getlength(line) > max_w and len(line) > 1:  # a word longer than the line
                cut = 1
                while cut < len(line) and f.getlength(line[: cut + 1]) <= max_w:
                    cut += 1
                rows.append(line[:cut])
                line = line[cut:]
        rows.append(line.rstrip())
    return rows or [""]


def _font_for(line: Line, inner: float, scale: float):
    px = max(10, int(SIZES.get(line.size, SIZES[1]) * scale))
    if line.size >= 3 or line.invert:  # the headline and banners: as big as fits on one line
        floor = int((MIN_HEADLINE if line.size >= 3 else MIN_BANNER) * scale)
        while px > floor and font(px).getlength(line.text) > inner:
            px -= 2
    return font(px), px


def _rows(line: Line, dots: int, scale: float) -> Tuple[object, int, List[Tuple[str, float, Optional[str]]]]:
    """(font, px, [(text, x, right_text_or_None)]) for one Line."""
    inner = dots - 2 * MARGIN - (16 if line.invert else 0)
    f, px = _font_for(line, inner, scale)
    lead_w = font(max(10, int(SIZES[1] * scale))).getlength("0" * (LEAD_COLS - 1)) + 10 * scale if (line.lead or line.indent) else 0
    x0 = MARGIN + (8 if line.invert else 0) + lead_w
    right_w = f.getlength(line.right) if line.right else 0
    rows = wrap_px(line.text, f, inner - lead_w)
    out: List[Tuple[str, float, Optional[str]]] = []
    for i, r in enumerate(rows):
        if line.align == "center" and not line.right:
            out.append((r, (dots - f.getlength(r)) / 2, None))
        else:
            out.append((r, x0, None))
    if line.right:
        last, x, _ = out[-1]
        if f.getlength(last) + 14 * scale + right_w <= inner - lead_w:
            out[-1] = (last, x, line.right)
        else:
            out.append(("", x0, line.right))
    return f, px, out


def render(lines: List[Line], paper_width: int = 80):
    """The ticket as a mode "1" picture, `dots` wide (576 for 80 mm)."""
    from PIL import Image, ImageDraw

    dots = dots_for(paper_width)
    scale = _scale(paper_width)
    plan = []  # (kind, height, payload)
    for line in lines:
        if line.kind == "feed":
            plan.append(("feed", int(16 * scale), None))
        elif line.kind == "rule":
            plan.append(("rule", int(18 * scale), None))
        elif line.kind == "double":
            plan.append(("double", int(22 * scale), None))
        else:
            f, px, rows = _rows(line, dots, scale)
            row_h = int(px * 1.32)
            pad = int(6 * scale) if line.invert else 0
            gap = int(BANNER_GAP * scale) if line.invert else 0
            plan.append(("text", row_h * len(rows) + 2 * pad + gap, (line, f, px, rows, row_h, pad)))
    height = sum(h for _, h, _ in plan) + int(12 * scale)
    img = Image.new("L", (dots, height), 255)
    draw = ImageDraw.Draw(img)
    y = 0
    for kind, h, payload in plan:
        if kind == "rule":
            mid = y + h // 2
            for x in range(MARGIN, dots - MARGIN, 12):
                draw.rectangle([x, mid - 1, min(x + 7, dots - MARGIN), mid], fill=0)
        elif kind == "double":
            mid = y + h // 2
            draw.rectangle([MARGIN, mid - 4, dots - MARGIN, mid - 3], fill=0)
            draw.rectangle([MARGIN, mid + 2, dots - MARGIN, mid + 3], fill=0)
        elif kind == "text":
            line, f, px, rows, row_h, pad = payload
            ink = 0
            if line.invert:
                draw.rectangle([MARGIN, y, dots - MARGIN - 1, y + h - 1 - int(BANNER_GAP * scale)], fill=0)
                ink = 255
            top = font(px).getbbox("国Ag")
            ty0 = y + pad + (row_h - (top[3] - top[1])) / 2 - top[1]
            for i, (text, x, right) in enumerate(rows):
                ty = ty0 + i * row_h
                if i == 0 and line.lead:
                    draw.text((MARGIN + (8 if line.invert else 0), ty), line.lead, font=f, fill=ink)
                    if line.bold:
                        draw.text((MARGIN + (8 if line.invert else 0) + 1, ty), line.lead, font=f, fill=ink)
                for dx in ((0, 1) if line.bold else (0,)):
                    if text:
                        draw.text((x + dx, ty), text, font=f, fill=ink)
                    if right:
                        rx = dots - MARGIN - (8 if line.invert else 0) - f.getlength(right)
                        draw.text((rx + dx, ty), right, font=f, fill=ink)
        y += h
    return trim_bottom(img.point(lambda v: 0 if v < THRESHOLD else 255, mode="1"))


def trim_bottom(img, keep: int = 4):
    """Cuts the blank rows under the last ink (no paper fed for nothing before the cut)."""
    from PIL import ImageOps

    box = ImageOps.invert(img.convert("L")).getbbox()
    if not box:
        return img
    bottom = min(img.size[1], box[3] + keep)
    return img.crop((0, 0, img.size[0], bottom)) if bottom < img.size[1] else img


def render_many(tickets: List[List[Line]], paper_width: int = 80) -> list:
    return [render(t, paper_width) for t in tickets]


def png_bytes(img) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
