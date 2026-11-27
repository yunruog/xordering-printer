"""A pretend network receipt printer for testing without hardware.

Listens on a TCP port (9100 by default) and saves every ticket it receives in --out:

    0001.bin   the raw bytes
    0001.txt   what would be on paper (text mode decoded from GBK; [cut] where it cuts)
    0001.png   the picture, for tickets sent as pictures (needs Pillow)

Two kinds of printer:
    ESC/POS (default)   answers DLE EOT status queries; text (GBK) and GS v 0 pictures
    --star              a Star TSP100 (Star Graphic Mode only): answers ESC ACK SOH with an ASB
                        status and checks every byte against the Star Graphic Mode command spec
                        (decode_star): anything else is a format error, logged and not "printed"

    python tools/mock_printer.py --port 9100 --star --out mock-prints
    python tools/mock_printer.py --port 9101 --paper-out     # answers "out of paper"

Also importable: MockPrinter(port, out).start() / .stop() (tests run several at once).
"""
import argparse
import json
import socket
import struct
import threading
import time
from pathlib import Path
from typing import List, Optional

ESC, GS, FS, DLE = 0x1B, 0x1D, 0x1C, 0x10
STAR_STATUS = b"\x1b\x06\x01"


class StarFormatError(Exception):
    pass


# STAR Graphic Mode Command Specifications: the n values each ESC * r x n NUL command accepts.
STAR_FF_EOT_MODES = {0, 1, 2, 3, 8, 9, 12, 13, 32, 33, 36, 37}
STAR_CUTS = {2: False, 8: True, 9: True, 12: True, 13: True}  # mode -> cuts (default on cutter models: 13)


