import time
import unittest

from tests.helpers import ORDER, free_port, temp_home
from mock_printer import MockPrinter


class FakeServer:
    """print_device_poll / report semantics in memory (the real ones: tests in yunruog/xordering)."""

    def __init__(self, printers):
        self.printers = printers
        self.jobs = {}
        self.online = True
        self.revoked = False
        self.reports = []

    def add(self, job_id, printer_id, number, reason="new"):
        self.jobs[job_id] = {"id": job_id, "printerId": printer_id, "reason": reason, "status": "pending", "order": dict(ORDER, number=number)}

    def client(self, cfg):
        server = self
        from xordering_printer import api

        class C:
            def poll(self, token):
                if not server.online:
                    raise api.NetworkError("offline")
                if server.revoked:
                    raise api.ApiError("device_revoked", 400)
                return {"restaurant": {"id": "r-1", "name": {"zh": "金莲小馆", "en": "Golden Lotus"}, "currency": "CAD"}, "printers": server.printers, "device": {"name": "PC"},
                        "jobs": [j for j in server.jobs.values() if j["status"] in ("pending", "failed")]}

            def report(self, token, job_id, status, error=None):
                if not server.online:
                    raise api.NetworkError("offline")
                server.reports.append((job_id, status))
                j = server.jobs[job_id]
                if j["status"] != "printed":
                    j["status"] = status
                return {"status": j["status"]}

        return C()


