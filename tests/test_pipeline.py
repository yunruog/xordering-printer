"""One pipeline for every ticket (pipeline.py): same bytes for the same ticket whichever way it was
asked for, bytes never turned into text on the way, each order printed once, bad tickets never sent.

Bytes are caught by a fake sender (what would go to IP:9100 / the Windows RAW job), so the tests see
exactly what the printer would get.
"""
import json
import time
import unittest

from tests.helpers import ORDER, temp_home
from tests.test_worker import FakeServer
from xordering_printer.printer import PrinterError


class Capture:
    def __init__(self):
        self.sent = []
        self.fail = []  # PrinterError codes to raise, one per call

    def __call__(self, host, port, data, kind="escpos"):
        assert isinstance(data, bytes)  # never a str: the bytes go out untouched
        if self.fail:
            code = self.fail.pop(0)
            raise PrinterError(f"pretend failure {code}", code)
        self.sent.append(data)
        return {}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        temp_home()
        from xordering_printer import config
        from xordering_printer.jobs import JobStore
        from xordering_printer.worker import Worker

        self.printer = {"id": "p0", "name": "后厨 Kitchen 1", "host": "127.0.0.1", "port": 9100, "enabled": True, "paperWidth": 80, "render": "image"}
        self.server = FakeServer([self.printer])
        config.update(supabaseUrl="http://fake", publishableKey="k", deviceToken="xpd_" + "a" * 64)
        self.store = JobStore(str(config.config_path().parent / "jobs.db"))
        self.capture = Capture()
        self.worker = Worker(store=self.store, sender=self.capture, client_factory=self.server.client, who_uses=lambda host: "")
        self.config = config

    def rounds(self, n=1, due_now=True):
        for _ in range(n):
            if due_now:
                self.store.db.execute("update jobs set next_attempt = 0")
            self.worker.step()

    # --- the same bytes ------------------------------------------------------------------------

    def test_order_path_and_test_path_make_the_same_bytes(self):
        from xordering_printer import pipeline, setup, ticket

        for render in ("image", "star", "text"):
            self.printer["render"] = render
            order = dict(ORDER, id=f"o-{render}", number=4100 + len(self.capture.sent))
            self.server.jobs.clear()
            self.server.add(f"j-{render}", "p0", order["number"], **{k: v for k, v in order.items() if k != "number"})
            sent_before = len(self.capture.sent)
            self.rounds(1)
            self.assertEqual(len(self.capture.sent), sent_before + 1, render)
            from_worker = self.capture.sent[-1]
            restaurant = self.worker.cfg["restaurant"]
            # The window's path (test page / reprint) with the same order data.
            pipeline.print_tickets(dict(self.printer), [ticket.build(self.server.jobs[f"j-{render}"]["order"], restaurant)], source="test page",
                                   title="t", sender=self.capture, driver="")
            self.assertEqual(self.capture.sent[-1], from_worker, render)
        # The dashboard's test page and the window's test page: the same bytes too.
        self.printer["render"] = "image"
        self.server.jobs.clear()
        self.server.add("j-test", "p0", 0, reason="test")
        self.rounds(1)
        dashboard = self.capture.sent[-1]
        self.config.update(restaurant=self.worker.cfg["restaurant"])
        pipeline.print_tickets(dict(self.printer), ticket.test_tickets(self.worker.cfg["restaurant"]), source="test page", title="t", sender=self.capture, driver="")
        self.assertEqual(self.capture.sent[-1], dashboard)

    def test_server_to_printer_round_trip_keeps_every_byte(self):
        """The order arrives as JSON (no printer bytes cross the internet); stored in jobs.db and read
        back it makes exactly the bytes the original order makes."""
        from xordering_printer import pipeline, ticket

        order = dict(ORDER, id="o-rt", number=4200, notes="不要香菜 · ★ 🍣 \\ \" ' é 中")
        job = {"id": "j-rt", "printerId": "p0", "reason": "new", "status": "pending", "order": order}
        wire = json.dumps(job, ensure_ascii=False).encode("utf-8")  # what the server sends (UTF-8 JSON)
        received = json.loads(wire.decode("utf-8"))
        self.assertTrue(self.store.add(received))
        stored = json.loads(self.store.get("j-rt")["payload"])
        self.assertEqual(stored, job)
        restaurant = {"name": {"en": "Tsunami Sushi"}, "currency": "CAD"}
        direct = pipeline.build(self.printer, [ticket.build(order, restaurant)])
        via = pipeline.build(self.printer, [ticket.build(stored["order"], restaurant)])
        self.assertEqual(direct, via)
        # Bytes go to the sender untouched (the same object built and checked).
        self.server.jobs["j-rt"] = job
        self.config.update(restaurant=restaurant, printers=[self.printer])
        self.worker.cfg = self.config.load()
        self.worker.print_due()
        self.assertEqual(self.capture.sent[-1], direct)
        self.assertTrue(self.capture.sent[-1].startswith(b"\x1b@\x1ba\x00\x1dv0\x00\x48\x00"))  # ESC @, GS v 0, 72 bytes = 576 dots

    # --- once ----------------------------------------------------------------------------------

    def test_same_order_pushed_again_prints_once(self):
        self.server.add("j1", "p0", 4301, id="o-4301")
        self.rounds(4)
        self.assertEqual(len(self.capture.sent), 1)
        # The report got lost: the server sends the same job again, many times.
        self.server.jobs["j1"]["status"] = "pending"
        self.rounds(4)
        self.assertEqual(len(self.capture.sent), 1)
        # A second "new" job for the same order (pushed twice by mistake): skipped, not printed.
        self.server.add("j2", "p0", 4301, id="o-4301")
        self.rounds(3)
        self.assertEqual(len(self.capture.sent), 1)
        self.assertEqual(self.store.get("j2")["state"], "skipped")
        self.assertIn(("j2", "skipped"), self.server.reports)
        # A reprint is a new job with reason reprint: it prints.
        self.server.add("j3", "p0", 4301, reason="reprint", id="o-4301")
        self.rounds(2)
        self.assertEqual(len(self.capture.sent), 2)

    def test_failures_that_may_have_printed_stop_after_three_tries(self):
        from xordering_printer.jobs import MAX_TRIES

        self.capture.fail = ["lost"] * 10
        self.server.add("j1", "p0", 4401)
        self.rounds(8)
        row = self.store.get("j1")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["tries"], MAX_TRIES)
        self.assertEqual(len(self.capture.fail), 10 - MAX_TRIES)  # no 4th try
        self.assertEqual(self.server.jobs["j1"]["status"], "failed")

    def test_printer_off_waits_without_using_up_tries(self):
        self.capture.fail = ["refused"] * 6
        self.server.add("j1", "p0", 4402)
        self.rounds(7)
        self.assertEqual(self.store.get("j1")["state"], "printed")
        self.assertEqual(self.store.get("j1")["tries"], 0)
        self.assertEqual(len(self.capture.sent), 1)

    # --- refused before sending ------------------------------------------------------------------

    def test_star_raster_to_an_esc_pos_windows_printer_is_refused(self):
        """The Tsunami case: an ESC/POS printer saved as Star raster."""
        from xordering_printer import pipeline, ticket

        star = dict(self.printer, render="star", connection="windows", windowsPrinter="Kitchen")
        with self.assertRaises(pipeline.BadTicket) as e:
            pipeline.print_tickets(star, [ticket.build(ORDER, {})], source="order", title="t", sender=self.capture, driver="POS-80C Printer")
        self.assertIn("ESC/POS", str(e.exception))
        self.assertEqual(self.capture.sent, [])
        last = pipeline.journal(1)[0]
        self.assertEqual((last["result"], last["mode"], last["driver"]), ("refused", "star", "POS-80C Printer"))
        self.assertTrue(last["head"].startswith("1b 2a 72 52"))
        # A Star printer on the Generic / Text Only driver is fine.
        self.assertEqual(pipeline.type_conflict(dict(star, windowsPrinter="Star TSP143"), "Generic / Text Only"), "")

    def test_wrong_width_too_long_or_malformed_bytes_are_never_sent(self):
        from xordering_printer import escpos, pipeline, render, ticket

        lines = [ticket.build(ORDER, {})]
        p80 = dict(self.printer)
        good = pipeline.build(p80, lines)
        self.assertEqual(pipeline.validate(p80, good, lines)["rows"] > 0, True)
        with self.assertRaises(pipeline.BadTicket):  # 80 mm picture on a 58 mm printer
            pipeline.validate(dict(p80, paperWidth=58), good, lines)
        with self.assertRaises(pipeline.BadTicket):  # Star bytes to an ESC/POS picture printer
            pipeline.validate(p80, pipeline.build(dict(p80, render="star"), lines), lines)
        with self.assertRaises(pipeline.BadTicket):  # cut short (a truncated job)
            pipeline.validate(p80, good[: len(good) // 2], lines)
        with self.assertRaises(pipeline.BadTicket):  # text where a picture belongs
            pipeline.validate(p80, b"\x1b@" + "乱码".encode("gbk") * 100, lines)
        from PIL import Image

        huge = escpos.picture_bytes(Image.new("1", (576, 20000), 0))  # 2.5 m of paper
        with self.assertRaises(pipeline.BadTicket) as e:
            pipeline.validate(p80, huge, lines)
        self.assertIn("过长", str(e.exception))
        self.assertLess(render.render(lines[0], 80).size[1], pipeline.row_limit(lines, 80) / 2)

    def test_a_refused_ticket_is_not_retried_and_the_server_hears_why(self):
        from unittest import mock

        from xordering_printer import pipeline

        self.server.add("j1", "p0", 4501)
        with mock.patch.object(pipeline, "build", return_value=b"\x1b*rR garbage"):
            self.rounds(4)
        self.assertEqual(self.capture.sent, [])
        self.assertEqual(self.store.get("j1")["state"], "failed")
        self.assertEqual(self.server.jobs["j1"]["status"], "failed")
        self.assertIn("not sent", self.store.get("j1")["last_error"])
        self.assertEqual(self.worker.status_payload()["problems"][0]["order"], 4501)

    # --- pause ---------------------------------------------------------------------------------

    def test_pause_then_resume_and_print(self):
        from xordering_printer import setup

        self.server.paused = False
        setup.set_paused(True)
        self.server.add("j1", "p0", 4601)
        self.rounds(3)
        self.assertEqual(self.capture.sent, [])
        self.assertEqual(self.store.get("j1")["state"], "queued")
        self.assertEqual(self.server.pause_calls[-1], (True, False))
        setup.set_paused(False)
        self.rounds(2)
        self.assertEqual(len(self.capture.sent), 1)

    def test_pause_then_resume_and_skip(self):
        from xordering_printer import setup

        setup.set_paused(True)
        self.server.add("j1", "p0", 4602)
        self.server.add("j2", "p0", 4603)
        self.rounds(2)
        setup.set_paused(False, skip_waiting=True)
        self.rounds(3)
        self.assertEqual(self.capture.sent, [])
        self.assertEqual({self.store.get(j)["state"] for j in ("j1", "j2")}, {"skipped"})
        self.assertEqual(self.server.pause_calls[-1], (False, True))
        self.server.add("j3", "p0", 4604)  # after the resume: prints
        self.rounds(2)
        self.assertEqual(len(self.capture.sent), 1)

    def test_pause_on_the_dashboard_holds_tickets_here(self):
        self.server.paused = True
        self.server.add("j1", "p0", 4605)
        self.rounds(3)
        self.assertEqual(self.capture.sent, [])
        self.assertTrue(self.config.load()["paused"])
        self.server.paused = False
        self.rounds(2)
        self.assertEqual(len(self.capture.sent), 1)

    # --- reprint, log, one worker ----------------------------------------------------------------

    def test_reprint_last_order_goes_through_the_same_pipeline(self):
        from unittest import mock

        from xordering_printer import pipeline, setup

        with self.assertRaises(LookupError):
            setup.reprint_last()
        self.server.add("j1", "p0", 4701)
        self.rounds(2)
        with mock.patch("xordering_printer.printer.send", self.capture):
            st = setup.reprint_last()
        self.assertEqual(st["order"], 4701)
        self.assertEqual(len(self.capture.sent), 2)
        last = pipeline.journal(1)[0]
        self.assertEqual((last["source"], last["order"], last["result"], last["api"], last["datatype"]), ("reprint (window)", 4701, "sent", "TCP socket", "RAW"))
        text = pipeline.copy_text(self.config.load(), {"state": "ok"})
        self.assertIn('"order": 4701', text)
        self.assertIn("type=image paper=80mm", text)
        self.assertNotIn("xpd_" + "a" * 64, text)

    def test_every_attempt_is_logged_with_its_bytes(self):
        from xordering_printer import pipeline

        self.server.add("j1", "p0", 4801)
        self.rounds(2)
        e = pipeline.journal(1)[0]
        data = self.capture.sent[0]
        self.assertEqual((e["source"], e["job"], e["order"], e["attempt"], e["mode"], e["paper"], e["dots"]), ("order", "j1", 4801, 1, "image", 80, 576))
        self.assertEqual((e["bytes"], e["head"], e["tail"]), (len(data), data[:64].hex(" "), data[-32:].hex(" ")))

    def test_one_print_loop_per_computer(self):
        from xordering_printer import service

        first = service.worker_lock()
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(service.worker_lock())
        finally:
            first.close()


class WindowsJobWaitTest(unittest.TestCase):
    """A Windows job taken back after it started printing counts as a try (not retried forever)."""

    def wait(self, job_status, printer_status=0):
        from xordering_printer import winprint

        calls = {"deleted": 0}

        class W:
            def GetJob(self, h, j, level):
                return {"Status": job_status, "pStatus": ""}

            def GetPrinter(self, h, level):
                return {"Status": printer_status}

            def SetJob(self, *a):
                calls["deleted"] += 1

        with self.assertRaises(PrinterError) as e:
            winprint.wait_for_job(W(), None, "Kitchen", 7, timeout=0.2, poll=0.02)
        self.assertEqual(calls["deleted"], 1)
        return e.exception.code

    def test_printing_job_taken_back_counts_as_a_try(self):
        from xordering_printer import winprint
        from xordering_printer.worker import MAYBE_PRINTED

        code = self.wait(winprint.J_PRINTING)
        self.assertEqual(code, "partial")
        self.assertIn(code, MAYBE_PRINTED)

    def test_offline_job_only_waits(self):
        from xordering_printer import winprint
        from xordering_printer.worker import MAYBE_PRINTED

        code = self.wait(winprint.J_OFFLINE, winprint.P_OFFLINE)
        self.assertEqual(code, "offline")
        self.assertNotIn(code, MAYBE_PRINTED)


if __name__ == "__main__":
    unittest.main()
