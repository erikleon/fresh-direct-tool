"""Tests for the pure replenishment-cadence calculator (no DB/network)."""

from datetime import date

from app.analytics import ItemPurchases, compute_cadence


def _weekly(start: date, n: int) -> list[date]:
    return [start.fromordinal(start.toordinal() + 7 * i) for i in range(n)]


def test_skips_items_below_min_purchases():
    items = [ItemPurchases("milk", "Milk", _weekly(date(2026, 1, 1), 2))]
    assert compute_cadence(items, today=date(2026, 3, 1), min_purchases=3) == []


def test_weekly_item_predicts_seven_day_interval():
    items = [ItemPurchases("milk", "Milk", _weekly(date(2026, 5, 1), 4))]
    # last purchase 2026-05-22; +7d → predicted 2026-05-29
    preds = compute_cadence(items, today=date(2026, 5, 29))
    assert len(preds) == 1
    p = preds[0]
    assert p.times_bought == 4
    assert p.mean_interval_days == 7.0
    assert p.last_purchased == date(2026, 5, 22)
    assert p.days_since_last == 7
    assert p.predicted_next == date(2026, 5, 29)
    assert p.days_overdue == 0
    assert p.is_due
    assert p.active


def test_overdue_is_positive_and_sorted_first():
    today = date(2026, 6, 1)
    items = [
        ItemPurchases("coffee", "Coffee", _weekly(date(2026, 5, 1), 4)),  # last 5/22, +7 -> 5/29 => +3 overdue
        ItemPurchases("rice", "Rice", _weekly(date(2026, 5, 25), 3)),     # last 6/8 in future-ish
    ]
    preds = compute_cadence(items, today=today)
    # Most overdue first.
    assert preds[0].name == "Coffee"
    assert preds[0].days_overdue == 3


def test_not_yet_due_has_negative_overdue():
    items = [ItemPurchases("oil", "Olive Oil", _weekly(date(2026, 5, 1), 4))]
    preds = compute_cadence(items, today=date(2026, 5, 25))  # predicted 5/29
    assert preds[0].days_overdue == -4
    assert not preds[0].is_due


def test_duplicate_dates_are_deduped():
    d = date(2026, 5, 1)
    dates = _weekly(d, 4) + [d]  # a duplicate of the first date
    preds = compute_cadence([ItemPurchases("eggs", "Eggs", dates)], today=date(2026, 6, 1))
    assert preds[0].times_bought == 4  # deduped, not 5


def test_item_dropped_from_rotation_is_inactive():
    # Weekly item, but last bought ~a year ago -> overdue but not active.
    items = [ItemPurchases("candy", "Soft Candy", _weekly(date(2025, 5, 1), 6))]
    preds = compute_cadence(items, today=date(2026, 6, 1), idle_multiple=3.0)
    p = preds[0]
    assert p.days_overdue > 300       # technically very overdue
    assert not p.active               # but clearly out of rotation


def test_recent_item_within_idle_window_is_active():
    items = [ItemPurchases("milk", "Milk", _weekly(date(2026, 5, 1), 4))]
    # last 2026-05-22; 14 days later is within 3× the 7d cadence (<=21d).
    preds = compute_cadence(items, today=date(2026, 6, 5), idle_multiple=3.0)
    assert preds[0].active


def test_irregular_intervals_average():
    # Purchases at day 0, 10, 30 -> intervals 10 and 20 -> mean 15
    base = date(2026, 1, 1)
    dates = [base, base.fromordinal(base.toordinal() + 10), base.fromordinal(base.toordinal() + 30)]
    preds = compute_cadence([ItemPurchases("tp", "Paper Towels", dates)], today=date(2026, 3, 1))
    assert preds[0].mean_interval_days == 15.0