class WorkerTest(unittest.TestCase):
    def setUp(self):
        temp_home()
        from xordering_printer import config
        from xordering_printer.jobs import JobStore
        from xordering_printer.worker import Worker

        self.ports = [free_port(), free_port()]
        self.printers = [{"id": f"p{i}", "name": f"Kitchen {i + 1}", "host": "127.0.0.1", "port": p, "enabled": True, "paperWidth": 80, "render": "text"} for i, p in enumerate(self.ports)]
        self.server = FakeServer(self.printers)
        self.mocks = [MockPrinter(p, out=f"{config.config_path().parent}/mock{p}", quiet=True).start() for p in self.ports]
        config.update(supabaseUrl="http://fake", publishableKey="k", deviceToken="xpd_" + "a" * 64)
        self.store = JobStore(str(config.config_path().parent / "jobs.db"))
        self.worker = Worker(store=self.store, client_factory=self.server.client)
        self.Worker, self.JobStore, self.config = Worker, JobStore, config

    def tearDown(self):
        for m in self.mocks:
            m.stop()

    def rounds(self, n=1):
        for _ in range(n):
            self.worker.step()
            time.sleep(0.05)

    def wait_count(self, mock, needle, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and mock.count(needle) < n:
            time.sleep(0.05)
        return mock.count(needle)

    def test_prints_once_per_printer_and_reports(self):
        self.server.add("j1", "p0", 2001)
        self.server.add("j2", "p1", 2001)
        self.rounds(4)
        self.assertEqual([self.wait_count(m, "#2001", 1) for m in self.mocks], [1, 1])
        self.assertEqual({j["status"] for j in self.server.jobs.values()}, {"printed"})
        self.rounds(3)
        time.sleep(0.3)
        self.assertEqual([m.count("#2001") for m in self.mocks], [1, 1])

    def test_printer_offline_then_back(self):
        self.mocks[1].stop()
        self.server.add("j1", "p0", 2002)
        self.server.add("j2", "p1", 2002)
        self.rounds(2)
        self.assertEqual(self.wait_count(self.mocks[0], "#2002", 1), 1)
        self.assertEqual(self.server.jobs["j2"]["status"], "failed")
        self.assertIn(("j2", "failed"), self.server.reports)
        self.mocks[1] = MockPrinter(self.ports[1], out=self.mocks[1].out, quiet=True).start()
        self.store.db.execute("update jobs set next_attempt = 0")
        self.rounds(2)
        self.assertEqual(self.wait_count(self.mocks[1], "#2002", 1), 1)
        self.assertEqual(self.server.jobs["j2"]["status"], "printed")
        self.assertEqual(self.mocks[0].count("#2002"), 1)

    def test_paper_out_is_a_failure(self):
        self.mocks[0].paper_out = True
        self.server.add("j1", "p0", 2003)
        self.rounds(1)
        self.assertEqual(self.store.get("j1")["state"], "queued")
        self.assertIn("paper", self.store.get("j1")["last_error"])
        self.mocks[0].paper_out = False
        self.store.db.execute("update jobs set next_attempt = 0")
        self.rounds(1)
        self.assertEqual(self.wait_count(self.mocks[0], "#2003", 1), 1)

    def test_network_down_prints_received_tickets_and_reports_later(self):
        self.server.add("j1", "p0", 2004)
        self.worker.poll(self.server.client(None), "t")  # received...
        self.server.online = False  # ...then the internet drops
        self.rounds(2)
        self.assertEqual(self.wait_count(self.mocks[0], "#2004", 1), 1)
        self.assertEqual(self.server.jobs["j1"]["status"], "pending")  # not reported yet
        self.assertEqual(self.worker.state, "offline")
        self.server.online = True
        self.rounds(2)
        self.assertEqual(self.server.jobs["j1"]["status"], "printed")
        self.assertEqual(self.mocks[0].count("#2004"), 1)

    def test_lost_report_does_not_reprint(self):
        self.server.add("j1", "p0", 2005)
        self.rounds(1)
        self.assertEqual(self.wait_count(self.mocks[0], "#2005", 1), 1)
        # The server never got the report (say it timed out): it lists the job again.
        self.server.jobs["j1"]["status"] = "pending"
        self.rounds(3)
        time.sleep(0.3)
        self.assertEqual(self.mocks[0].count("#2005"), 1)
        self.assertEqual(self.server.jobs["j1"]["status"], "printed")

    def test_queue_survives_a_restart(self):
        self.mocks[0].stop()
        self.server.add("j1", "p0", 2006)
        self.rounds(1)
        self.server.online = False
        # The program restarts (crash, update) while the printer and the internet are down.
        self.store.close()
        store = self.JobStore(str(self.config.config_path().parent / "jobs.db"))
        worker = self.Worker(store=store, client_factory=self.server.client)
        self.mocks[0] = MockPrinter(self.ports[0], out=self.mocks[0].out, quiet=True).start()
        store.db.execute("update jobs set next_attempt = 0")
        worker.step()
        self.assertEqual(self.wait_count(self.mocks[0], "#2006", 1), 1)
        self.store = store

    def test_reprint_is_a_new_job(self):
        self.server.add("j1", "p0", 2007)
        self.rounds(1)
        self.server.add("j9", "p0", 2007, reason="reprint")
        self.rounds(2)
        self.assertEqual(self.wait_count(self.mocks[0], "#2007", 2), 2)
        self.assertEqual(self.mocks[0].count("REPRINT"), 1)

    def test_withdrawn_tickets_are_not_printed(self):
        self.mocks[0].stop()
        self.server.add("j1", "p0", 2008)
        self.rounds(1)
        del self.server.jobs["j1"]  # the order was cancelled before it printed
        self.mocks[0] = MockPrinter(self.ports[0], out=self.mocks[0].out, quiet=True).start()
        self.rounds(2)
        time.sleep(0.3)
        self.assertEqual(self.mocks[0].count("#2008"), 0)

    def test_revoked_device_stops(self):
        self.server.revoked = True
        self.server.add("j1", "p0", 2009)
        self.rounds(2)
        self.assertEqual(self.worker.state, "revoked")
        self.assertEqual(self.mocks[0].count("#2009"), 0)
        # Connecting again (a new token) resumes.
        self.server.revoked = False
        self.config.update(deviceToken="xpd_" + "b" * 64)
        time.sleep(0.02)
        self.worker.cfg_mtime = 0
        self.rounds(1)
        self.assertEqual(self.wait_count(self.mocks[0], "#2009", 1), 1)


if __name__ == "__main__":
    unittest.main()
