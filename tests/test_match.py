"""Tests for the heuristic SKU matcher (pure; no network/DB/LLM)."""

from decimal import Decimal

from app.freshdirect.base import Product
from app.match import best_match, normalize_key, score_candidates


def _p(sku, name, brand=None, organic=False, sold_out=False, size=None, price="1.00"):
    full = f"Organic {name}" if organic else name
    return Product(
        sku=sku, name=full, brand=brand, unit_size=size,
        price=Decimal(price), sold_out=sold_out,
    )


CANDIDATES = [
    _p("A", "Whole Milk, Plastic Bottle", "Just FreshDirect", size="1 gallon"),
    _p("B", "Whole Milk, Carton", "Organic Valley", organic=True, size="1/2 gallon"),
    _p("C", "Almond Milk, Unsweetened", "Silk", organic=True, size="64 fl oz"),
    _p("D", "Whole Milk, Carton", "Horizon", organic=True, sold_out=True, size="1/2 gallon"),
]


def test_organic_query_prefers_organic_product():
    r = best_match("organic whole milk", CANDIDATES)
    assert r.pick.sku == "B"  # organic + whole milk match, not the non-organic gallon
    assert r.method == "heuristic"
    assert r.confidence > 0.5


def test_non_organic_query_does_not_penalize_plain():
    r = best_match("whole milk", CANDIDATES)
    assert r.pick.sku in {"A", "B"}  # both match "whole milk"
    assert not r.needs_review


def test_sold_out_is_deprioritized():
    ranked = score_candidates("organic whole milk", CANDIDATES)
    assert ranked[-1].product.sku == "D"  # sold out sinks to the bottom


def test_preferred_brand_wins_ties():
    r = best_match("whole milk", CANDIDATES, preferred_brand="Organic Valley")
    assert r.pick.brand == "Organic Valley"


def test_alias_short_circuits_to_chosen_sku():
    r = best_match("organic whole milk", CANDIDATES, alias_sku="A")
    assert r.pick.sku == "A"
    assert r.method == "alias"
    assert r.confidence > 0.9


def test_alias_ignored_if_sold_out():
    r = best_match("organic whole milk", CANDIDATES, alias_sku="D")  # D is sold out
    assert r.method == "heuristic"  # falls back rather than picking a sold-out alias


def test_multipack_demoted_when_not_requested():
    cands = [
        _p("CASE", "Whole Milk, Cartons", "Horizon", organic=True, size="6ct, 1/2 gallon ea"),
        _p("ONE", "Whole Milk, Carton", "Organic Valley", organic=True, size="1/2 gallon"),
    ]
    r = best_match("organic whole milk", cands)
    assert r.pick.sku == "ONE"  # single carton beats the 6-count case


def test_multipack_kept_when_requested():
    cands = [
        _p("CASE", "Whole Milk, Cartons", "Horizon", organic=True, size="6ct, 1/2 gallon ea"),
        _p("ONE", "Whole Milk, Carton", "Organic Valley", organic=True, size="1/2 gallon"),
    ]
    r = best_match("organic whole milk 6 pack", cands)
    assert r.pick.sku == "CASE"


def test_ambiguous_tie_lowers_confidence():
    # Two effectively identical matches → confident pick should be tempered.
    cands = [
        _p("X", "Whole Milk, Carton", "Brand A", organic=True, size="1/2 gallon"),
        _p("Y", "Whole Milk, Carton", "Brand B", organic=True, size="1/2 gallon"),
    ]
    r = best_match("organic whole milk", cands)
    assert r.confidence < 0.8  # not falsely 100% when it's a toss-up


def test_no_candidates_needs_review():
    r = best_match("dragon fruit", [])
    assert r.pick is None
    assert r.needs_review
    assert r.method == "none"


def test_normalize_key_is_order_insensitive():
    assert normalize_key("Organic Whole Milk") == normalize_key("milk whole organic")
