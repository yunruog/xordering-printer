"""Star Graphic Mode (TSP100 / TSP143III): the bytes checked against the command spec, not just "sent".

decode_star (tools/mock_printer.py) is a separate, strict reading of the STAR Graphic Mode Command
Specifications: it rejects any byte that isn't part of a command, numbers that aren't ASCII decimal
with a NUL, raster lines longer than 72 bytes, data outside raster mode, a document that isn't
ended (form feed / quit) and raster mode left on.
"""
import socket
import time
import unittest
from unittest import mock

from tests.helpers import ORDER, RESTAURANT, free_port, temp_home
from mock_printer import MockPrinter, StarFormatError, decode_star
from xordering_printer import escpos, printer, render, star, ticket

STAR = {"id": "p0", "name": "Tsunami", "host": "127.0.0.1", "port": 9100, "render": "star", "paperWidth": 80, "enabled": True}


def pixels(img):
    return img.convert("1").tobytes()


class StarBytesTest(unittest.TestCase):
    def setUp(self):
        self.lines = ticket.build(dict(ORDER, fulfillment="pickup", readyAtLocal="16:10"), RESTAURANT)
        self.img = render.render(self.lines, 80)
        self.data = star.raster_bytes(self.img)

    def test_command_sequence(self):
        d = self.data
        # Initialize, enter raster mode, continuous paper, FF mode 13 (feed + partial cut) ...
        self.assertTrue(d.startswith(b"\x1b*rR" + b"\x1b*rA" + b"\x1b*rP0\x00" + b"\x1b*rF13\x00"))
        # ... raster lines ... then form feed (print + cut) and quit raster mode.
        self.assertTrue(d.endswith(b"\x1b\x0c\x00" + b"\x1b*rB"))
        # No ESC/POS anywhere: no GS v 0 picture, no ESC @, no GS V cut, no DLE EOT.
        doc = decode_star(d)
        self.assertEqual(doc["commands"][:4], ["init", "enter", "P0", "F13"])
        self.assertEqual(doc["commands"][-2:], ["FF", "quit"])

    def test_round_trip_is_the_rendered_picture(self):
        doc = decode_star(self.data)
        self.assertEqual(len(doc["images"]), 1)
        self.assertEqual(doc["cuts"], 1)
        rows = doc["images"][0]
        self.assertEqual(len(rows), self.img.size[1])
        self.assertTrue(all(len(r) == 72 for r in rows))
        self.assertEqual(b"".join(bytes(b ^ 0xFF for b in r) for r in rows), pixels(self.img))

    def test_raster_lines_and_blank_runs(self):
        d = self.data
        i, lines, blanks = 0, 0, 0
        body = d[len(b"\x1b*rR\x1b*rA\x1b*rP0\x00\x1b*rF13\x00"):-len(b"\x1b\x0c\x00\x1b*rB")]
        while i < len(body):
            if body[i] == ord("b"):
                k = body[i + 1] + body[i + 2] * 256
                self.assertTrue(1 <= k <= 72)
                self.assertNotEqual(body[i + 2 + k], 0)  # trailing white bytes are left out
                i += 3 + k
                lines += 1
            else:
                self.assertTrue(body.startswith(b"\x1b*rY", i), body[i:i + 8])
                end = body.index(b"\x00", i)
                self.assertTrue(body[i + 4:end].isdigit())
                blanks += int(body[i + 4:end])
                i = end + 1
        self.assertEqual(lines + blanks, self.img.size[1])
        self.assertGreater(blanks, 0)

    def test_test_page_is_two_cut_tickets(self):
        data = escpos.tickets_bytes(ticket.test_tickets(RESTAURANT), STAR)
        doc = decode_star(data)
        self.assertEqual((len(doc["images"]), doc["cuts"]), (2, 2))

    def test_printer_type_picks_the_command_set(self):
        self.assertTrue(escpos.ticket_bytes(self.lines, STAR).startswith(b"\x1b*rR"))
        self.assertTrue(escpos.ticket_bytes(self.lines, dict(STAR, render="image")).startswith(b"\x1b@"))
        self.assertTrue(escpos.ticket_bytes(self.lines, dict(STAR, render="text")).startswith(b"\x1b@\x1c&"))
        # A Star printer is always 80 mm (576 dots), whatever paper width is stored.
        self.assertEqual(len(decode_star(escpos.ticket_bytes(self.lines, dict(STAR, paperWidth=58)))["images"][0][0]), 72)

    def test_strict_parser_rejects_wrong_bytes(self):
        bad = {
            "ESC/POS picture": escpos.image_bytes(self.lines, 80),
            "ESC/POS text": escpos.text_bytes(self.lines, 80),
            "line too long": b"\x1b*rA" + b"b" + bytes([73, 0]) + b"\xff" * 73 + b"\x1b\x0c\x00\x1b*rB",
            "binary number": b"\x1b*rA\x1b*rP\x00\x00\x1b*rB",
            "number without NUL": b"\x1b*rA\x1b*rP0",
            "bad FF mode": b"\x1b*rA\x1b*rF7\x00\x1b*rB",
            "data outside raster mode": b"b" + bytes([1, 0]) + b"\xff",
            "not quit": b"\x1b*rA" + b"b\x01\x00\xff" + b"\x1b\x0c\x00",
            "truncated line": b"\x1b*rA" + b"b\x10\x00\xff",
        }
        for name, data in bad.items():
            with self.assertRaises(StarFormatError, msg=name):
                decode_star(data)

    def test_status_bytes(self):
        ok = bytes([0x23, 0x86, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(star.parse_status(ok), {"coverOpen": False, "offline": False, "paperEmpty": False, "paperLow": False})
        self.assertTrue(star.parse_status(bytes([0x23, 0x86, 0x28, 0, 0, 0, 0, 0, 0]))["coverOpen"])
        self.assertTrue(star.parse_status(bytes([0x23, 0x86, 0x08, 0, 0, 0x08, 0, 0, 0]))["paperEmpty"])
        self.assertTrue(star.parse_status(bytes([0x23, 0x86, 0, 0, 0, 0x04, 0, 0, 0]))["paperLow"])
        self.assertIsNone(star.parse_status(b"\x12"))


class StarPrinterTest(unittest.TestCase):
    """Sending to a mock TSP100 over TCP, status included."""

    def setUp(self):
        temp_home()
        self.port = free_port()
        self.mock = MockPrinter(self.port, out=f"{temp_home()}/star", quiet=True, star=True).start()
        self.payload = escpos.ticket_bytes(ticket.build(ORDER, RESTAURANT), STAR)

    def tearDown(self):
        self.mock.stop()

    def wait_tickets(self, n, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end and len(self.mock.tickets) < n:
            time.sleep(0.05)
        return len(self.mock.tickets)

    def test_prints_and_checks_status(self):
        st = printer.send("127.0.0.1", self.port, self.payload, kind="star")
        self.assertEqual(st, {"ok": True, "paperLow": False})
        self.assertEqual(self.wait_tickets(1), 1)
        self.assertEqual(self.mock.format_errors, [])
        self.assertGreaterEqual(self.mock.status_requests, 2)  # before (ready?) and after (delivered)
        self.assertEqual(pixels(self.mock.image(1)), pixels(render.render(ticket.build(ORDER, RESTAURANT), 80)))

    def test_cover_open_and_paper_out_fail_before_printing(self):
        self.mock.cover_open = True
        with self.assertRaises(printer.PrinterError) as e:
            printer.send("127.0.0.1", self.port, self.payload, kind="star")
        self.assertEqual(e.exception.code, "cover_open")
        self.mock.cover_open, self.mock.paper_out = False, True
        with self.assertRaises(printer.PrinterError) as e:
            printer.send("127.0.0.1", self.port, self.payload, kind="star")
        self.assertEqual(e.exception.code, "paper_out")
        time.sleep(0.2)
        self.assertEqual(len(self.mock.tickets), 0)

    def test_paper_low_is_reported(self):
        self.mock.paper_low = True
        self.assertTrue(printer.send("127.0.0.1", self.port, self.payload, kind="star")["paperLow"])

    def test_busy_with_the_pos(self):
        self.mock.busy_until = time.time() + 30
        with self.assertRaises(printer.PrinterError) as e:
            printer.send("127.0.0.1", self.port, self.payload, kind="star")
        self.assertIn(e.exception.code, ("busy", "lost"))
        self.mock.busy_until = 0
        printer.send("127.0.0.1", self.port, self.payload, kind="star")
        self.assertEqual(self.wait_tickets(1), 1)


class ConnectionTest(unittest.TestCase):
    def test_refused(self):
        with self.assertRaises(printer.PrinterError) as e:
            printer.probe("127.0.0.1", free_port())
        self.assertEqual(e.exception.code, "refused")

    def test_timeout(self):
        with mock.patch("socket.create_connection", side_effect=socket.timeout()):
            with self.assertRaises(printer.PrinterError) as e:
                printer.probe("192.168.1.250", 9100, timeout=1)
        self.assertEqual(e.exception.code, "timeout")
        self.assertIn("timed out", str(e.exception))

    def test_probe_and_scan_find_a_printer(self):
        import ipaddress

        temp_home()
        port = free_port()
        m = MockPrinter(port, out=f"{temp_home()}/scan", quiet=True, star=True).start()
        try:
            res = printer.probe("127.0.0.1", port, kind="star")
            self.assertTrue(res["ok"])
            self.assertFalse(res["status"]["coverOpen"])
            found = printer.scan(port, networks=[ipaddress.IPv4Network("127.0.0.0/29")], timeout=0.3)
            self.assertEqual(found, ["127.0.0.1"])
            time.sleep(0.2)
            self.assertEqual(m.tickets, [])  # probing and scanning print nothing
        finally:
            m.stop()


if __name__ == "__main__":
    unittest.main()
