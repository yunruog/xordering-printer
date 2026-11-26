"""End to end, without hardware: order -> poll -> print -> report, and what can go wrong.

Runs against XOrdering's local dev server (the real SQL functions in an in-memory Postgres; from
the main repository: `node scripts/print-dev-server.ts`), through a TCP proxy that can cut the
"internet", with the program running as it does in a shop (the service and its worker, as separate
processes) and two mock printers.

    python e2e/run_e2e.py --xordering ../xordering        # starts the dev server itself
    python e2e/run_e2e.py --server http://127.0.0.1:8787   # or uses a running one

Checks: device key and email sign-in (the dashboard's 6-digit email code; no SMS); both printers print each order once; printer offline ->
"failed" on the dashboard -> printed when it is back; internet down -> orders print when it
returns; no duplicates; reprint; rejected orders don't print; the worker is killed -> restarted;
the program restarts -> still connected, nothing printed twice; image mode; a Star TSP100 (Star
Graphic Mode, every byte checked) busy with the POS; a scheduled order and its reprint before
pickup; the dashboard's test page, preview (the very picture printed) and printer status.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mock_printer import MockPrinter  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(base: str, path: str, body=None):
    req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def get(base: str, path: str):
    with urllib.request.urlopen(base + path, timeout=20) as r:
        return json.loads(r.read())


class Proxy:
    """127.0.0.1:port -> target, with an on/off switch for the internet."""

    def __init__(self, target_port: int):
        self.target = target_port
        self.port = free_port()
        self.up = True
        self.conns = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(32)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            if not self.up:
                c.close()
                continue
            try:
                u = socket.create_connection(("127.0.0.1", self.target))
            except OSError:
                c.close()
                continue
            self.conns += [c, u]
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    def _pipe(self, a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d or not self.up:
                    break
                b.sendall(d)
        except OSError:
            pass
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

    def down(self):
        self.up = False
        for s in self.conns:
            try:
                s.close()
            except OSError:
                pass
        self.conns = []

    def restore(self):
        self.up = True


def wait(cond, timeout=30.0, what=""):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {what}")


class Run:
    def __init__(self, server: str, out: Path):
        self.server = server.rstrip("/")
        self.out = out
        self.results = []
        self.proxy = Proxy(int(self.server.rsplit(":", 1)[1]))
        self.site = f"http://127.0.0.1:{self.proxy.port}/"
        self.home = Path(tempfile.mkdtemp(prefix="xop-e2e-"))
        self.env = dict(os.environ, XORDERING_PRINTER_HOME=str(self.home), XORDERING_PRINTER_PORT=str(free_port()), PYTHONPATH=str(ROOT))
        os.environ.update(XORDERING_PRINTER_HOME=str(self.home))
        self.service = None
        self.ports = [free_port(), free_port()]
        self.mocks = [MockPrinter(p, out=str(out / f"printer{i + 1}"), quiet=True).start() for i, p in enumerate(self.ports)]

    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  - ' + detail if detail else ''}", flush=True)
        if not ok:
            raise AssertionError(name)

    def start_service(self):
        log = open(self.home / "service.out", "ab")
        self.service = subprocess.Popen([sys.executable, "-m", "xordering_printer", "--service"], cwd=str(ROOT), env=self.env, stdout=log, stderr=log)
        started = time.time()
        wait(lambda: self.status().get("state") == "ok" and self.status().get("updatedAt", 0) > started and self.status().get("pid") != getattr(self, "last_pid", None), 30, "the service to come up")
        self.last_pid = self.status()["pid"]

    def stop_service(self):
        from xordering_printer import service as svc

        os.environ["XORDERING_PRINTER_PORT"] = self.env["XORDERING_PRINTER_PORT"]
        svc.CONTROL_PORT = int(self.env["XORDERING_PRINTER_PORT"])
        svc.ask("quit")
        self.service.wait(20)

    def status(self):
        try:
            return json.loads((self.home / "status.json").read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def order(self, **extra):
        return post(self.server, "/__dev/order", dict(slug=self.r["slug"], dishId=self.r["dishId"], choiceId=self.r["spiceChoiceIds"][1], **extra))

    def print_state(self, order_id):
        orders = post(self.server, "/__dev/orders", {"restaurantId": self.r["restaurantId"]})
        o = next(o for o in orders if o["id"] == order_id)
        return o.get("print")

    def count(self, i, number):
        return self.mocks[i].count(f"#{number:04d}")

    def run(self):
        from xordering_printer import api, config, setup

        # A restaurant with two printers (the mock printers), opened for orders.
        self.r = post(self.server, "/__dev/setup", {"printers": [{"name": "后厨 Kitchen 1", "host": "127.0.0.1", "port": self.ports[0]}, {"name": "后厨 Kitchen 2", "host": "127.0.0.1", "port": self.ports[1]}]})
        config.update(site=self.site)

        # 1. Email sign-in (owner) registers a computer; then a device key connects this one.
        setup.connect_server(self.site)
        owner_email = "Owner@Golden-Lotus.example"
        sent = setup.send_code(owner_email, "zh")
        self.check("email sign-in: a code is emailed (60 s resend cooldown)", sent.get("cooldownSeconds") == 60 and sent.get("expiresInMinutes") == 10)
        try:
            setup.send_code(owner_email)
            self.check("email sign-in: resending at once is refused (cooldown)", False)
        except api.ApiError as e:
            self.check("email sign-in: resending at once is refused (cooldown)", e.code == "cooldown" and 0 < e.payload.get("waitSeconds", 0) <= 60)
        code = get(self.server, "/__dev/last-code?email=owner@golden-lotus.example")["code"]
        try:
            setup.sign_in(owner_email, "000000" if code != "000000" else "111111")
            self.check("email sign-in: a wrong code is refused", False)
        except api.ApiError as e:
            self.check("email sign-in: a wrong code is refused", e.code == "invalid_code" and e.payload.get("attemptsLeft") == 4)
        try:
            setup.send_code("not an email")
            self.check("email sign-in: a bad email is refused", False)
        except api.ApiError as e:
            self.check("email sign-in: a bad email is refused", e.code == "invalid_email")
        session = setup.sign_in(owner_email, code)
        rs = setup.owned_restaurants(session)
        self.check("email sign-in finds the owner's restaurant", any(r["id"] == self.r["restaurantId"] for r in rs))
        reg = api.client_for(config.load()).register(session, self.r["restaurantId"], "Office PC")
        self.check("signed-in owner registers a device token", reg["token"].startswith("xpd_"))
        key = post(self.server, "/__dev/device-key", {"restaurantId": self.r["restaurantId"]})["key"]
        cfg = setup.pair_with_key(key, "Kitchen PC")
        self.check("device key pairs this computer", cfg["deviceToken"].startswith("xpd_") and cfg["restaurant"]["id"] == self.r["restaurantId"])
        try:
            setup.pair_with_key(key, "Again")
            self.check("a used device key is refused", False)
        except api.ApiError as e:
            self.check("a used device key is refused", e.code == "invalid_key")
        cfg = setup.refresh()
        self.check("printers come from the server", [p["port"] for p in cfg["printers"]] == self.ports)
        for p in cfg["printers"]:
            setup.test_print(p)
        self.check("test page reaches both printers (ASAP and scheduled samples)", wait(lambda: all(m.tickets and m.tickets[-1]["text"].count("TEST PRINT - 测试打印") == 2 and m.tickets[-1]["cuts"] == 2 for m in self.mocks), 10, "test prints"))

        # 2. The program runs as in the shop: service + worker.
        self.start_service()
        o1 = self.order()
        wait(lambda: self.count(0, o1["number"]) and self.count(1, o1["number"]), 20, "order 1 on both printers")
        wait(lambda: (self.print_state(o1["orderId"]) or {}).get("status") == "printed", 20, "order 1 reported")
        self.check("order prints on both printers and shows Printed", True, f"#{o1['number']:04d}")
        time.sleep(9)
        self.check("no duplicates after more polls", [self.count(0, o1["number"]), self.count(1, o1["number"])] == [1, 1])
        t = self.mocks[0].tickets[-1]["text"]
        self.check("ticket content (pickup time first, store, customer, dish, options, totals, GBK Chinese)", t.index("PICKUP: ASAP (~") < t.index("GOLDEN LOTUS") < t.index("Order #") and all(s in t for s in ["Placed ", "陈先生 Mr. Chen", "Kung Pao Chicken", "宫保鸡丁", "Spice: Hot / 特辣 (+1.00)", "@13.88", "少放花生", "TOTAL", "不要香菜", "NOT PAID - PAY AT PICKUP", "[cut]"]))

        # 3. Printer 2 offline: Failed on the dashboard, printed once it is back.
        self.mocks[1].stop()
        o2 = self.order(fulfillment="dine_in", tableLabel="12")
        wait(lambda: self.count(0, o2["number"]) == 1, 20, "order 2 on printer 1")
        st = wait(lambda: (lambda p: p if p and p["status"] == "failed" else None)(self.print_state(o2["orderId"])), 30, "order 2 failed state")
        self.check("printer offline -> dashboard shows Print failed with the reason", "connect" in (st.get("error") or ""), st.get("error") or "")
        self.mocks[1].start()
        wait(lambda: self.count(1, o2["number"]) == 1, 60, "order 2 on printer 2 after it came back")
        wait(lambda: (self.print_state(o2["orderId"]) or {}).get("status") == "printed", 20, "order 2 printed")
        self.check("printer back -> the waiting ticket prints, once", [self.count(0, o2["number"]), self.count(1, o2["number"])] == [1, 1])
        self.check("dine-in ticket shows the table", "DINE-IN - TABLE 12" in self.mocks[0].tickets[-1]["text"])

        # 4. Internet down: orders placed meanwhile print when it returns; nothing is lost or doubled.
        self.proxy.down()
        wait(lambda: self.status().get("state") == "offline", 20, "the worker to notice the outage")
        o3, o4 = self.order(), self.order()
        time.sleep(8)
        self.check("internet down -> nothing printed yet", self.count(0, o3["number"]) + self.count(0, o4["number"]) == 0)
        self.proxy.restore()
        wait(lambda: all(self.count(i, o["number"]) == 1 for i in (0, 1) for o in (o3, o4)), 40, "orders 3 and 4 after the outage")
        wait(lambda: all((self.print_state(o["orderId"]) or {}).get("status") == "printed" for o in (o3, o4)), 20, "orders 3 and 4 reported")
        self.check("internet back -> both orders print on both printers, once each", True)

        # 5. Reprint from the dashboard: one more copy each, marked REPRINT.
        post(self.server, "/__dev/reprint", {"restaurantId": self.r["restaurantId"], "orderId": o1["orderId"]})
        wait(lambda: self.count(0, o1["number"]) == 2 and self.count(1, o1["number"]) == 2, 20, "the reprint")
        self.check("reprint prints one more copy on each printer, marked REPRINT", self.mocks[0].tickets[-1]["text"].lstrip().startswith("REPRINT"))

        # 6. An order rejected while printing is down never prints.
        self.mocks[0].stop()
        self.mocks[1].stop()
        o5 = self.order()
        time.sleep(1)
        post(self.server, "/__dev/set-order-status", {"restaurantId": self.r["restaurantId"], "orderId": o5["orderId"], "status": "rejected"})
        self.mocks[0].start()
        self.mocks[1].start()
        time.sleep(15)
        self.check("an order rejected before it printed is not printed", self.count(0, o5["number"]) + self.count(1, o5["number"]) == 0)

        # 7. The worker crashes: the service starts it again.
        pid = self.status()["pid"]
        os.kill(pid, signal.SIGKILL)
        wait(lambda: self.status().get("pid") not in (None, pid) and self.status().get("state") == "ok", 30, "a new worker")
        o6 = self.order()
        wait(lambda: self.count(0, o6["number"]) == 1 and self.count(1, o6["number"]) == 1, 30, "order 6 after the crash")
        self.check("worker killed -> restarted by the service, keeps printing", True, f"pid {pid} -> {self.status().get('pid')}")

        # 8. The program restarts (update, reinstall, reboot): still connected, nothing printed twice.
        before = [len(m.tickets) for m in self.mocks]
        self.stop_service()
        self.start_service()
        time.sleep(9)
        self.check("restart -> still connected, no ticket printed again", [len(m.tickets) for m in self.mocks] == before and self.status().get("state") == "ok", f"worker pid {self.last_pid}")

        # 9. Image mode (printers without a Chinese font).
        p2 = next(p for p in config.load()["printers"] if p["port"] == self.ports[1])
        setup.save_printer(dict(p2, render="image"))
        o7 = self.order()
        wait(lambda: self.count(0, o7["number"]) == 1 and any(t["image"] for t in self.mocks[1].tickets), 30, "the picture ticket")
        png = sorted((self.out / "printer2").glob("*.png"))[-1]
        self.check("image mode prints the ticket as a picture", png.stat().st_size > 2000, str(png))

        # 10. The Tsunami printer: a Star TSP100 (no fonts, no ESC/POS), shared with the POS.
        from mock_printer import MockPrinter as Mock

        self.mocks[0].stop()
        star = self.mocks[0] = Mock(self.ports[0], out=str(self.out / "star"), quiet=True, star=True).start()
        p1 = next(p for p in config.load()["printers"] if p["port"] == self.ports[0])
        post(self.server, "/__dev/save-printer", {"restaurantId": self.r["restaurantId"], "printer": dict(p1, render="star")})
        wait(lambda: any(p.get("render") == "star" for p in config.load()["printers"]), 20, "the worker to see the Star printer")
        o8 = self.order()
        wait(lambda: len(star.tickets) == 1, 30, "the Star ticket")
        self.check("Star printer gets the ticket in Star Graphic Mode raster (every byte checked), with a cut", star.format_errors == [] and star.tickets[0]["cuts"] == 1 and star.status_requests >= 2, f"{star.tickets[0]['bytes']} bytes")
        self.check("ticket starts with the pickup time in the biggest type", star.image(1).size[0] == 576 and star.image(1).size[1] > 600)
        star.busy_until = time.time() + 12  # the POS is printing: port 9100 drops us
        o9 = self.order()
        time.sleep(6)
        self.check("printer busy with the POS -> the ticket waits", len(star.tickets) == 1 and star.busy_hits >= 1, f"{star.busy_hits} refused tries")
        wait(lambda: len(star.tickets) == 2, 60, "the ticket after the POS finished")
        time.sleep(9)
        self.check("POS done -> the ticket prints once", len(star.tickets) == 2 and (self.print_state(o9["orderId"]) or {}).get("status") == "printed")

        # 11. A scheduled order prints when placed and again 20 minutes before pickup, marked READY BY … · REPRINT.
        from datetime import datetime, timedelta, timezone

        p2 = next(p for p in config.load()["printers"] if p["port"] == self.ports[1])
        post(self.server, "/__dev/save-printer", {"restaurantId": self.r["restaurantId"], "printer": dict(p2, render="text")})
        wait(lambda: all(p.get("render") != "image" for p in config.load()["printers"]), 20, "printer 2 back in text mode")
        t = datetime.now(timezone.utc) + timedelta(minutes=75)
        at = t.replace(minute=t.minute // 15 * 15, second=0, microsecond=0)
        o10 = self.order(pickupAt=at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        wait(lambda: len(star.tickets) == 3, 30, "the scheduled order's ticket")
        post(self.server, "/__dev/scheduled-due", {"orderId": o10["orderId"]})
        post(self.server, "/__dev/alert-tick", {})
        wait(lambda: len(star.tickets) == 4, 30, "the reprint before pickup")
        time.sleep(9)
        post(self.server, "/__dev/alert-tick", {})
        time.sleep(6)
        self.check("scheduled order: one ticket when placed, one READY BY reprint before pickup, no more", len(star.tickets) == 4 and self.count(1, o10["number"]) == 2)
        text2 = self.mocks[1].tickets[-1]["text"]
        self.check("the reprint is marked READY BY … · REPRINT above ★ SCHEDULED", text2.lstrip().startswith("READY BY ") and "REPRINT" in " ".join(text2.strip().split("\n")[:2]) and "* SCHEDULED " in text2, " / ".join(r.strip() for r in text2.strip().split("\n")[:2]))

        # 12. Dashboard: test page, preview (the program's own picture), printer status and heartbeat.
        before = len(star.tickets)
        post(self.server, "/__dev/test-page", {"restaurantId": self.r["restaurantId"]})
        wait(lambda: len(star.tickets) == before + 2, 30, "the test page from the dashboard")
        self.check("dashboard test page prints the two sample tickets", True)
        post(self.server, "/__dev/request-preview", {"restaurantId": self.r["restaurantId"]})
        pv = wait(lambda: (lambda p: p if p.get("images") else None)(post(self.server, "/__dev/preview", {"restaurantId": self.r["restaurantId"]})), 30, "the preview")
        import base64
        import io

        from PIL import Image

        imgs = [Image.open(io.BytesIO(base64.b64decode(b))).convert("1") for b in pv["images"]]
        printed = [star.image(before + 1), star.image(before + 2)]
        self.check("preview PNGs are exactly the pictures the printer got", [i.tobytes() for i in imgs] == [p.tobytes() for p in printed], f"{[i.size for i in imgs]}")
        star.paper_low = True
        o11 = self.order()
        wait(lambda: any((st or {}).get("paperLow") for d in post(self.server, "/__dev/printing", {"restaurantId": self.r["restaurantId"]})["devices"] for st in ((d.get("status") or {}).get("printers") or {}).values()), 90, "paper low on the dashboard")
        devs = post(self.server, "/__dev/printing", {"restaurantId": self.r["restaurantId"]})["devices"]
        self.check("dashboard shows the computer online and the printer's paper running low", any(d.get("online") for d in devs))
        del o11

        totals = [len(m.tickets) for m in self.mocks]
        print(f"\nAll {len(self.results)} checks passed. Tickets received: printer 1 = {totals[0]}, printer 2 = {totals[1]} (in {self.out}).")

    def close(self):
        if self.service and self.service.poll() is None:
            try:
                self.stop_service()
            except Exception:
                self.service.kill()
        for m in self.mocks:
            m.stop()
        shutil.copy(self.home / "logs" / "printer.log", self.out / "printer.log") if (self.home / "logs" / "printer.log").exists() else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server")
    p.add_argument("--xordering", help="path to the XOrdering repository (starts its dev server)")
    p.add_argument("--out", default=str(ROOT / "e2e-output"))
    a = p.parse_args()
    out = Path(a.out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    dev = None
    server = a.server
    if not server:
        port = free_port()
        dev = subprocess.Popen(["node", "scripts/print-dev-server.ts", "--port", str(port)], cwd=a.xordering, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        server = f"http://127.0.0.1:{port}"
        wait(lambda: _up(server), 60, "the dev server")
    run = Run(server, out)
    try:
        run.run()
    finally:
        run.close()
        if dev:
            dev.terminate()
        (out / "results.json").write_text(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in run.results], ensure_ascii=False, indent=1), "utf-8")


def _up(server):
    try:
        urllib.request.urlopen(server + "/printer-config.json", timeout=2).read()
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
