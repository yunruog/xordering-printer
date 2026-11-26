import unittest

from tests.helpers import ORDER, RESTAURANT  # noqa: F401  (sets sys.path)
from mock_printer import decode
from xordering_printer import escpos, render, ticket


def text_of(lines, paper=80):
    return "\n".join(r for line in lines for r in ticket.layout(line, paper))


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

    def test_right_aligned_amounts(self):
        rows = ticket.layout(ticket.Line("Subtotal", right="30.76"), 80)
        self.assertEqual(len(rows), 1)
        self.assertEqual(ticket.width(rows[0]), 48)
        self.assertTrue(rows[0].endswith("30.76"))
        # A long dish name wraps beside the quantity column; the amount ends the last row.
        rows = ticket.layout(ticket.Line("Shrimp Tempura Appetizer with Spicy Mayo and Extra Crispy Flakes", lead="2", right="19.90"), 80)
        self.assertGreater(len(rows), 1)
        self.assertTrue(rows[0].startswith("2   Shrimp"))
        self.assertTrue(all(r.startswith("    ") for r in rows[1:]))
        self.assertTrue(rows[-1].endswith("19.90"))

    def test_online_order_ticket(self):
        order = dict(ORDER, fulfillment="pickup", tableLabel=None, placedAtLocal="15:55", readyAtLocal="16:10",
                     customer={"name": "Alan", "phone": "+15095550134"})
        lines = ticket.build(order, {"name": {"en": "Tsunami Sushi"}, "currency": "USD"})
        # The pickup time comes first, in the biggest type.
        self.assertEqual(lines[0].text, "PICKUP: ASAP (~4:10 PM)")
        self.assertEqual(lines[0].size, 3)
        self.assertEqual(max(line.size for line in lines), 3)
        text = text_of(lines)
        for expected in ["TSUNAMI SUSHI", "ONLINE ORDER · PICKUP", "Order #1042", "Placed 3:55 PM", "Alan · (509) 555-0134",
                         "Kung Pao Chicken", "宫保鸡丁", "@13.88", "27.76", "Spice: Hot / 特辣 (+1.00)", "Note: 少放花生",
                         "Note: 不要香菜，谢谢", "Subtotal", "30.76", "Tax", "1.54", "TOTAL", "$35.30", "NOT PAID · PAY AT PICKUP"]:
            self.assertIn(expected, text)
        order_lines = [line.text for line in lines]
        self.assertLess(order_lines.index("TSUNAMI SUSHI"), order_lines.index("Order #1042"))
        self.assertNotIn("REPRINT", text)

    def test_scheduled_order_and_its_reprint(self):
        order = dict(ORDER, fulfillment="pickup", pickupAt="2026-11-25T18:30:00-08:00", pickupAtLocal="2026-11-25 18:30")
        first = ticket.build(order, RESTAURANT)
        self.assertEqual(first[0].text, "★ SCHEDULED 6:30 PM ★")
        self.assertTrue(first[0].invert and first[0].size == 3)
        again = ticket.build(order, RESTAURANT, reason="scheduled")
        self.assertEqual(again[0].text, "READY BY 6:30 PM · REPRINT")
        self.assertTrue(again[0].invert)
        self.assertEqual(again[1].text, "★ SCHEDULED 6:30 PM ★")
        self.assertEqual(ticket.build(ORDER, RESTAURANT, reason="reprint")[0].text, "REPRINT")

    def test_dine_in_headline(self):
        self.assertEqual(ticket.build(ORDER, RESTAURANT)[0].text, "DINE-IN · TABLE 12")

    def test_test_page_has_both_pickup_styles(self):
        tickets = ticket.test_tickets({"name": {"en": "Tsunami Sushi"}, "taxRateBps": 890, "prepMinutes": 15})
        self.assertEqual(len(tickets), 2)
        heads = [[line.text for line in t[:2]] for t in tickets]
        self.assertEqual(heads[0], ["TEST PRINT · 测试打印", "PICKUP: ASAP (~4:10 PM)"])
        self.assertEqual(heads[1], ["TEST PRINT · 测试打印", "★ SCHEDULED 6:30 PM ★"])
        text = text_of(tickets[0])
        self.assertIn("Shrimp Tempura Appetizer", text)
        self.assertIn("少放酱油", text)

    def test_text_mode_is_gbk_with_cut(self):
        data = escpos.text_bytes(ticket.build(ORDER, RESTAURANT), 80)
        self.assertTrue(data.startswith(b"\x1b@\x1c&"))
        self.assertIn("宫保鸡丁".encode("gbk"), data)
        self.assertTrue(data.endswith(b"\x1dVB\x00"))
        self.assertIn(b"\x1d!\x11", data)  # double size for the headline
        text, rasters, cuts = decode(data)
        self.assertIn("Kung Pao Chicken", text)
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

    def test_bundled_font_draws_chinese(self):
        self.assertTrue(render.FONT_FILE.exists())
        self.assertEqual(render.font_path(), str(render.FONT_FILE))
        from PIL import Image, ImageDraw

        f = render.font(25)

        def glyph(ch):
            img = Image.new("L", (40, 40), 255)
            ImageDraw.Draw(img).text((4, 4), ch, font=f, fill=0)
            return img.tobytes()

        missing = glyph("\U000F0000")  # a private-use character: what a missing glyph looks like
        for ch in "少放酱油谢★":
            g = glyph(ch)
            self.assertGreater(sum(1 for v in g if v < 128), 20, ch)
            self.assertNotEqual(g, missing, ch)

    def test_headline_fits_on_one_line(self):
        for text, size in (("PICKUP: ASAP (~12:45 PM)", 3), ("★ SCHEDULED 12:45 PM ★", 3), ("READY BY 12:45 PM · REPRINT", 2), ("TEST PRINT · 测试打印", 2)):
            line = ticket.Line(text, size=size, align="center", bold=True, invert=not text.startswith("PICKUP"))
            _, _, rows = render._rows(line, 576, 1.0)
            self.assertEqual(len(rows), 1, text)


if __name__ == "__main__":
    unittest.main()
