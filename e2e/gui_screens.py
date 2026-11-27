"""Screenshots of the printer windows (run on Windows CI; saved to e2e-output/screens/).

    python e2e/gui_screens.py

1 the first run: no printer yet, the printer window opens and looks for printers for real
2 Tsunami Sushi's case (made up): a Star on USB, the LAN Star held by qMenu, an office printer
3 one receipt printer only: picked and filled in by itself
4 the diagnostics report
5 the main window with a printer that keeps failing (red warning)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ["XORDERING_PRINTER_HOME"] = tempfile.mkdtemp(prefix="xop-screens-")
OUT = ROOT / "e2e-output" / "screens"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
OUT.mkdir(parents=True, exist_ok=True)

import json  # noqa: E402
import tkinter as tk  # noqa: E402

from PIL import ImageGrab  # noqa: E402

from tests.test_discover import tsunami  # noqa: E402
from xordering_printer import config, discover, gui, install  # noqa: E402
from xordering_printer.paths import status_path  # noqa: E402

install.ensure_running = lambda: None


def grab(win, name):
    win.update()
    time.sleep(0.4)
    win.update()
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    ImageGrab.grab(bbox=(x - 8, y - 32, x + w + 8, y + h + 8), all_screens=True).save(OUT / name)
    print("saved", OUT / name, w, h, flush=True)


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.05)


config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64,
              restaurant={"name": {"zh": "海啸寿司", "en": "Tsunami Sushi"}}, printers=[])
root = tk.Tk()
app = gui.App(root)
pump(root, 2.5)
assert app.dialog is not None, "the printer window should open by itself on the first run"
grab(app.dialog.top, "1-first-run-searching.png")
deadline = time.time() + 90
while app.dialog.searching and time.time() < deadline:
    pump(root, 0.5)
grab(app.dialog.top, "1b-first-run-found.png")
print("real search:", json.dumps([[c["name"], discover.address_text(c), discover.source_text(c), c["score"]] for c in app.dialog.candidates], ensure_ascii=False))
app.dialog.top.destroy()

res = tsunami()
res.update(version="screens", at=time.time(), computer="QMENU-TABLET", errors={}, networks=["192.168.1.0/24"],
           interfaces=[{"name": "Wi-Fi", "ip": "192.168.1.23", "prefix": 24, "network": "192.168.1.0/24", "scan": "192.168.1.0/24", "skipped": ""}])
new = {"name": "后厨 Kitchen 1", "host": "", "port": 9100, "paperWidth": 80, "render": "image", "enabled": True, "connection": "windows"}
d = gui.PrinterDialog(app, dict(new), autodetect=False)
d.show_found(res, auto=True)
d.found.selection_set("1")
d.pick_found()
grab(d.top, "2-tsunami-several.png")
d.top.destroy()

one = dict(res, candidates=discover.merge(dict(res, connections=[], seen=[], windowsPrinters=res["windowsPrinters"][:1], usb=[], scan={}, arp=[], identify={})))
d = gui.PrinterDialog(app, dict(new), autodetect=False)
d.show_found(one, auto=True)
grab(d.top, "3-one-printer-auto-filled.png")
r = gui.ReportWindow(app, d.top, gui.setup.diagnostics(res))
grab(r.top, "4-diagnostics.png")
r.top.destroy()
d.top.destroy()

config.update(printers=[{"id": "p1", "name": "后厨 Kitchen 1", "host": "192.168.1.50", "port": 9100, "paperWidth": 80, "render": "star", "enabled": True}])
status_path().write_text(json.dumps({"updatedAt": time.time(), "state": "ok", "queue": {"queued": 2, "printed": 14},
                                     "printers": {"p1": {"ok": False, "stuck": True, "failingSince": time.time() - 420, "code": "in_use",
                                                         "error": "打印机正被 qMenu.exe 使用，订单在排队，会自动重试 The printer is in use by qMenu.exe: the ticket waits and is retried"}}}), "utf-8")
app.render()
pump(root, 1)
grab(root, "5-stuck-warning.png")
root.destroy()
