import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def temp_home() -> str:
    """A fresh data folder for this test (config, jobs.db, logs)."""
    d = tempfile.mkdtemp(prefix="xop-test-")
    os.environ["XORDERING_PRINTER_HOME"] = d
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ORDER = {
    "id": "o-1",
    "number": 1042,
    "fulfillment": "dine_in",
    "tableLabel": "12",
    "createdAtLocal": "2026-11-25 18:32",
    "customer": {"name": "陈先生", "phone": "604 555 0187"},
    "notes": "不要香菜，谢谢",
    "lines": [
        {"name": {"en": "Kung Pao Chicken", "zh": "宫保鸡丁"}, "quantity": 2, "unitPriceCents": 1388, "lineTotalCents": 2776,
         "options": [{"group": {"en": "Spice", "zh": "辣度"}, "choice": {"en": "Hot", "zh": "特辣"}, "priceDeltaCents": 100}], "note": "少放花生"},
        {"name": "Jasmine Tea", "quantity": 1, "unitPriceCents": 300, "lineTotalCents": 300, "options": [], "note": ""},
    ],
    "subtotalCents": 3076, "taxCents": 154, "tipCents": 300, "totalCents": 3530, "currency": "CAD", "paymentStatus": "unpaid",
}
RESTAURANT = {"id": "r-1", "name": {"en": "Golden Lotus", "zh": "金莲小馆"}, "currency": "CAD"}