def decode_star(data: bytes, max_line_bytes: int = 72):
    """Star Graphic Mode bytes -> {"images": [rows of 72 bytes], "cuts", "statusRequests", "commands"}.

    Strict: every byte must belong to a command of the spec, numbers in ESC * r … NUL commands are
    ASCII digits ended by NUL and within range, raster lines (b n1 n2 data) only inside raster mode
    and at most `max_line_bytes` long, the document ends with ESC FF NUL (or raster mode is quit,
    which ends it the same way) and raster mode is quit at the end. Raises StarFormatError.
    """
    i, n = 0, len(data)
    raster = False
    ff_mode = 13
    rows: list = []
    images = []
    cuts = 0
    status_requests = 0
    commands = []

    def number(j: int, name: str):
        end = data.find(b"\x00", j)
        if end < 0:
            raise StarFormatError(f"{name} at {j}: no NUL terminator")
        digits = data[j:end]
        if not digits or not digits.isdigit() or len(digits) > 5:
            raise StarFormatError(f"{name} at {j}: n must be ASCII decimal digits, got {digits!r}")
        return int(digits), end + 1

    def end_document(how: str) -> None:
        nonlocal rows, cuts
        if rows:
            images.append(rows)
            rows = []
            if STAR_CUTS.get(ff_mode, False):
                cuts += 1
        commands.append(how)

    while i < n:
        if data.startswith(STAR_STATUS, i):
            status_requests += 1
            i += 3
            continue
        b = data[i]
        if b == ord("b") and raster:
            if i + 3 > n:
                raise StarFormatError(f"raster line at {i}: truncated header")
            k = data[i + 1] + data[i + 2] * 256
            if not 1 <= k <= max_line_bytes:
                raise StarFormatError(f"raster line at {i}: {k} bytes (allowed 1..{max_line_bytes})")
            if i + 3 + k > n:
                raise StarFormatError(f"raster line at {i}: {k} bytes announced, {n - i - 3} left")
            rows.append(bytes(data[i + 3:i + 3 + k]).ljust(max_line_bytes, b"\x00"))
            i += 3 + k
            continue
        if b == ESC and data.startswith(b"\x1b\x0c\x00", i):
            if not raster:
                raise StarFormatError(f"ESC FF NUL at {i} outside raster mode")
            end_document("FF")
            i += 3
            continue
        if b == ESC and data.startswith(b"\x1b\x0c\x04", i):
            if not raster:
                raise StarFormatError(f"ESC FF EOT at {i} outside raster mode")
            end_document("EOT")
            i += 3
            continue
        if b == ESC and data.startswith(b"\x1b*r", i) and i + 3 < n:
            c = chr(data[i + 3])
            j = i + 4
            if c == "R":
                ff_mode = 13
                commands.append("init")
            elif c == "A":
                raster = True
                commands.append("enter")
            elif c == "B":
                if not raster:
                    raise StarFormatError(f"ESC * r B at {i} outside raster mode")
                end_document("quit")
                raster = False
            elif c == "C":
                rows = []
                commands.append("clear")
            elif c in "PQFEY":
                v, j = number(j, f"ESC * r {c}")
                if c == "P" and not (v == 0 or 200 <= v <= 64000):
                    raise StarFormatError(f"page length {v} (0 or 200..64000)")
                if c == "Q" and v > 2:
                    raise StarFormatError(f"print quality {v} (0..2)")
                if c in "FE" and v not in STAR_FF_EOT_MODES:
                    raise StarFormatError(f"ESC * r {c} mode {v} not in the spec")
                if c == "F":
                    if rows:
                        raise StarFormatError("FF mode changed with raster data in the buffer (ignored by the printer)")
                    ff_mode = 13 if v == 0 else v
                if c == "Y":
                    if not raster:
                        raise StarFormatError(f"ESC * r Y at {i} outside raster mode")
                    rows.extend([b"\x00" * max_line_bytes] * v)
                commands.append(f"{c}{v}")
            elif c == "m" and i + 6 < n and chr(data[i + 4]) in "lr" and data[i + 6] == 0:
                j = i + 7
                commands.append(f"margin {chr(data[i + 4])}{data[i + 5]}")
            else:
                raise StarFormatError(f"unknown command ESC * r {c!r} at {i}")
            i = j
            continue
        raise StarFormatError(f"byte 0x{b:02x} at {i} is not a Star Graphic Mode command (raster mode {'on' if raster else 'off'})")
    if raster:
        raise StarFormatError("raster mode not quit at the end (ESC * r B)")
    if rows:
        raise StarFormatError("raster data left in the buffer")
    return {"images": images, "cuts": cuts, "statusRequests": status_requests, "commands": commands}


