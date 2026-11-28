"""The ticket: an order laid out as lines, independent of how it is printed.

One ticket per online order, in English (dish names keep their Chinese name too when the menu has
one). From the top:

    PICKUP: ASAP (~4:10 PM)            biggest text: when the customer comes
    or, inverted, ★ SCHEDULED 6:30 PM ★
    TSUNAMI SUSHI / ONLINE ORDER · PICKUP
    Order #1042            Placed 3:55 PM
    Alan · (509) 555-0134
    qty  dish (wrapped)            amount
         @unit price, options, Note: …
    Order note
    Subtotal / Tax (8.5%) / TOTAL
    NOT PAID · PAY AT PICKUP

A scheduled order's second ticket (20 minutes before pickup) starts with READY BY 6:30 PM · REPRINT.

render.py draws these lines as a picture (Star raster and ESC/POS picture modes); escpos.py can
also print them as ESC/POS text (Chinese in GBK). Text mode counts widths the way receipt printers
do: a Chinese character takes two columns; 80 mm paper has 48 columns (Font A), 58 mm 32.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Line:
    text: str = ""
    right: str = ""
    # 1: normal; 2: large (store name, TOTAL); 3: largest (the pickup time)
    size: int = 1
    align: str = "left"  # left | center
    bold: bool = False
    invert: bool = False  # white on black
    kind: str = "text"  # text | rule | double | feed
    # A left column (the quantity): wrapped rows of `text` keep to the right of it.
    lead: str = ""
    # Rows start where the text after a `lead` starts (lines under a dish).
    indent: int = 0


CURRENCY = {"CAD": "$", "USD": "$", "AUD": "$", "HKD": "HK$", "CNY": "¥", "GBP": "£", "EUR": "€", "TWD": "NT$"}
LEAD_COLS = 4  # "12  " — the quantity column


def money(cents: Optional[int], currency: str = "CAD") -> str:
    cents = int(cents or 0)
    sign = "-" if cents < 0 else ""
    a = abs(cents)
    return f"{sign}{CURRENCY.get(currency, currency + ' ')}{a // 100}.{a % 100:02d}"


def tax_of(subtotal_cents: int, rate_bps: int) -> int:
    """Tax on a subtotal, rounded to the cent half up, as the server's place_order rounds it."""
    return (int(subtotal_cents) * int(rate_bps) + 5000) // 10000


def rate_text(bps: int) -> str:
    """A tax rate without extra zeros: 850 -> 8.5%, 1000 -> 10%, 725 -> 7.25%."""
    bps = int(bps)
    whole, frac = divmod(bps, 100)
    return f"{whole}{('.' + f'{frac:02d}'.rstrip('0')) if frac else ''}%"


def tax_label(order: Dict[str, Any], restaurant: Dict[str, Any]) -> str:
    """'Tax (8.5%)': the rate the order was taxed at (the server sends it with the order). Older
    servers don't: then the restaurant's rate when it gives exactly the order's tax, else just 'Tax'."""
    bps = order.get("taxRateBps")
    if bps is None:
        current = restaurant.get("taxRateBps")
        if current is not None and tax_of(order.get("subtotalCents") or 0, current) == int(order.get("taxCents") or 0):
            bps = current
    try:
        return f"Tax ({rate_text(bps)})" if bps is not None else "Tax"
    except (TypeError, ValueError):
        return "Tax"


def amount(cents: Optional[int]) -> str:
    """A price without the currency sign (dish lines): 14.95."""
    cents = int(cents or 0)
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 0 if unicodedata.combining(c) else 1 for c in s)


def columns(paper_width: int, size: int = 1) -> int:
    base = 32 if int(paper_width) == 58 else 48
    return base // min(2, max(1, size))


