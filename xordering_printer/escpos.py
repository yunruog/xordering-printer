"""Printer bytes for a ticket, per printer type:

    text    ESC/POS text, Chinese as GBK (most receipt printers sold for Chinese-speaking markets)
    image   ESC/POS picture (GS v 0) of the ticket drawn by render.py (ESC/POS printers without GBK)
    star    Star Graphic Mode raster (star.py) of the same picture: TSP100 / TSP143III futurePRNT,
            which have no fonts and no ESC/POS

All of them end with a feed and a cut.
"""
from typing import List

from . import render, star
from .ticket import Line, columns, layout

ESC, GS, FS = b"\x1b", b"\x1d", b"\x1c"
INIT = ESC + b"@"
CHINESE_ON = FS + b"&"  # Chinese (GBK double-byte) character mode
CUT = GS + b"V" + bytes([66, 0])  # feed to the cutter and cut (partial cut where supported)


def _size(n: int) -> bytes:
    big = n >= 2
    return GS + b"!" + bytes([0x11 if big else 0x00]) + FS + b"!" + bytes([0x0C if big else 0x00])


def _align(a: str) -> bytes:
    return ESC + b"a" + bytes([1 if a == "center" else 2 if a == "right" else 0])


def _encode(text: str) -> bytes:
    # ★ and · aren't in every printer's GBK font: use plain ASCII for them.
    return text.replace("★", "*").replace("·", "-").encode("gbk", errors="replace")


def text_bytes(lines: List[Line], paper_width: int = 80) -> bytes:
    out = bytearray(INIT + CHINESE_ON)
    for line in lines:
        if line.kind in ("rule", "double"):
            out += _size(1) + _align("left") + ESC + b"E\x00" + _encode(layout(line, paper_width)[0]) + b"\n"
            continue
        if line.kind == "feed":
            out += b"\n"
            continue
        out += _size(line.size) + ESC + b"E" + bytes([1 if line.bold else 0]) + GS + b"B" + bytes([1 if line.invert else 0])
        # Centering is done by layout() with spaces; the printer aligns left.
        out += _align("left")
        for row in layout(line, paper_width):
            out += _encode(row) + b"\n"
        out += GS + b"B\x00"
    out += _size(1) + ESC + b"E\x00" + ESC + b"d\x03" + CUT
    return bytes(out)


# --- Picture mode (ESC/POS) ------------------------------------------------------------------------

def render_image(lines: List[Line], paper_width: int = 80):
    return render.render(lines, paper_width)


def raster_bytes(img) -> bytes:
    """GS v 0: the picture in bands of at most 128 rows (some printers limit one command's size)."""
    width, height = img.size
    xbytes = (width + 7) // 8
    packed = b"".join(star.raster_lines(img))
    out = bytearray()
    for top in range(0, height, 128):
        band = min(128, height - top)
        out += GS + b"v0" + bytes([0, xbytes & 0xFF, xbytes >> 8, band & 0xFF, band >> 8])
        out += packed[top * xbytes:(top + band) * xbytes]
    return bytes(out)


def image_bytes(lines: List[Line], paper_width: int = 80) -> bytes:
    return picture_bytes(render_image(lines, paper_width))


def picture_bytes(img) -> bytes:
    return INIT + _align("left") + raster_bytes(img) + ESC + b"d\x03" + CUT


# --- Per printer ---------------------------------------------------------------------------------

def kind(printer: dict) -> str:
    r = printer.get("render") or "text"
    return r if r in ("text", "image", "star") else "text"


def paper(printer: dict) -> int:
    return 80 if kind(printer) == "star" else int(printer.get("paperWidth") or 80)


def ticket_bytes(lines: List[Line], printer: dict) -> bytes:
    k = kind(printer)
    if k == "star":
        return star.raster_bytes(render.render(lines, 80))
    if k == "image":
        return image_bytes(lines, paper(printer))
    return text_bytes(lines, paper(printer))


def tickets_bytes(tickets: List[List[Line]], printer: dict) -> bytes:
    """Several tickets in one go (the test page), each cut."""
    return b"".join(ticket_bytes(t, printer) for t in tickets)


def columns_for(printer: dict) -> int:
    return columns(paper(printer))
