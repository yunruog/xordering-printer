"""Star Graphic Mode (TSP100 / TSP143III futurePRNT): the ticket as raster lines, then cut.

These printers have no fonts at all: they only print pictures sent in Star's raster commands (ESC/POS
commands, GBK text and even ESC/POS pictures (GS v 0) print nothing). Bytes, per the STAR Graphic Mode
Command Specifications (numbers in ESC * r … NUL commands are ASCII decimal digits):

    ESC * r R               1B 2A 72 52        initialize raster mode
    ESC * r A               1B 2A 72 41        enter raster mode
    ESC * r P 0 NUL         1B 2A 72 50 30 00  continuous paper (page length 0)
    ESC * r F 13 NUL        1B 2A 72 46 31 33 00   FF mode: feed to the cutter, partial cut
    b n1 n2 d1…dk           62 n1 n2 …         one raster line, k = n1 + n2*256 bytes (≤ 72), MSB = left, 1 = black
    ESC * r Y n NUL         1B 2A 72 59 …  00  n blank lines (moves down n dots)
    ESC FF NUL              1B 0C 00           end of document: print, feed and cut (FF mode)
    ESC * r B               1B 2A 72 42        quit raster mode

Status: ESC ACK SOH (1B 06 01) asks for the automatic status (ASB): header 1, header 2, then
printer status bytes (status 1 bit 5 cover open, bit 3 offline; status 4 bit 3 paper empty,
bit 2 paper near end). Printers that don't answer are printed to anyway.
"""
from typing import Dict, List, Optional

ESC = b"\x1b"
INIT = ESC + b"*rR"
ENTER = ESC + b"*rA"
QUIT = ESC + b"*rB"
PAGE_CONTINUOUS = ESC + b"*rP0\x00"
FF_CUT = ESC + b"*rF13\x00"  # FormFeed + cut feed + partial cut (the cutter models' default)
FORM_FEED = ESC + b"\x0c\x00"
STATUS_REQUEST = ESC + b"\x06\x01"
MAX_LINE_BYTES = 72  # 576 dots

# Header 1 -> total length of the ASB reply (STAR Graphic Mode spec, appendix: status).
ASB_LENGTH = {0x0F: 7, 0x21: 8, 0x23: 9, 0x25: 10, 0x27: 11, 0x29: 12, 0x2B: 13, 0x2D: 14, 0x2F: 15}


def _num(prefix: bytes, n: int) -> bytes:
    return prefix + str(int(n)).encode("ascii") + b"\x00"


def raster_lines(img) -> List[bytes]:
    """Each row of a mode "1" picture as packed bits, 1 = black (left pixel = most significant bit)."""
    img = img.convert("1")
    w, h = img.size
    if w > MAX_LINE_BYTES * 8:
        raise ValueError(f"picture is {w} dots wide; Star 80 mm printers take at most {MAX_LINE_BYTES * 8}")
    xbytes = (w + 7) // 8
    raw = img.tobytes()  # 1 = white in PIL
    rows = []
    for y in range(h):
        row = bytearray(b ^ 0xFF for b in raw[y * xbytes:(y + 1) * xbytes])
        if w % 8:
            row[-1] &= (0xFF << (8 - w % 8)) & 0xFF  # padding bits past the edge stay white
        rows.append(bytes(row))
    return rows


def raster_bytes(img, cut: bool = True) -> bytes:
    """A whole ticket in Star Graphic Mode."""
    out = bytearray(INIT + ENTER + PAGE_CONTINUOUS + FF_CUT)
    blank = 0
    for row in raster_lines(img):
        data = row.rstrip(b"\x00")
        if not data:
            blank += 1
            continue
        if blank:
            out += _num(ESC + b"*rY", blank)
            blank = 0
        k = len(data)
        out += b"b" + bytes([k & 0xFF, k >> 8]) + data
    if blank:
        out += _num(ESC + b"*rY", blank)
    if cut:
        out += FORM_FEED
    out += QUIT
    return bytes(out)


def parse_status(data: bytes) -> Optional[Dict[str, bool]]:
    """An ASB reply as flags, or None when it doesn't look like one."""
    if len(data) < 2:
        return None
    expected = ASB_LENGTH.get(data[0])
    if expected is None or len(data) < min(expected, 6):
        return None
    ps = data[2:]
    s1 = ps[0] if len(ps) > 0 else 0
    s4 = ps[3] if len(ps) > 3 else 0
    return {
        "coverOpen": bool(s1 & 0x20),
        "offline": bool(s1 & 0x08),
        "paperEmpty": bool(s4 & 0x08),
        "paperLow": bool(s4 & 0x04),
    }
