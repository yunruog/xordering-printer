"""The kitchen ticket: an order laid out as lines, independent of how it is printed.

escpos.py prints these lines as ESC/POS text (Chinese in GBK) or as a picture (image mode). Widths
count characters the way receipt printers do: a Chinese character takes two columns. 80 mm paper
has 48 columns (Font A), 58 mm has 32; double-size text has half as many.
"""
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Line:
    text: str = ""
    right: str = ""
    # 1: normal; 2: double width and height (headings, dishes)
    size: int = 1
    align: str = "left"  # left | center
    bold: bool = False
    invert: bool = False
    kind: str = "text"  # text | rule | feed


CURRENCY = {"CAD": "$", "USD": "$", "AUD": "$", "HKD": "HK$", "CNY": "¥", "GBP": "£", "EUR": "€", "TWD": "NT$"}


def money(cents: Optional[int], currency: str = "CAD") -> str:
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{sign}{CURRENCY.get(currency, currency + ' ')}{a // 100}.{a % 100:02d}"


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 0 if unicodedata.combining(c) else 1 for c in s)


def columns(paper_width: int, size: int = 1) -> int:
    base = 32 if int(paper_width) == 58 else 48
    return base // size


def wrap(text: str, cols: int) -> List[str]:
    """Greedy wrap: Latin words stay whole when they fit; CJK breaks between any two characters."""
    out: List[str] = []
    for para in (text or "").replace("\r", "").split("\n"):
        tokens: List[str] = []
        word = ""
        for ch in para:
            if ch == " " or width(ch) == 2:
                if word:
                    tokens.append(word)
                    word = ""
                tokens.append(ch)
            else:
                word += ch
        if word:
            tokens.append(word)
        line = ""
        for tok in tokens:
            if width(line + tok) <= cols:
                line += tok
                continue
            if line.strip():
                out.append(line.rstrip())
            line = "" if tok == " " else tok
            while width(line) > cols:  # a word longer than the line
                cut = 0
                while width(line[: cut + 1]) <= cols:
                    cut += 1
                out.append(line[:cut])
                line = line[cut:]
        out.append(line.rstrip())
    return out or [""]


