"""Finding printers: parsing what Windows prints, merging and ranking, and the live looks (a program
holding a printer connection, SNMP, web page titles) against local stand-ins."""
import http.server
import socket
import subprocess
import sys
import threading
import time
import unittest

from tests.helpers import free_port, temp_home
from mock_printer import MockPrinter

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1012
  TCP    192.168.1.23:52344     192.168.1.50:9100      ESTABLISHED     4120
  TCP    192.168.1.23:52345     192.168.1.60:515       TIME_WAIT       0
  TCP    192.168.1.23:52399     142.250.69.206:443     ESTABLISHED     7788
  TCP    [::]:135               [::]:0                 LISTENING       1012
"""
TASKLIST = '''"System Idle Process","0","Services","0","8 K"
"qMenu.exe","4120","Console","1","182,344 K"
"chrome.exe","7788","Console","1","92,100 K"
'''
ARP_WINDOWS = """
Interface: 192.168.1.23 --- 0x7
  Internet Address      Physical Address      Type
  192.168.1.1           a0-b1-c2-d3-e4-f5     dynamic
  192.168.1.50          00-11-62-aa-bb-cc     dynamic
  192.168.1.77          64-eb-8c-01-02-03     dynamic
  192.168.1.255         ff-ff-ff-ff-ff-ff     static
  224.0.0.22            01-00-5e-00-00-16     static