def wrap(text: str, cols: int) -> List[str]:
    """Greedy wrap: Latin words stay whole when they fit; CJK breaks between any two characters."""
    out: List[str] = []
    cols = max(1, cols)
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
    """One Line as the text rows it prints (text mode), padded so right text ends at the edge."""
    cols = columns(paper_width, line.size)
    if line.kind in ("rule", "double"):
        return [("=" if line.kind == "double" else "-") * columns(paper_width)]
    if line.kind == "feed":
        return [""]
    pad = LEAD_COLS if (line.lead or line.indent) else 0
    prefix = (line.lead or "").ljust(pad) if pad else ""
    rows = wrap(line.text, cols - pad)
    rows = [(prefix if i == 0 else " " * pad) + r for i, r in enumerate(rows)]
    if line.right:
        last = rows[-1]
        if width(last) + 1 + width(line.right) <= cols:
            rows[-1] = last + " " * (cols - width(last) - width(line.right)) + line.right
        else:
            rows.append(" " * max(0, cols - width(line.right)) + line.right)
    elif line.align == "center":
        rows = [" " * max(0, (cols - width(r)) // 2) + r for r in rows]
    return rows


def name_parts(value: Any) -> List[str]:
    """A localized name as [English, Chinese] (each only when present and different)."""
    if isinstance(value, dict):
        zh = str(value.get("zh") or "").strip()
        en = str(value.get("en") or "").strip()
        return [x for x in (en, zh if zh != en else "") if x]
    return [str(value or "").strip()]


def restaurant_name(restaurant: Dict[str, Any]) -> str:
    parts = name_parts((restaurant or {}).get("name"))
    return parts[0] if parts else ""


def phone(value: str) -> str:
    """(509) 555-0134 for North American numbers, otherwise as given."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return (value or "").strip()


def clock(value: Optional[str]) -> str:
    """'2026-11-25 18:32' (or '18:32') -> '6:32 PM'; anything else unchanged."""
    m = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?$", (value or "").strip())
    if not m:
        return (value or "").strip()
    h, mi = int(m.group(1)), m.group(2)
    return f"{(h % 12) or 12}:{mi} {'AM' if h < 12 else 'PM'}"


HOW = {"pickup": "PICKUP", "dine_in": "DINE-IN", "delivery": "DELIVERY"}


def headline(order: Dict[str, Any], reason: str = "new") -> List[Line]:
    """The top of the ticket: when (pickup time) or where (table), in the biggest type."""
    fulfillment = order.get("fulfillment") or "pickup"
    pickup = clock(order.get("pickupAtLocal")) if order.get("pickupAt") else ""
    out: List[Line] = []
    if reason == "scheduled" and pickup:
        out.append(Line(f"READY BY {pickup} · REPRINT", size=2, align="center", bold=True, invert=True))
    elif reason == "reprint":
        out.append(Line("REPRINT", size=2, align="center", bold=True, invert=True))
    elif reason == "test":
        out.append(Line("TEST PRINT · 测试打印", size=2, align="center", bold=True, invert=True))
    if fulfillment == "dine_in":
        table = order.get("tableLabel")
        out.append(Line(f"DINE-IN · TABLE {table}" if table else "DINE-IN", size=3, align="center", bold=True))
    elif pickup:
        out.append(Line(f"★ SCHEDULED {pickup} ★", size=3, align="center", bold=True, invert=True))
    else:
        ready = clock(order.get("readyAtLocal"))
        word = HOW.get(fulfillment, fulfillment.upper())
        out.append(Line(f"{word}: ASAP (~{ready})" if ready else f"{word}: ASAP", size=3, align="center", bold=True))
    return out


def build(order: Dict[str, Any], restaurant: Dict[str, Any], *, reason: str = "new", printer_name: str = "", reprint: Optional[bool] = None) -> List[Line]:
    """The ticket for one order. reason: new | reprint (dashboard) | scheduled (before pickup) | test."""
    if reprint:  # older callers
        reason = "reprint"
    cur = order.get("currency") or restaurant.get("currency") or "CAD"
    fulfillment = order.get("fulfillment") or "pickup"
    lines = headline(order, reason)
    lines.append(Line(kind="double"))
    name = restaurant_name(restaurant)
    if name:
        lines.append(Line(name.upper(), size=2, align="center", bold=True))
    lines.append(Line(f"ONLINE ORDER · {HOW.get(fulfillment, fulfillment.upper())}", align="center"))
    lines.append(Line(kind="double"))

    placed = clock(order.get("placedAtLocal") or order.get("createdAtLocal") or "")
    lines.append(Line(f"Order #{int(order.get('number') or 0)}", right=f"Placed {placed}" if placed else "", bold=True))
    customer = order.get("customer") or {}
    who = " · ".join(x for x in (str(customer.get("name") or "").strip(), phone(str(customer.get("phone") or ""))) if x)
    if who:
        lines.append(Line(who))
    addr = order.get("deliveryAddress")
    if addr:
        text = addr if isinstance(addr, str) else " ".join(str(v) for v in addr.values() if v)
        lines.append(Line(f"Address: {text}", bold=True))
    lines.append(Line(kind="rule"))

    for item in order.get("lines") or []:
        parts = name_parts(item.get("name"))
        qty = int(item.get("quantity") or 1)
        total = amount(item.get("lineTotalCents"))
        unit = item.get("unitPriceCents")
        lines.append(Line(parts[0] if parts else "", lead=str(qty), right="" if qty > 1 else total, bold=True))
        for extra in parts[1:]:
            lines.append(Line(extra, indent=1))
        for opt in item.get("options") or []:
            group = (name_parts(opt.get("group")) or [""])[0]
            choice = " / ".join(name_parts(opt.get("choice")))
            delta = int(opt.get("priceDeltaCents") or 0)
            plus = f" (+{amount(delta)})" if delta > 0 else f" ({amount(delta)})" if delta < 0 else ""
            lines.append(Line(f"{group}: {choice}{plus}" if group else f"{choice}{plus}", indent=1))
        if qty > 1:
            each = unit if unit is not None else (int(item.get("lineTotalCents") or 0) // qty)
            lines.append(Line(f"@{amount(each)}", right=total, indent=1))
        if item.get("note"):
            lines.append(Line(f"Note: {item['note']}", indent=1, bold=True))
    lines.append(Line(kind="rule"))

    if order.get("notes"):
        lines.append(Line(f"Note: {order['notes']}", bold=True))
        lines.append(Line(kind="rule"))

    lines.append(Line("Subtotal", right=amount(order.get("subtotalCents"))))
    lines.append(Line(tax_label(order, restaurant), right=amount(order.get("taxCents"))))
    if order.get("tipCents"):
        lines.append(Line("Tip", right=amount(order.get("tipCents"))))
    if order.get("deliveryFeeCents"):
        lines.append(Line("Delivery", right=amount(order.get("deliveryFeeCents"))))
    lines.append(Line("TOTAL", right=money(order.get("totalCents"), cur), size=2, bold=True))
    if order.get("paymentStatus") == "paid":
        lines.append(Line("PAID", align="center", bold=True))
    else:
        pay = "PAY AT PICKUP" if fulfillment == "pickup" else "PAY AT THE RESTAURANT"
        lines.append(Line(f"NOT PAID · {pay}", align="center", bold=True))
    lines.append(Line(kind="double"))
    if printer_name:
        lines.append(Line(printer_name, align="center"))
    return lines


# --- The test page and the preview: one sample order, both pickup styles ----------------------------

def sample_orders(restaurant: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Two sample orders (ASAP and scheduled) with a long dish name and a Chinese note."""
    r = restaurant or {}
    tax_bps = int(r.get("taxRateBps") if r.get("taxRateBps") is not None else 500)
    prep = int(r.get("prepMinutes") or 15)
    lines = [
        {"name": {"en": "Tuna Pepper"}, "quantity": 1, "unitPriceCents": 1495, "lineTotalCents": 1495, "options": [], "note": ""},
        {"name": {"en": "Shrimp Tempura Appetizer with Spicy Mayo and Extra Crispy Flakes", "zh": "天妇罗虾"}, "quantity": 2,
         "unitPriceCents": 995, "lineTotalCents": 1990, "options": [], "note": "no wasabi"},
        {"name": {"en": "Dragon Roll"}, "quantity": 1, "unitPriceCents": 1750, "lineTotalCents": 1750,
         "options": [{"group": {"en": "Rice"}, "choice": {"en": "Brown rice"}, "priceDeltaCents": 100}], "note": ""},
    ]
    subtotal = sum(x["lineTotalCents"] for x in lines)
    tax = tax_of(subtotal, tax_bps)
    placed = 15 * 60 + 55
    ready = placed + prep
    base = {
        "number": 1042, "fulfillment": "pickup", "currency": r.get("currency") or "CAD",
        "placedAtLocal": f"{placed // 60:02d}:{placed % 60:02d}", "readyAtLocal": f"{ready // 60 % 24:02d}:{ready % 60:02d}",
        "customer": {"name": "Alan", "phone": "+15095550134"},
        "notes": "少放酱油，谢谢！Please call when ready.", "lines": lines,
        "subtotalCents": subtotal, "taxCents": tax, "taxRateBps": tax_bps, "totalCents": subtotal + tax, "paymentStatus": "unpaid",
    }
    scheduled = dict(base, number=1043, pickupAt="2026-01-01T18:30:00", pickupAtLocal="18:30", customer={"name": "陈小姐", "phone": "+16045550187"})
    return [base, scheduled]


def test_tickets(restaurant: Optional[Dict[str, Any]] = None) -> List[List[Line]]:
    """The test page: the sample order as an ASAP ticket and as a scheduled one."""
    return [build(o, restaurant or {}, reason="test") for o in sample_orders(restaurant)]


def test_lines(printer: Dict[str, Any], restaurant: Optional[Dict[str, Any]] = None, when: Optional[str] = None) -> List[Line]:
    """The connection test: one short ticket with the store, the time, how this computer reaches the
    printer (IP or Windows printer) and its type, and some Chinese (to check GBK text mode)."""
    import time as _time

    mode = {"star": "Star raster (TSP100)", "image": "ESC/POS picture", "text": "ESC/POS text (GBK)"}.get(printer.get("render") or "text", "")
    if printer.get("connection") == "windows":
        how = ["Connection: Windows printer", f"  {printer.get('windowsPrinter', '')}"]
        if printer.get("windowsMethod") == "driver":
            how.append("  (picture through the driver)")
    else:
        how = ["Connection: network IP (direct)", f"  {printer.get('host', '')}:{printer.get('port') or 9100}"]
    paper_w = 80 if printer.get("render") == "star" else printer.get("paperWidth", 80)
    return [
        Line("TEST PRINT · 测试打印", size=2, align="center", bold=True, invert=True),
        Line(restaurant_name(restaurant or {}) or "XOrdering", align="center", bold=True),
        Line(when or _time.strftime("%Y-%m-%d %H:%M:%S"), align="center"),
        Line(kind="rule"),
        Line(f"Printer: {printer.get('name', '')}"),
        *[Line(h) for h in how],
        Line(f"Paper: {paper_w} mm · {mode}"),
        Line(kind="rule"),
        Line("中文测试：麻婆豆腐 干炒牛河 小笼包", bold=True),
        Line(kind="rule"),
        Line("If this prints, the printer is set up.", align="center"),
        Line("Garbled Chinese in text mode? Use Picture.", align="center"),
    ]