def layout(line: Line, paper_width: int) -> List[str]:
    """One Line as the rows it prints, padded to the line width (right text right-aligned)."""
    cols = columns(paper_width, line.size)
    if line.kind == "rule":
        return ["-" * columns(paper_width)]
    if line.kind == "feed":
        return [""]
    if not line.right:
        rows = wrap(line.text, cols)
        if line.align == "center":
            return [" " * max(0, (cols - width(r)) // 2) + r for r in rows]
        return rows
    rows = wrap(line.text, cols)
    last = rows[-1]
    if width(last) + 1 + width(line.right) <= cols:
        rows[-1] = last + " " * (cols - width(last) - width(line.right)) + line.right
    else:
        rows.append(" " * max(0, cols - width(line.right)) + line.right)
    return rows


def name_parts(value: Any) -> List[str]:
    """A localized name as [Chinese, English] (each only when present and different)."""
    if isinstance(value, dict):
        zh = str(value.get("zh") or "").strip()
        en = str(value.get("en") or "").strip()
        return [x for x in (zh, en if en != zh else "") if x]
    return [str(value or "").strip()]


FULFILLMENT = {"pickup": "自取 PICKUP", "dine_in": "堂食 DINE-IN", "delivery": "外送 DELIVERY"}


def build(order: Dict[str, Any], restaurant: Dict[str, Any], *, reprint: bool = False, printer_name: str = "") -> List[Line]:
    cur = order.get("currency") or restaurant.get("currency") or "CAD"
    lines: List[Line] = []
    names = name_parts(restaurant.get("name"))
    lines.append(Line(names[0] if names else "", size=2, align="center", bold=True))
    if len(names) > 1:
        lines.append(Line(names[1], align="center"))
    if reprint:
        lines.append(Line(" 重新打印 REPRINT ", align="center", bold=True, invert=True))
    lines.append(Line("", kind="feed"))
    lines.append(Line(f"#{int(order.get('number') or 0):04d}", size=2, align="center", bold=True))
    how = FULFILLMENT.get(order.get("fulfillment") or "pickup", str(order.get("fulfillment")))
    if order.get("fulfillment") == "dine_in" and order.get("tableLabel"):
        how = f"堂食 桌号 {order['tableLabel']}"
    lines.append(Line(how, size=2, align="center", bold=True))
    if order.get("fulfillment") == "dine_in" and order.get("tableLabel"):
        lines.append(Line(f"DINE-IN - TABLE {order['tableLabel']}", align="center"))
    addr = order.get("deliveryAddress")
    if addr:
        text = addr if isinstance(addr, str) else " ".join(str(v) for v in addr.values() if v)
        lines.append(Line(f"地址 Address: {text}", bold=True))
    lines.append(Line(f"下单 Placed  {order.get('createdAtLocal') or str(order.get('createdAt', ''))[:16]}", align="center"))
    lines.append(Line(kind="rule"))

    for item in order.get("lines") or []:
        parts = name_parts(item.get("name"))
        qty = int(item.get("quantity") or 1)
        lines.append(Line(f"{qty} x {parts[0] if parts else ''}", size=2, bold=True))
        if len(parts) > 1:
            lines.append(Line(f"    {parts[1]}"))
        for opt in item.get("options") or []:
            group = (name_parts(opt.get("group")) or [""])[0]
            choice = " ".join(name_parts(opt.get("choice")))
            delta = int(opt.get("priceDeltaCents") or 0)
            extra = f" ({'+' if delta > 0 else '-'}{money(abs(delta), cur)})" if delta else ""
            lines.append(Line(f"  - {group}: {choice}{extra}", bold=True))
        if item.get("note"):
            lines.append(Line(f"  * 备注 Note: {item['note']}", bold=True))
        lines.append(Line("", right=money(item.get("lineTotalCents"), cur)))
    lines.append(Line(kind="rule"))

    lines.append(Line("小计 Subtotal", right=money(order.get("subtotalCents"), cur)))
    if order.get("taxCents"):
        lines.append(Line("税 Tax", right=money(order.get("taxCents"), cur)))
    if order.get("tipCents"):
        lines.append(Line("小费 Tip", right=money(order.get("tipCents"), cur)))
    if order.get("deliveryFeeCents"):
        lines.append(Line("配送费 Delivery", right=money(order.get("deliveryFeeCents"), cur)))
    lines.append(Line("合计 TOTAL", right=money(order.get("totalCents"), cur), size=2, bold=True))
    lines.append(Line("已付款 PAID" if order.get("paymentStatus") == "paid" else "到店付款 Pay at restaurant", align="center"))

    if order.get("notes"):
        lines.append(Line(kind="rule"))
        lines.append(Line("顾客备注 Customer note:", bold=True))
        lines.append(Line(str(order["notes"]), size=2, bold=True))
    lines.append(Line(kind="rule"))
    customer = order.get("customer") or {}
    if customer.get("name") or customer.get("phone"):
        lines.append(Line(f"顾客 {customer.get('name') or ''}  {customer.get('phone') or ''}".strip()))
    if printer_name:
        lines.append(Line(f"{printer_name}", align="center"))
    return lines


def test_lines(printer: Dict[str, Any], restaurant: Optional[Dict[str, Any]] = None) -> List[Line]:
    """The setup's test print: Chinese and English, the sizes the kitchen ticket uses, and the address."""
    names = name_parts((restaurant or {}).get("name")) if restaurant else []
    return [
        Line("测试打印 TEST PRINT", size=2, align="center", bold=True),
        Line(names[0] if names else "XOrdering", align="center"),
        Line(kind="rule"),
        Line(f"打印机 Printer: {printer.get('name', '')}"),
        Line(f"地址 Address: {printer.get('host', '')}:{printer.get('port', 9100)}"),
        Line(f"纸宽 Paper: {printer.get('paperWidth', 80)} mm · {'图片 Picture' if printer.get('render') == 'image' else '文字 Text (GBK)'}"),
        Line(kind="rule"),
        Line("2 x 宫保鸡丁", size=2, bold=True),
        Line("    Kung Pao Chicken"),
        Line("  - 辣度: 特辣 (+$1.00)", bold=True),
        Line("", right="$27.76"),
        Line("中文测试：麻婆豆腐 干炒牛河 小笼包", bold=True),
        Line(kind="rule"),
        Line("如果上面的中文是乱码，请在设置里把", align="center"),
        Line("“中文打印方式”改成“图片”。", align="center"),
        Line("Garbled Chinese above? Set Chinese", align="center"),
        Line("text to Picture for this printer.", align="center"),
    ]