"""
ARP_MAC = """? (192.168.0.1) at 76:9b:e8:ab:8:31 on en0 ifscope [ethernet]
? (192.168.0.13) at 0:11:62:9:16:8 on en0 ifscope [ethernet]
mdns.mcast.net (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""


def tsunami():
    """What the qMenu tablet at Tsunami Sushi might show: a Star on USB driven by Windows, the
    POS holding a Star LAN printer, an office laser printer and the virtual PDF printer."""
    from xordering_printer import discover

    res = {
        "windowsPrinters": [
            {"name": "Star TSP143IIIU", "driver": "Star TSP100 Cutter (TSP143)", "port": "USB001", "portKind": "usb", "virtual": False, "status": "", "ip": "", "tcpPort": None},
            {"name": "Microsoft Print to PDF", "driver": "Microsoft Print To PDF", "port": "PORTPROMPT:", "portKind": "other", "virtual": True, "status": "", "ip": "", "tcpPort": None},
            {"name": "HP LaserJet Pro M404", "driver": "HP Universal Printing PCL 6", "port": "IP_192.168.1.80", "portKind": "network", "virtual": False, "status": "", "ip": "192.168.1.80", "tcpPort": 9100},
        ],
        "connections": [{"ip": "192.168.1.50", "port": 9100, "state": "ESTABLISHED", "pid": 4120, "process": "qMenu.exe"}],
        "seen": [],
        "arp": [{"ip": "192.168.1.50", "mac": "00:11:62:aa:bb:cc", "interface": "", "vendor": "Star Micronics"},
                {"ip": "192.168.1.1", "mac": "a0:b1:c2:d3:e4:f5", "interface": "", "vendor": ""}],
        "scan": {"192.168.1.50": {"9100": "refused"}, "192.168.1.80": {"9100": "open"}, "192.168.1.1": {"515": "refused"}},
        "identify": {"192.168.1.50": {"http": "", "snmp": "Star TSP143IIILAN"}, "192.168.1.80": {"http": "HP LaserJet Pro M404", "snmp": ""}},
        "usb": [{"name": "Star TSP143IIIU", "id": "USB\\VID_0519&PID_0003\\1", "status": "OK", "class": "Printer", "vendor": "Star Micronics"}],
    }
    res["candidates"] = discover.merge(res)
    return res


class ParseTest(unittest.TestCase):
    def test_netstat_and_tasklist(self):
        from xordering_printer import discover

        conns = discover.parse_netstat(NETSTAT)
        self.assertEqual([(c["ip"], c["port"], c["state"], c["pid"]) for c in conns],
                         [("0.0.0.0", 0, "LISTENING", 1012), ("192.168.1.50", 9100, "ESTABLISHED", 4120), ("192.168.1.60", 515, "TIMEWAIT", 0), ("142.250.69.206", 443, "ESTABLISHED", 7788)])
        self.assertEqual(discover.parse_tasklist(TASKLIST)[4120], "qMenu.exe")

    def test_arp(self):
        from xordering_printer import discover

        rows = discover.parse_arp(ARP_WINDOWS)
        self.assertEqual([r["ip"] for r in rows], ["192.168.1.1", "192.168.1.50", "192.168.1.77"])
        self.assertEqual(rows[1]["vendor"], "Star Micronics")
        self.assertEqual(rows[2]["vendor"], "Seiko Epson")
        self.assertEqual(rows[0]["interface"], "192.168.1.23")
        mac = discover.parse_arp(ARP_MAC)
        self.assertEqual([(r["ip"], r["mac"]) for r in mac], [("192.168.0.1", "76:9b:e8:ab:08:31"), ("192.168.0.13", "00:11:62:09:16:08")])
        self.assertEqual(mac[1]["vendor"], "Star Micronics")

    def test_ports_and_names(self):
        from xordering_printer import discover

        self.assertEqual(discover.port_kind("USB001"), "usb")
        self.assertEqual(discover.port_kind("IP_192.168.1.50"), "network")
        self.assertEqual(discover.ip_in("IP_192.168.1.50"), "192.168.1.50")
        self.assertEqual(discover.ip_in("TCP:192.168.1.51:9100"), "192.168.1.51")
        self.assertEqual(discover.port_in("TCP:192.168.1.51:9101"), 9101)
        self.assertEqual(discover.port_kind("BTHENUM_1"), "bluetooth")
        self.assertEqual(discover.port_kind("COM3:"), "serial")
        self.assertEqual(discover.port_kind("WSD-1234"), "wsd")
        for name, driver, port in (("Microsoft Print to PDF", "", "PORTPROMPT:"), ("Microsoft XPS Document Writer", "", "XPSPort:"), ("OneNote (Desktop)", "", "nul:"), ("Fax", "Microsoft Shared Fax Driver", "SHRFAX:")):
            self.assertTrue(discover.is_virtual_printer(name, driver, port), name)
        self.assertFalse(discover.is_virtual_printer("Star TSP143IIIU", "Star TSP100 Cutter (TSP143)", "USB001"))

    def test_guess_type_and_paper(self):
        from xordering_printer import discover

        self.assertEqual(discover.guess("Star TSP143IIIU")["render"], "star")
        self.assertEqual(discover.guess("TSP100 futurePRNT")["render"], "star")
        self.assertEqual(discover.guess("EPSON TM-T88VI Receipt")["render"], "image")
        self.assertEqual(discover.guess("POS-58 Thermal")["paperWidth"], 58)
        self.assertEqual(discover.guess("XP-80C")["paperWidth"], 80)
        self.assertTrue(discover.guess("Generic 80mm Thermal Printer")["receipt"])
        self.assertTrue(discover.guess("HP LaserJet Pro M404")["office"])
        self.assertFalse(discover.guess("HP LaserJet Pro M404")["receipt"])


class MergeTest(unittest.TestCase):
    def test_tsunami_ranking(self):
        from xordering_printer import discover

        res = tsunami()
        cands = res["candidates"]
        names = [c["name"] for c in cands]
        self.assertNotIn("Microsoft Print to PDF", names)
        receipt = discover.receipt_printers(cands)
        self.assertEqual(len(receipt), 2)
        # The LAN printer qMenu is holding comes first, the USB Star (Windows printer) second, the office printer last.
        lan, usb = receipt
        self.assertEqual(lan["ip"], "192.168.1.50")
        self.assertEqual(lan["process"], "qMenu.exe")
        self.assertIn("in_use", lan["sources"])
        self.assertEqual((lan["render"], lan["connection"], lan["port"]), ("star", "tcp", 9100))
        self.assertIn("qMenu.exe", discover.source_text(lan))
        self.assertEqual(usb["windowsPrinter"], "Star TSP143IIIU")
        self.assertEqual((usb["render"], usb["connection"]), ("star", "windows"))
        self.assertEqual(usb["usb"], "Star TSP143IIIU")  # the USB device joined its Windows printer
        self.assertEqual(cands[-1]["name"], "HP LaserJet Pro M404")
        self.assertTrue(cands[-1]["office"])
        self.assertNotIn("192.168.1.1", [c["ip"] for c in cands])  # the router (ARP only, no printer port)
        s = discover.settings_for(usb, "Kitchen")
        self.assertEqual(s, {"name": "Kitchen", "connection": "windows", "host": "", "port": 9100, "windowsPrinter": "Star TSP143IIIU", "render": "star", "paperWidth": 80})

    def test_single_receipt_printer_and_usb_without_driver(self):
        from xordering_printer import discover

        res = {"windowsPrinters": [], "connections": [], "seen": [], "arp": [], "scan": {"10.0.0.9": {"9100": "open"}},
               "identify": {"10.0.0.9": {"http": "EPSON TM-T20III", "snmp": ""}},
               "usb": [{"name": "Star TSP143IIIU", "id": "USB\\VID_0519&PID_0003\\9", "status": "Error", "class": "", "vendor": "Star Micronics"}]}
        cands = discover.merge(res)
        receipt = discover.receipt_printers(cands)
        self.assertEqual([(c["ip"], c["render"], c["brand"]) for c in receipt], [("10.0.0.9", "image", "Epson")])
        usb = next(c for c in cands if c["usb"])
        self.assertEqual(usb["connection"], "")  # no driver: listed with a note, not printable
        self.assertTrue(any("driver" in n for n in usb["notes"]))

    def test_report_has_everything_and_no_token(self):
        from xordering_printer import discover

        temp_home()
        res = tsunami()
        res.update(version="9.9.9", at=time.time(), computer="TABLET", errors={"usb": "timeout"}, networks=["192.168.1.0/24"],
                   interfaces=[{"name": "Wi-Fi", "ip": "192.168.1.23", "prefix": 24, "network": "192.168.1.0/24", "scan": "192.168.1.0/24", "skipped": ""},
                               {"name": "vEthernet (WSL)", "ip": "172.20.0.1", "prefix": 20, "network": "172.20.0.0/20", "scan": "172.20.0.0/24", "skipped": "virtual"}])
        cfg = {"deviceToken": "xpd_secret_token", "restaurant": {"name": {"en": "Tsunami Sushi"}},
               "printers": [{"id": "p1", "name": "Kitchen", "connection": "windows", "windowsPrinter": "Star TSP143IIIU", "render": "star", "paperWidth": 80}]}
        text = discover.report(res, cfg, {"printers": {"p1": {"ok": False, "error": "offline"}}, "queue": {"queued": 2, "printed": 5}})
        for needle in ("192.168.1.23/24", "skipped: virtual", "Star TSP143IIIU", "USB001", "192.168.1.50:9100 ESTABLISHED by qMenu.exe (pid 4120)",
                       "00:11:62:aa:bb:cc  Star Micronics", "192.168.1.80: 9100 open", "snmp: Star TSP143IIILAN", "Tsunami Sushi",
                       "Windows \"Star TSP143IIIU\"", "waiting 2", "usb: timeout", "[virtual]"):
            self.assertIn(needle, text)
        self.assertNotIn("xpd_secret_token", text)


class LiveTest(unittest.TestCase):
    """Real sockets on this computer: a printer held by another program, SNMP and HTTP."""

    def test_finds_the_program_holding_a_printer(self):
        temp_home()
        from xordering_printer import discover

        port = free_port()
        mock = MockPrinter(port, temp_home(), quiet=True, star=True, exclusive=True).start()
        pos = subprocess.Popen([sys.executable, "-c", f"import socket,time; s=socket.create_connection(('127.0.0.1',{port})); print('up', flush=True); time.sleep(30)"],
                               stdout=subprocess.PIPE)
        try:
            self.assertEqual(pos.stdout.readline().strip(), b"up")
            time.sleep(0.3)
            conns = discover.printer_connections(ports=[port], include_loopback=True)
            mine = [c for c in conns if c["pid"] == pos.pid]
            self.assertEqual(len(mine), 1, conns)
            self.assertIn("python", mine[0]["process"].lower())
            self.assertEqual(mine[0]["state"], "ESTABLISHED")
            self.assertIn("python", discover.who_uses("127.0.0.1", ports=[port], include_loopback=True).lower())
            discover.remember_connections(mine)
            # The whole search, limited to this computer: the held printer is found, named as in use.
            res = discover.run(networks=[], ports=[port], include_loopback=True)
            c = next(c for c in res["candidates"] if c["ip"] == "127.0.0.1")
            self.assertIn("in_use", c["sources"])
            self.assertIn("python", c["process"].lower())
            self.assertIn("127.0.0.1", discover.report(res))
        finally:
            pos.kill()
            pos.wait()
            mock.stop()
        # Gone now, but remembered for a while (a POS connects only while it prints).
        self.assertIn("python", discover.who_uses("127.0.0.1", ports=[port], include_loopback=True).lower())
        self.assertEqual(discover.who_uses("127.0.0.1", ports=[free_port()], include_loopback=True), "")

    def test_snmp_describe(self):
        from xordering_printer import discover

        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]

        def answer():
            data, addr = srv.recvfrom(4096)
            self.assertEqual(data, discover.snmp_get_request())
            T = discover._tlv
            binds = b"".join(T(0x30, discover._ber_oid(oid) + T(0x04, val.encode())) for oid, val in
                             (("1.3.6.1.2.1.1.1.0", "Star Micronics TSP143IIILAN Ver1.2"), ("1.3.6.1.2.1.1.5.0", "TSP143-Kitchen")))
            pdu = T(0xA2, discover._ber_int(0x5850) + discover._ber_int(0) + discover._ber_int(0) + T(0x30, binds))
            srv.sendto(T(0x30, discover._ber_int(0) + T(0x04, b"public") + pdu), addr)

        t = threading.Thread(target=answer, daemon=True)
        t.start()
        try:
            text = discover.snmp_describe("127.0.0.1", port=port)
        finally:
            t.join(3)
            srv.close()
        self.assertEqual(text, "Star Micronics TSP143IIILAN Ver1.2 · TSP143-Kitchen")
        self.assertEqual(discover.brand(text), "Star")
        self.assertEqual(discover.snmp_describe("127.0.0.1", timeout=0.3, port=free_port()), "")

    def test_http_title(self):
        from xordering_printer import discover

        class H(http.server.BaseHTTPRequestHandler):
            server_version = "EPSON_Linux UPnP/1.0"
            sys_version = ""

            def do_GET(self):
                body = b"<html><head><title>EPSON TM-T88VI</title></head></html>"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            text = discover.http_title("127.0.0.1", srv.server_address[1])
        finally:
            srv.shutdown()
        self.assertEqual(text, "EPSON TM-T88VI · EPSON_Linux UPnP/1.0")
        self.assertEqual(discover.guess(text)["render"], "image")

    def test_scan_open_and_refused(self):
        from xordering_printer import discover

        port = free_port()
        mock = MockPrinter(port, temp_home(), quiet=True).start()
        closed = free_port()
        try:
            res = discover.scan([], ports=[port, closed], timeout=3, extra_hosts=["127.0.0.1"])
        finally:
            mock.stop()
        self.assertEqual(res["127.0.0.1"][port], "open")
        self.assertEqual(res["127.0.0.1"].get(closed), "refused")

    def test_interfaces_skip_loopback(self):
        from xordering_printer import discover

        nics = discover.interfaces()
        self.assertTrue(nics)
        lo = [n for n in nics if n["ip"].startswith("127.")]
        self.assertTrue(all(n["skipped"] for n in lo))
        for n in nics:
            self.assertGreaterEqual(int(n["scan"].split("/")[1]), 24)


if __name__ == "__main__":
    unittest.main()
