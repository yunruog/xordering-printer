"""Windows printers, for real: run on Windows (CI) with a printer installed for the test.

The CI workflow installs "Star TSP143 (XOP test)" with the Generic / Text Only driver on a Standard
TCP/IP port named XOP_TEST_PORT that points to 127.0.0.1:XOP_TEST_PRINTER_PORT, where these tests
run a pretend Star printer (MockPrinter). So a ticket goes: our RAW job → Windows print spooler →
TCP/IP port monitor → the pretend printer, which checks every byte against the Star spec.
"""
import os
import sys
import time
import unittest

from tests.helpers import temp_home
from mock_printer import MockPrinter

NAME = os.environ.get("XOP_TEST_PRINTER", "")
PORT = int(os.environ.get("XOP_TEST_PRINTER_PORT") or 0)


@unittest.skipUnless(sys.platform == "win32" and NAME and PORT, "needs Windows with the test printer installed (CI)")
class WindowsPrinterTest(unittest.TestCase):
    def setUp(self):
        self.home = temp_home()
        self.mock = MockPrinter(PORT, self.home + "/prints", quiet=True, star=True).start()
        self.printer = {"id": "p1", "name": "Kitchen", "host": "", "port": 9100, "render": "star", "paperWidth": 80, "enabled": True,
                        "connection": "windows", "windowsPrinter": NAME}

    def tearDown(self):
        self.mock.stop()

    def wait_tickets(self, n, seconds=20):
        deadline = time.time() + seconds
        while time.time() < deadline and len(self.mock.tickets) < n:
            time.sleep(0.2)
        return len(self.mock.tickets)

    def test_lists_windows_printers_with_their_ip(self):
        from xordering_printer import discover, winprint

        printers = winprint.list_printers()
        mine = next(p for p in printers if p["name"] == NAME)
        self.assertEqual((mine["ip"], mine["tcpPort"], mine["portKind"], mine["virtual"]), ("127.0.0.1", PORT, "network", False))
        self.assertEqual(mine["port"], "XOP_TEST_PORT")  # the IP came from the port monitor, not the name
        pdf = [p for p in printers if p["name"] == "Microsoft Print to PDF"]
        if pdf:
            self.assertTrue(pdf[0]["virtual"])
        res = discover.run(networks=[], include_loopback=True, ports=[PORT])
        c = next(c for c in res["candidates"] if c["windowsPrinter"] == NAME)
        self.assertEqual((c["connection"], c["render"], c["ip"]), ("windows", "star", "127.0.0.1"))
        self.assertIn("windows", c["sources"])
        self.assertNotIn("Microsoft Print to PDF", [c["windowsPrinter"] for c in res["candidates"]])
        self.assertIn(NAME, discover.report(res))

    def test_raw_job_through_the_spooler(self):
        from xordering_printer import escpos, ticket, winprint

        data = escpos.tickets_bytes([ticket.test_lines(self.printer, {"name": {"en": "Tsunami Sushi"}})], self.printer)
        st = winprint.send_raw(NAME, data)
        self.assertEqual(st["method"], "raw")
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertEqual(self.mock.format_errors, [])
        self.assertEqual(self.mock.tickets[0]["bytes"], len(data))

    def test_connection_test_and_test_page_in_windows_mode(self):
        from xordering_printer import config, setup

        config.update(restaurant={"name": {"en": "Tsunami Sushi"}})
        res = setup.test_connection(self.printer)
        self.assertTrue(res["ok"])
        self.assertEqual(res["method"], "raw")
        self.assertEqual(self.wait_tickets(1), 1)
        setup.test_print(self.printer)
        self.assertEqual(self.wait_tickets(3), 3)  # the test page: two sample tickets
        self.assertEqual(self.mock.format_errors, [])

    def test_text_and_picture_types_and_58mm(self):
        from xordering_printer import deliver, ticket

        self.mock.stop()
        self.mock = MockPrinter(PORT, self.home + "/escpos", quiet=True).start()
        for render, paper in (("text", 80), ("image", 58)):
            p = dict(self.printer, render=render, paperWidth=paper)
            deliver.deliver(p, [ticket.test_lines(p)])
        self.assertEqual(self.wait_tickets(2), 2)
        self.assertIn("TEST PRINT", self.mock.tickets[0]["text"])
        self.assertIn("Windows printer", self.mock.tickets[0]["text"])
        self.assertTrue(self.mock.tickets[1]["image"])

    def test_printer_offline_takes_the_job_back(self):
        from xordering_printer import winprint
        from xordering_printer.printer import PrinterError

        self.mock.stop()
        import win32print

        started = time.time()
        with self.assertRaises(PrinterError):
            winprint.send_raw(NAME, b"\x1b@hello\n", timeout=8)
        self.assertLess(time.time() - started, 30)
        h = win32print.OpenPrinter(NAME)
        try:
            deadline = time.time() + 15
            while time.time() < deadline and win32print.EnumJobs(h, 0, 99, 1):
                time.sleep(0.3)
            self.assertEqual(len(win32print.EnumJobs(h, 0, 99, 1)), 0)  # not left to print later
        finally:
            win32print.ClosePrinter(h)
        # Back online: prints.
        self.mock = MockPrinter(PORT, self.home + "/again", quiet=True, star=True).start()
        from xordering_printer import escpos, ticket

        winprint.send_raw(NAME, escpos.tickets_bytes([ticket.test_lines(self.printer)], self.printer))
        self.assertEqual(self.wait_tickets(1), 1)

    def test_unknown_windows_printer(self):
        from xordering_printer import deliver, ticket
        from xordering_printer.printer import PrinterError

        with self.assertRaises(PrinterError) as ctx:
            deliver.deliver(dict(self.printer, windowsPrinter="No Such Printer 123"), [ticket.test_lines(self.printer)])
        self.assertEqual(ctx.exception.code, "not_found")

    def test_worker_prints_orders_through_the_windows_printer(self):
        from tests.test_worker import FakeServer
        from xordering_printer import config
        from xordering_printer.jobs import JobStore
        from xordering_printer.worker import Worker

        server_printer = {k: v for k, v in self.printer.items() if k not in ("connection", "windowsPrinter")}
        server = FakeServer([dict(server_printer, host="KITCHEN-PC")])
        server.add("j1", "p1", 7001)
        config.update(supabaseUrl="http://127.0.0.1:9", publishableKey="k", deviceToken="xpd_" + "d" * 64)
        config.set_local("p1", connection="windows", windowsPrinter=NAME)
        w = Worker(store=JobStore(self.home + "/jobs.db"), client_factory=server.client)
        w.step()
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertEqual(server.jobs["j1"]["status"], "printed")
        self.assertEqual(w.printer_state["p1"]["method"], "raw")
        self.assertEqual(config.load()["printerLocal"]["p1"]["windowsPrinter"], NAME)  # the poll didn't overwrite it


if __name__ == "__main__":
    unittest.main()
