"""The program's email sign-in (the dashboard's 6-digit email code, the ordering-auth function): no SMS."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from xordering_printer import api, setup


class _Fake(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        _Fake.calls.append((self.path, body, self.headers.get("apikey")))
        if self.path.endswith("/ordering-auth/send"):
            if body["email"] == "busy@example.com":
                return self._json(429, {"error": "cooldown", "waitSeconds": 42})
            return self._json(200, {"ok": True, "expiresInMinutes": 10, "cooldownSeconds": 60})
        if self.path.endswith("/ordering-auth/verify"):
            if body["code"] != "123456":
                return self._json(400, {"error": "invalid_code", "attemptsLeft": 3})
            return self._json(200, {"session": {"access_token": "at-owner", "refresh_token": "rt"}, "restaurantsAdded": 0})
        return self._json(404, {"error": "not_found"})

    def _json(self, status, value):
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class EmailSignInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.client = api.Client(f"http://127.0.0.1:{cls.server.server_port}", "sb_publishable_test")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Fake.calls.clear()

    def test_sends_and_verifies_through_ordering_auth(self):
        self.assertEqual(self.client.send_code(" Owner@Tsunami.example ", "zh"), {"ok": True, "expiresInMinutes": 10, "cooldownSeconds": 60})
        self.assertEqual(self.client.verify_code("owner@tsunami.example", " 123 456 "), "at-owner")
        self.assertEqual([c[0] for c in _Fake.calls], ["/functions/v1/ordering-auth/send", "/functions/v1/ordering-auth/verify"])
        self.assertEqual(_Fake.calls[0][1], {"email": "owner@tsunami.example", "locale": "zh", "app": "printer"})
        self.assertEqual(_Fake.calls[1][1], {"email": "owner@tsunami.example", "code": "123456"})
        self.assertEqual(_Fake.calls[0][2], "sb_publishable_test")

    def test_errors_carry_the_details(self):
        with self.assertRaises(api.ApiError) as wrong:
            self.client.verify_code("owner@tsunami.example", "000000")
        self.assertEqual((wrong.exception.code, wrong.exception.payload.get("attemptsLeft")), ("invalid_code", 3))
        with self.assertRaises(api.ApiError) as busy:
            self.client.send_code("busy@example.com")
        self.assertEqual((busy.exception.code, busy.exception.payload.get("waitSeconds")), ("cooldown", 42))

    def test_email_is_checked_before_sending(self):
        self.assertEqual(setup.normalize_email("  Owner@Tsunami.Example "), "owner@tsunami.example")
        for bad in ("", "+16045550100", "owner@", "no spaces@x.co"):
            with self.assertRaises(api.ApiError) as e:
                setup.normalize_email(bad)
            self.assertEqual(e.exception.code, "invalid_email")

    def test_no_sms_anywhere(self):
        root = Path(__file__).resolve().parent.parent / "xordering_printer"
        for f in root.glob("*.py"):
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("/auth/v1/otp", text, f.name)
            self.assertNotIn('"sms"', text, f.name)
            self.assertNotIn("normalize_phone", text, f.name)

    def test_window_recommends_the_device_key(self):
        text = (Path(__file__).resolve().parent.parent / "xordering_printer" / "gui.py").read_text(encoding="utf-8")
        self.assertIn("推荐：在后台 Settings > Kitchen printers（设置 → 后厨打印机）生成设备密钥，粘贴到这里。", text)
        self.assertIn("邮箱登录 Email sign-in", text)
        self.assertNotIn("手机号登录", text)


if __name__ == "__main__":
    unittest.main()
