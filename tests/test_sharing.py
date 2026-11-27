"""Sharing a printer with the POS (qMenu …), printers that keep failing, and settings that survive.

A Star LAN printer takes one connection at a time (MockPrinter exclusive=True). While another
program holds it, our ticket must wait in the queue with an error naming that program, then print
exactly once when the printer is free.
"""
import json
import subprocess
import sys
import time
import unittest

from tests.helpers import free_port, temp_home
from mock_printer import MockPrinter


def wait_for(cond, seconds: float = 5.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        time.sleep(0.05)


def hold(port: int) -> subprocess.Popen:
    """Another program (the 'POS') connected to the printer."""
    p = subprocess.Popen([sys.executable, "-c", f"import socket,time; s=socket.create_connection(('127.0.0.1',{port})); print('up', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE)
    assert p.stdout.readline().strip() == b"up"
    time.sleep(0.3)
    return p


class SharedPrinterTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_home()
        from xordering_printer import discover

        self.port = free_port()
        self.mock = MockPrinter(self.port, self.home + "/prints", quiet=True, star=True, exclusive=True).start()
        self.who = lambda host: discover.who_uses(host, ports=[self.port], include_loopback=True)
        self.printer = {"id": "p0", "name": "Kitchen", "host": "127.0.0.1", "port": self.port, "render": "star", "paperWidth": 80, "enabled": True}

    def tearDown(self):
        self.mock.stop()

    def test_deliver_names_the_program_holding_the_printer(self):
        from xordering_printer import deliver, ticket
        from xordering_printer.printer import PrinterError

        pos = hold(self.port)
        try:
            with self.assertRaises(PrinterError) as ctx:
                deliver.deliver(self.printer, [ticket.test_lines(self.printer)], who_uses=self.who)
            self.assertEqual(ctx.exception.code, "in_use")
            self.assertIn("python", str(ctx.exception).lower())
            self.assertIn("打印机正被", str(ctx.exception))
        finally:
            pos.kill()
            pos.wait()
        time.sleep(0.3)
        st = deliver.deliver(self.printer, [ticket.test_lines(self.printer, {"name": {"en": "Tsunami Sushi"}})], who_uses=self.who)
        self.assertEqual(st["method"], "tcp")
        wait_for(lambda: len(self.mock.tickets) == 1)
        self.assertEqual(len(self.mock.tickets), 1)
        self.assertEqual(self.mock.format_errors, [])

    def test_worker_queues_while_in_use_and_prints_once(self):
        from tests.test_worker import FakeServer
        from xordering_printer import config
        from xordering_printer.jobs import JobStore
        from xordering_printer.worker import Worker

        server = FakeServer([self.printer])
        server.add("j1", "p0", 5001)
        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64)
        store = JobStore(self.home + "/jobs.db")
        w = Worker(store=store, client_factory=server.client, who_uses=self.who, rediscover=lambda: {"candidates": []})
        pos = hold(self.port)
        try:
            w.step()
            row = store.get("j1")
            self.assertEqual(row["state"], "queued")
            self.assertEqual(w.printer_state["p0"]["code"], "in_use")
            self.assertIn("python", w.printer_state["p0"]["error"].lower())
            self.assertGreater(row["next_attempt"], time.time() + 1)  # backs off (2 s, 5 s, 10 s …)
            self.assertEqual(server.reports[-1], ("j1", "failed"))  # the dashboard hears about it
            w.step()  # still backing off: no new attempt
            self.assertEqual(store.get("j1")["attempts"], 1)
        finally:
            pos.kill()
            pos.wait()
        time.sleep(0.3)
        store.db.execute("update jobs set next_attempt = 0")
        w.step()
        self.assertEqual(store.get("j1")["state"], "printed")
        self.assertTrue(w.printer_state["p0"]["ok"])
        for _ in range(3):
            w.step()
        wait_for(lambda: len(self.mock.tickets) >= 1)
        time.sleep(0.5)
        self.assertEqual(len(self.mock.tickets), 1)  # printed once
        self.assertEqual(server.jobs["j1"]["status"], "printed")
        # Our own connection is closed right after the ticket: the printer is free for the POS again.
        time.sleep(0.3)
        self.assertEqual(self.mock.active, 0)


class StuckPrinterTest(unittest.TestCase):
    def test_keeps_failing_flags_stuck_and_looks_again(self):
        home = temp_home()
        from tests.test_worker import FakeServer
        from xordering_printer import config, worker as worker_mod
        from xordering_printer.jobs import JobStore

        closed = free_port()
        printer = {"id": "p0", "name": "Kitchen", "host": "127.0.0.1", "port": closed, "render": "star", "paperWidth": 80, "enabled": True}
        server = FakeServer([printer])
        server.add("j1", "p0", 6001)
        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "a" * 64)
        calls = []

        def rediscover():
            calls.append(time.time())
            return {"candidates": [], "at": time.time()}

        old = (worker_mod.STUCK_AFTER, worker_mod.STUCK_ATTEMPTS)
        worker_mod.STUCK_AFTER, worker_mod.STUCK_ATTEMPTS = 0.0, 2
        try:
            store = JobStore(home + "/jobs.db")
            w = worker_mod.Worker(store=store, client_factory=server.client, who_uses=lambda h: "", rediscover=rediscover)
            w.step()
            self.assertFalse(w.printer_state["p0"]["stuck"])
            store.db.execute("update jobs set next_attempt = 0")
            w.step()
            st = w.printer_state["p0"]
            self.assertTrue(st["stuck"])
            self.assertEqual(st["attempts"], 2)
            for _ in range(50):
                if not w.rediscovering and calls:
                    break
                time.sleep(0.1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(worker_mod.discovery_path().read_text("utf-8"))["reason"], "printing kept failing")
            store.db.execute("update jobs set next_attempt = 0")
            w.step()
            self.assertEqual(len(calls), 1)  # not again within 30 min
            self.assertEqual(store.get("j1")["state"], "queued")  # never dropped
            w.write_status()
            status = json.loads((worker_mod.status_path()).read_text("utf-8"))
            self.assertTrue(status["printers"]["p0"]["stuck"])
            self.assertTrue(w.status_payload()["printers"]["p0"]["stuck"])
        finally:
            worker_mod.STUCK_AFTER, worker_mod.STUCK_ATTEMPTS = old


class SettingsSurviveTest(unittest.TestCase):
    def test_local_connection_survives_polls_and_saves_go_to_the_right_place(self):
        temp_home()
        from xordering_printer import api, config, setup

        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "b" * 64, restaurant={"id": "r"})
        sent = []

        class Fake:
            def save_printer(self, token, printer):
                sent.append(dict(printer))
                return dict(printer, id=printer.get("id") or "p-new", position=0)

        orig = api.client_for
        api.client_for = lambda cfg: Fake()
        try:
            saved = setup.save_printer({"name": "后厨 Kitchen 1", "host": "", "port": 9100, "render": "star", "paperWidth": 80, "enabled": True,
                                        "connection": "windows", "windowsPrinter": "Star TSP143IIIU"})
        finally:
            api.client_for = orig
        self.assertEqual(saved["connection"], "windows")
        self.assertEqual(saved["windowsPrinter"], "Star TSP143IIIU")
        self.assertNotIn("connection", sent[0])
        self.assertNotIn("windowsPrinter", sent[0])
        self.assertRegex(sent[0]["host"], r"^[A-Za-z0-9][A-Za-z0-9.-]*$")  # the server's host rule
        # A poll replaces the server's printer list; this computer's way of reaching it stays.
        config.update(printers=[{"id": "p-new", "name": "后厨 Kitchen 1", "host": sent[0]["host"], "port": 9100, "render": "star", "paperWidth": 80, "enabled": True}])
        p = config.effective_printers(config.load())[0]
        self.assertEqual((p["connection"], p["windowsPrinter"]), ("windows", "Star TSP143IIIU"))
        # Switching to the IP directly forgets the Windows printer.
        api.client_for = lambda cfg: Fake()
        try:
            setup.save_printer(dict(p, connection="tcp", host="192.168.1.50", windowsPrinter=""))
        finally:
            api.client_for = orig
        p = config.effective_printers(config.load())[0]
        self.assertEqual((p["connection"], p.get("windowsPrinter")), ("tcp", None))
        self.assertEqual(sent[-1]["host"], "192.168.1.50")

    def test_token_and_printers_survive_an_update_and_a_damaged_file(self):
        home = temp_home()
        from xordering_printer import config
        from xordering_printer.paths import config_path

        # A config.json as version 0.2.2 wrote it (no printerLocal yet).
        old = {"site": "https://yunruog.github.io/xordering/", "supabaseUrl": "https://x.supabase.co", "publishableKey": "sb_publishable_x",
               "deviceToken": "xpd_" + "c" * 64, "deviceId": "d1", "deviceName": "PC", "restaurant": {"id": "r"},
               "printers": [{"id": "p1", "name": "Kitchen", "host": "192.168.1.50", "port": 9100, "render": "star", "paperWidth": 80, "enabled": True}],
               "pollSeconds": 4, "autoUpdate": True}
        config_path().write_text(json.dumps(old), "utf-8")
        cfg = config.load()
        self.assertEqual(cfg["deviceToken"], old["deviceToken"])
        self.assertEqual(config.effective_printers(cfg)[0]["connection"], "tcp")
        config.set_local("p1", connection="windows", windowsPrinter="Star TSP143IIIU")
        # The old version's own saves (config.update) keep keys it doesn't know.
        raw = json.loads(config_path().read_text("utf-8"))
        raw["pollSeconds"] = 5
        config_path().write_text(json.dumps(raw), "utf-8")
        cfg = config.update(deviceName="PC2")
        self.assertEqual(cfg["printerLocal"]["p1"]["windowsPrinter"], "Star TSP143IIIU")
        # A damaged config.json (power cut mid-write): the backup keeps the token and the printers.
        config_path().write_text('{"deviceToken": "xpd_', "utf-8")
        cfg = config.load()
        self.assertEqual(cfg["deviceToken"], old["deviceToken"])
        self.assertEqual(cfg["printers"][0]["id"], "p1")
        self.assertTrue((config_path().parent / "config.backup.json").exists())
        self.assertTrue(str(config_path()).startswith(home))


if __name__ == "__main__":
    unittest.main()
