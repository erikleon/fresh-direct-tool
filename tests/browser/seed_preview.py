"""Seed a throwaway DB with a realistic draft plan for the browser checks.

See tests/browser/README.md — the dashboard must be started AFTER this runs, or
it holds an engine pointing at the sqlite file this script replaces.
"""
import os, sys, tempfile
from datetime import date, datetime
from pathlib import Path

DATA = Path(tempfile.gettempdir()) / "fdp-preview-data"
DATA.mkdir(exist_ok=True)
os.environ["FDPLANNER_DATA_DIR"] = str(DATA)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import PlanRow, PlanLineRow, RequestRow

get_settings.cache_clear()
s = get_settings()
print("data dir:", s.data_dir, "db:", s.db_path)
if Path(s.db_path).exists():
    Path(s.db_path).unlink()
init_db(s)

def alts(*items):
    return [
        {"sku": f"sku{i}", "name": n, "brand": b, "size": z, "price_cents": p,
         "url": "", "sold_out": False}
        for i, (n, b, z, p) in enumerate(items)
    ]

LINES = [
    ("Organic Artisan Calamari Rigati", "Just FreshDirect", "Organic Artisan Calamari Rigati", "16oz", 399,
     "due for restock · bought 5x, last on Aug 12", 0.94, True, 1),
    ("TOTAL 2% Greek Yogurt, Plain", "Fage", "TOTAL 2% Greek Yogurt, Plain", "35.3oz", 749,
     "due for restock · every 9 days on average", 0.88, False, 1),
    ("Albacore White Tuna In Water, Pouch", "Starkist", "Albacore White Tuna In Water, Pouch", "2.6oz", 249,
     "due for restock · bought 11x", 0.91, True, 4),
    ("YoBaby Whole Milk Yogurt Cups, Peach and Pear", "Stonyfield Organic",
     "YoBaby Whole Milk Yogurt Cups, Peach and Pear", "6ct, 4oz", 599,
     "due for restock · every 7 days on average", 0.83, True, 2),
    ("Hot Sauce, Original", "Cholula", "Hot Sauce, Original", "5oz", 449,
     "asked for on the shared list", 0.41, False, 1),
    ("Organic Baby Spinach", "Earthbound Farm", "Organic Baby Spinach", "5oz clamshell", 499,
     "due for restock · every 6 days on average", 0.96, True, 2),
    ("Whole Milk", "Ronnybrook Farm Dairy", "Creamline Whole Milk", "1qt glass", 429,
     "due for restock · bought 22x, last on Sep 01", 0.97, True, 2),
    ("Extra Virgin Olive Oil, First Cold Pressed", "Colavita",
     "Extra Virgin Olive Oil, First Cold Pressed", "34oz", 1899,
     "running low · every 45 days on average", 0.72, True, 1),
    ("Pasture Raised Large Brown Eggs", "Vital Farms", "Pasture Raised Large Brown Eggs", "12ct", 899,
     "due for restock · every 8 days on average", 0.95, True, 2),
    ("Sourdough Boule", "Balthazar Bakery", "Sourdough Boule", "1 loaf, 22oz", 699,
     "asked for on the shared list", 0.38, True, 1),
    ("Unsalted Butter, Sweet Cream", "Kerrygold", "Pure Irish Butter, Unsalted", "8oz", 549,
     "due for restock · every 14 days on average", 0.89, True, 2),
    ("Cold Brew Coffee Concentrate", "Grady's", "New Orleans Style Cold Brew Concentrate", "32oz", 1199,
     "running low · every 12 days on average", 0.81, True, 1),
]

with session_scope(s) as db:
    plan = PlanRow(
        week_of=date(2026, 9, 8), status="draft", budget_cap_cents=20000,
        address1="1240 Ocean Parkway", apartment="Apt 5C", city="Brooklyn",
        state="NY", zip_code="11230",
        delivery_start=datetime(2026, 9, 13, 19, 0), tip_cents=800,
    )
    db.add(plan); db.commit(); db.refresh(plan)
    for need, brand, name, size, price, reason, conf, incl, qty in LINES:
        db.add(PlanLineRow(
            plan_id=plan.id, need=need, reason=reason, confidence=conf,
            included=incl, quantity=qty, original_sku="sku0", selected_sku="sku0",
            selected_name=name, selected_brand=brand, selected_size=size,
            selected_price_cents=price,
            alternatives=alts(
                (name, brand, size, price),
                (f"{name} (Store Brand)", "Just FreshDirect", size, int(price * 0.78)),
                (f"{name} Family Size", brand, "family size", int(price * 1.6)),
            ),
        ))
    for t, src in [("paper towels", "reminders"), ("birthday candles", "reminders"),
                   ("something for taco night", "manual")]:
        db.add(RequestRow(text=t, source=src, status="open"))
    db.commit()
    print("seeded plan", plan.id)
