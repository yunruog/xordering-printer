"""The print loop with a Star TSP100 shared with the restaurant's POS: retries, no duplicates,
scheduled reprints, the dashboard's test page and preview, printer status for the dashboard."""
import base64
import io
import time
import unittest

from tests.helpers import free_port, temp_home
from tests.test_worker import FakeServer
from mock_printer import MockPrinter
from xordering_printer import render, ticket


class StarWorkerTest(unittest.TestCase):
    def setUp(self):
        temp_home()
        from xordering_printer import config
        from xordering_printer.jobs import JobStore
        from xordering_printer.worker import Worker

        self.port = free_port()
        self.printers = [{"id": "p0", "name": "Tsunami", "host": "127.0.0.1", "port": self.port, "enabled": True, "paperWidth": 80, "render": "star"}]
        self.server = FakeServer(self.printers)
        self.mock = MockPrinter(self.port, out=f"{config.config_path().parent}/star", quiet=True, star=True).start()
        config.update(supabaseUrl="http://fake", publishableKey="k", deviceToken="xpd_" + "a" * 64)
        self.store = JobStore(str(config.config_path().parent / "jobs.db"))
        self.worker = Worker(store=self.store, client_factory=self.server.client)

    def tearDown(self):
        self.mock.stop()

    def rounds(self, n=1, due_now=False):
        for _ in range(n):
            if due_now:
                self.store.db.execute("update jobs set next_attempt = 0")
            self.worker.step()
            time.sleep(0.05)

    def wait_tickets(self, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and len(self.mock.tickets) < n:
            time.sleep(0.05)
        time.sleep(0.1)
        return len(self.mock.tickets)

    def head(self, n):
        """The first rows of ticket n, compared with how a ticket with that headline is drawn."""
        return self.mock.image(n)

    def test_prints_once_in_star_raster(self):
        self.server.add("j1", "p0", 3001, fulfillment="pickup", readyAtLocal="16:10")
        self.rounds(3)
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertEqual(self.mock.format_errors, [])
        self.assertEqual(self.server.jobs["j1"]["status"], "printed")
        self.rounds(3)
        self.assertEqual(self.wait_tickets(1), 1)
        expected = render.render(ticket.build(self.server.jobs["j1"]["order"], self.worker.cfg["restaurant"]), 80)
        self.assertEqual(self.mock.image(1).tobytes(), expected.tobytes())

    def test_busy_with_the_pos_waits_and_prints_once(self):
        self.mock.busy_until = time.time() + 3600
        self.server.add("j1", "p0", 3002)
        self.server.add("j2", "p0", 3003)
        self.rounds(1)
        self.assertGreaterEqual(self.mock.busy_hits, 1)
        row = self.store.get("j1")
        self.assertEqual(row["state"], "queued")
        self.assertGreater(row["next_attempt"], time.time())  # backing off
        self.assertEqual(self.store.get("j2")["attempts"], 0)  # the rest of the queue waits its turn
        self.rounds(2, due_now=True)  # still busy: nothing lost, nothing printed
        self.assertEqual(len(self.mock.tickets), 0)
        self.assertEqual(self.server.jobs["j1"]["status"], "failed")
        self.mock.busy_until = 0  # the POS is done
        self.rounds(2, due_now=True)
        self.assertEqual(self.wait_tickets(2), 2)
        self.rounds(3, due_now=True)
        self.assertEqual(self.wait_tickets(2), 2)  # each order exactly once
        self.assertEqual({j["status"] for j in self.server.jobs.values()}, {"printed"})

    def test_cover_open_is_shown_and_retried(self):
        self.mock.cover_open = True
        self.server.add("j1", "p0", 3004)
        self.rounds(1)
        self.assertEqual(self.worker.printer_state["p0"]["code"], "cover_open")
        self.assertEqual(self.server.statuses[-1]["printers"]["p0"]["code"], "cover_open")
        self.mock.cover_open = False
        self.rounds(1, due_now=True)
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertTrue(self.worker.printer_state["p0"]["ok"])
        self.assertTrue(self.server.statuses[-1]["printers"]["p0"]["ok"])

    def test_scheduled_reprint_is_its_own_ticket(self):
        order = dict(fulfillment="pickup", pickupAt="2026-11-25T18:30:00-08:00", pickupAtLocal="2026-11-25 18:30")
        self.server.add("j1", "p0", 3005, **order)
        self.rounds(1)
        self.server.add("j2", "p0", 3005, reason="scheduled", **order)
        self.rounds(2)
        self.assertEqual(self.wait_tickets(2), 2)
        restaurant = self.worker.cfg["restaurant"]
        first = render.render(ticket.build(self.server.jobs["j1"]["order"], restaurant), 80)
        second = render.render(ticket.build(self.server.jobs["j2"]["order"], restaurant, reason="scheduled"), 80)
        self.assertEqual(self.mock.image(1).tobytes(), first.tobytes())
        self.assertEqual(self.mock.image(2).tobytes(), second.tobytes())
        self.assertEqual(ticket.build(self.server.jobs["j2"]["order"], restaurant, reason="scheduled")[0].text, "READY BY 6:30 PM · REPRINT")

    def test_dashboard_test_page_and_preview(self):
        self.server.add("t1", "p0", 0, reason="test")
        self.server.preview_requested = "2026-11-25T16:00:00Z"
        self.rounds(2)
        self.assertEqual(self.wait_tickets(2), 2)  # ASAP and scheduled samples, each cut
        self.assertEqual(len(self.server.previews), 1)
        from PIL import Image

        pngs = [Image.open(io.BytesIO(base64.b64decode(b))) for b in self.server.previews[0]]
        # The preview is the very picture the printer got.
        for i, png in enumerate(pngs, start=1):
            self.assertEqual(png.size, self.mock.image(i).size)
            self.assertEqual(png.convert("1").tobytes(), self.mock.image(i).tobytes())
        self.rounds(2)
        self.assertEqual(len(self.server.previews), 1)  # one preview per request

    def test_internet_down_then_back_prints_once(self):
        self.server.online = False
        self.server.add("j1", "p0", 3006)
        self.rounds(2)
        self.assertEqual(len(self.mock.tickets), 0)  # not received yet
        self.server.online = True
        self.rounds(1)
        self.assertEqual(self.wait_tickets(1), 1)
        self.server.jobs["j1"]["status"] = "pending"  # the "printed" report got lost
        self.rounds(3)
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertEqual(self.server.jobs["j1"]["status"], "printed")


if __name__ == "__main__":
    unittest.main()