def star_png(rows, path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    img = Image.frombytes("1", (len(rows[0]) * 8, len(rows)), bytes(b ^ 0xFF for r in rows for b in r))
    img.save(path)
    return True


def decode(data: bytes):
    """ESC/POS bytes -> (text, [raster images as (width_bytes, height, bytes)], cuts)."""
    out: List[str] = []
    rasters = []
    cuts = 0
    chinese = False
    i = 0
    buf = bytearray()

    def flush() -> None:
        if buf:
            out.append(bytes(buf).decode("gbk" if chinese else "cp437", errors="replace"))
            buf.clear()

    n = len(data)
    while i < n:
        b = data[i]
        if b == ESC and i + 1 < n:
            flush()
            c = data[i + 1]
            if c == ord("@"):
                i += 2
            elif c in (ord("a"), ord("E"), ord("d"), ord("!"), ord("t"), ord("-"), ord("M"), ord("J"), ord("G")):
                if c == ord("d"):
                    out.append("\n" * data[i + 2] if i + 2 < n else "")
                i += 3
            else:
                i += 2
        elif b == GS and i + 1 < n:
            flush()
            c = data[i + 1]
            if c == ord("V"):
                m = data[i + 2] if i + 2 < n else 0
                i += 4 if m in (65, 66) else 3
                cuts += 1
                out.append("\n[cut]\n")
            elif c == ord("v") and i + 7 < n and data[i + 2] == ord("0"):
                xb = data[i + 4] | (data[i + 5] << 8)
                h = data[i + 6] | (data[i + 7] << 8)
                start = i + 8
                rasters.append((xb, h, data[start:start + xb * h]))
                i = start + xb * h
            elif c in (ord("!"), ord("B"), ord("L"), ord("W")):
                i += 3 if c in (ord("!"), ord("B")) else 4
            else:
                i += 3
        elif b == FS and i + 1 < n:
            flush()
            c = data[i + 1]
            if c == ord("&"):
                chinese = True
                i += 2
            elif c == ord("."):
                chinese = False
                i += 2
            elif c == ord("!"):
                i += 3
            else:
                i += 2
        elif b == DLE and i + 2 < n and data[i + 1] == 0x04:
            i += 3
        else:
            buf.append(b)
            i += 1
    flush()
    return "".join(out), rasters, cuts


def raster_png(rasters, path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    if not rasters:
        return False
    width = max(xb for xb, _, _ in rasters) * 8
    height = sum(h for _, h, _ in rasters)
    img = Image.new("1", (width, height), 1)
    px = img.load()
    y0 = 0
    for xb, h, bits in rasters:
        for y in range(h):
            for x in range(xb * 8):
                if bits[y * xb + x // 8] & (0x80 >> (x % 8)):
                    px[x, y0 + y] = 0
        y0 += h
    img.save(path)
    return True


class MockPrinter:
    def __init__(self, port: int = 9100, out: str = "mock-prints", host: str = "127.0.0.1", paper_out: bool = False, quiet: bool = False, star: bool = False,
                 exclusive: bool = False):
        self.port, self.host = port, host
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.paper_out = paper_out
        self.cover_open = False
        self.paper_low = False
        self.quiet = quiet
        self.star = star
        # Busy with another device (the POS): until then, connections are accepted and dropped unread.
        self.busy_until = 0.0
        self.busy_hits = 0
        # Like a Star LAN printer: one connection at a time. While one is open (the POS holding the
        # printer), others are reset at once.
        self.exclusive = exclusive
        self.active = 0
        self.format_errors: List[str] = []
        self.status_requests = 0
        self.tickets: List[dict] = []
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> "MockPrinter":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(8)
        self._sock = s
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                # Linux keeps a closed socket listening while accept() blocks on it: shut it down first.
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _accept(self) -> None:
        while self._running and self._sock:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _status(self, n: int) -> int:
        if n == 1:
            return 0x12 | (0x08 if self.paper_out else 0)
        if n == 2:
            return 0x12 | (0x20 if self.paper_out else 0)
        if n == 4:
            return 0x12 | (0x60 if self.paper_out else 0)
        return 0x12

    def _asb(self) -> bytes:
        """A 9-byte Star ASB: header 1 (0x23 = 9 bytes), header 2, printer status 1..7."""
        ps1 = (0x20 if self.cover_open else 0) | (0x08 if (self.cover_open or self.paper_out) else 0)
        ps4 = (0x08 if self.paper_out else 0) | (0x04 if self.paper_low else 0)
        return bytes([0x23, 0x86, ps1, 0x00, 0x00, ps4, 0x00, 0x00, 0x00])

    def _handle(self, conn: socket.socket) -> None:
        with self._lock:
            taken = self.exclusive and self.active > 0
            if not taken:
                self.active += 1
        if taken:
            with self._lock:
                self.busy_hits += 1
            try:
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            except OSError:
                pass
            conn.close()  # reset: the printer is taken
            return
        try:
            self._serve(conn)
        finally:
            with self._lock:
                self.active -= 1

    def _serve(self, conn: socket.socket) -> None:
        data = bytearray()
        conn.settimeout(30 if self.exclusive else 3)
        if time.time() < self.busy_until:
            with self._lock:
                self.busy_hits += 1
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
            return
        with conn:
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if self.star:
                        # ESC ACK SOH is the last thing a client sends before waiting for the answer.
                        if chunk.endswith(STAR_STATUS):
                            self.status_requests += 1
                            conn.sendall(self._asb())
                        continue
                    # Answer real-time status queries as they arrive.
                    j = 0
                    while True:
                        k = chunk.find(b"\x10\x04", j)
                        if k < 0 or k + 2 >= len(chunk):
                            break
                        conn.sendall(bytes([self._status(chunk[k + 2])]))
                        j = k + 3
            except (socket.timeout, OSError):
                pass
        body = bytes(data)
        if self.star:
            self._star_ticket(body)
            return
        # A connection that only asked for status (or sent nothing) isn't a ticket.
        stripped = body.replace(b"\x10\x04\x01", b"").replace(b"\x10\x04\x02", b"").replace(b"\x10\x04\x04", b"")
        if not stripped or self.paper_out:
            return
        text, rasters, cuts = decode(body)
        with self._lock:
            n = len(self.tickets) + 1
            base = self.out / f"{n:04d}"
            base.with_suffix(".bin").write_bytes(body)
            base.with_suffix(".txt").write_text(text, "utf-8")
            png = raster_png(rasters, base.with_suffix(".png"))
            ticket = {"n": n, "at": time.time(), "bytes": len(body), "text": text, "image": png, "cuts": cuts}
            self.tickets.append(ticket)
            (self.out / "index.json").write_text(json.dumps([{k: v for k, v in t.items() if k != "text"} for t in self.tickets], indent=1), "utf-8")
        if not self.quiet:
            print(f"--- ticket {n} on :{self.port} ({len(body)} bytes, {'image' if png else 'text'}, {cuts} cut) ---")
            print(text if not png else f"[picture saved to {base.with_suffix('.png')}]")

    def _star_ticket(self, body: bytes) -> None:
        if not body.replace(STAR_STATUS, b""):
            return  # only status queries (a connection test)
        try:
            doc = decode_star(body)
        except StarFormatError as e:
            with self._lock:
                self.format_errors.append(str(e))
            if not self.quiet:
                print(f"--- :{self.port} FORMAT ERROR: {e} ---")
            return
        if self.paper_out or self.cover_open:
            return
        with self._lock:
            for rows in doc["images"]:
                n = len(self.tickets) + 1
                base = self.out / f"{n:04d}"
                base.with_suffix(".bin").write_bytes(body)
                png = star_png(rows, base.with_suffix(".png"))
                self.tickets.append({"n": n, "at": time.time(), "bytes": len(body), "text": "", "image": png, "cuts": 1 if doc["cuts"] else 0,
                                     "rows": rows, "star": True})
            (self.out / "index.json").write_text(json.dumps([{k: v for k, v in t.items() if k not in ("text", "rows")} for t in self.tickets], indent=1), "utf-8")
        if not self.quiet:
            print(f"--- {len(doc['images'])} Star ticket(s) on :{self.port} ({len(body)} bytes, {doc['cuts']} cut) ---")

    def image(self, n: int):
        """Ticket n (1-based) as a PIL picture (Star tickets and ESC/POS pictures)."""
        from PIL import Image

        t = self.tickets[n - 1]
        if t.get("rows"):
            rows = t["rows"]
            return Image.frombytes("1", (len(rows[0]) * 8, len(rows)), bytes(b ^ 0xFF for r in rows for b in r))
        return Image.open(self.out / f"{t['n']:04d}.png")

    def count(self, needle: str) -> int:
        with self._lock:
            return sum(1 for t in self.tickets if needle in t["text"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=9100)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--out", default="mock-prints")
    p.add_argument("--paper-out", action="store_true")
    p.add_argument("--star", action="store_true", help="a Star TSP100 (Star Graphic Mode raster only)")
    p.add_argument("--exclusive", action="store_true", help="one connection at a time, like a Star LAN printer")
    a = p.parse_args()
    MockPrinter(a.port, a.out, a.host, a.paper_out, star=a.star, exclusive=a.exclusive).start()
    print(f"Mock printer listening on {a.host}:{a.port}, saving tickets to {a.out}/ (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
