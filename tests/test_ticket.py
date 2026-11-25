import unittest

from tests.helpers import ORDER, RESTAURANT  # noqa: F401  (sets sys.path)
from mock_printer import decode
from xordering_printer import escpos, ticket


class TicketTest(unittest.TestCase):
    def test_widths_and_wrapping(self):
        self.assertEqual(ticket.width("宫保鸡丁"), 8)
        self.assertEqual(ticket.width("Tea 茶"), 6)
        self.assertEqual(ticket.columns(80), 48)
        self.assertEqual(ticket.columns(80, 2), 24)
        self.assertEqual(ticket.columns(58), 32)
        rows = ticket.wrap("宫保鸡丁宫保鸡丁宫保鸡丁宫保鸡丁", 10)
        self.assertTrue(all(ticket.width(r) <= 10 for r in rows))
        self.assertEqual("".join(rows), "宫保鸡丁宫保鸡丁宫保鸡丁宫保鸡丁")
        self.assertEqual(ticket.wrap("extra spicy please", 12), ["extra spicy", "please"])

    def test_right_aligned_prices(self):
        rows = ticket.layout(ticket.Line("小计 Subtotal", right="$30.76"), 80)
        self.assertEqual(len(rows), 1)
        self.assertEqual(ticket.width(rows[0]), 48)
        self.assertTrue(rows[0].endswith("$30.76"))

    def test_ticket_has_everything_the_kitchen_needs(self):
        lines = ticket.build(ORDER, RESTAURANT, reprint=True, printer_name="Kitchen 1")
        text = "\n".join(r for line in lines for r in ticket.layout(line, 80))
        for expected in ["金莲小馆", "Golden Lotus", "重新打印 REPRINT", "#1042", "堂食 桌号 12", "2026-11-25 18:32", "2 x 宫保鸡丁", "Kung Pao Chicken",
                         "辣度: 特辣 Hot (+$1.00)", "少放花生", "$27.76", "小计 Subtotal", "$30.76", "税 Tax", "$1.54", "小费 Tip", "$3.00",
                         "合计 TOTAL", "$35.30", "不要香菜，谢谢", "陈先生", "1 x Jasmine Tea", "到店付款"]:
            self.assertIn(expected, text)
        pickup = "\n".join(r for line in ticket.build(dict(ORDER, fulfillment="pickup", tableLabel=None, tipCents=0), RESTAURANT) for r in ticket.layout(line, 58))
        self.assertIn("自取 PICKUP", pickup)
        self.assertNotIn("小费", pickup)
        self.assertNotIn("REPRINT", pickup)

    def test_text_mode_is_gbk_with_cut(self):
        data = escpos.text_bytes(ticket.build(ORDER, RESTAURANT), 80)
        self.assertTrue(data.startswith(b"\x1b@\x1c&"))
        self.assertIn("宫保鸡丁".encode("gbk"), data)
        self.assertTrue(data.endswith(b"\x1dVB\x00"))
        self.assertIn(b"\x1d!\x11", data)  # double size for the dishes
        text, rasters, cuts = decode(data)
        self.assertIn("2 x 宫保鸡丁", text)
        self.assertEqual((rasters, cuts), ([], 1))

    def test_image_mode_draws_the_ticket(self):
        lines = ticket.build(ORDER, RESTAURANT)
        img = escpos.render_image(lines, 80)
        self.assertEqual(img.size[0], 576)
        self.assertGreater(img.size[1], 500)
        data = escpos.image_bytes(lines, 80)
        text, rasters, cuts = decode(data)
        self.assertEqual(cuts, 1)
        self.assertEqual(sum(h for _, h, _ in rasters), img.size[1])
        self.assertTrue(all(xb == 72 for xb, _, _ in rasters))
        black = sum(bin(b).count("1") for _, _, bits in rasters for b in bits)
        self.assertGreater(black, 5000)  # there is ink
        self.assertEqual(escpos.render_image(lines, 58).size[0], 384)


if __name__ == "__main__":
    unittest.main()
