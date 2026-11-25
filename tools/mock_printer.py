"""A pretend network receipt printer for testing without hardware.

Listens on a TCP port (9100 by default), answers ESC/POS status queries (DLE EOT) like a real
printer, and saves every ticket it receives in --out:

    0001.bin   the raw bytes
    0001.txt   what would be on paper (text mode decoded from GBK; [cut] where it cuts)
    0001.png   the picture, for tickets sent in image mode (needs Pillow)

    python tools/mock_printer.py --port 9100 --out mock-prints
    python tools/mock_printer.py --port 9101 --paper-out     # answers "out of paper"

Also importable: MockPrinter(port, out).start() / .stop() (tests run several at once).
"""
import argparse
import json
import socket
import threading
import time
from pathlib import Path
from typing import List, Optional

ESC, GS, FS, DLE = 0x1B, 0x1D, 0x1C, 0x10


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
    def __init__(self, port: int = 9100, out: str = "mock-prints", host: str = "127.0.0.1", paper_out: bool = False, quiet: bool = False):
        self.port, self.host = port, host
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.paper_out = paper_out
        self.quiet = quiet
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

    def _handle(self, conn: socket.socket) -> None:
        data = bytearray()
        conn.settimeout(3)
        with conn:
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
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

    def count(self, needle: str) -> int:
        with self._lock:
            return sum(1 for t in self.tickets if needle in t["text"])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=9100)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--out", default="mock-prints")
    p.add_argument("--paper-out", action="store_true")
    a = p.parse_args()
    MockPrinter(a.port, a.out, a.host, a.paper_out).start()
    print(f"Mock printer listening on {a.host}:{a.port}, saving tickets to {a.out}/ (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
