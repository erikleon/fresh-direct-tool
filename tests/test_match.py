"""Tests for the heuristic SKU matcher (pure; no network/DB/LLM).

The `resolve()` AI-branch tests mock `app.ai` — no real client is built.
"""

from decimal import Decimal

import pytest

from app import ai
from app.freshdirect.base import Product
from app.match import (
    best_match,
    calibrated_confidence,
    normalize_key,
    resolve,
    score_candidates,
)


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


# --- calibrated_confidence -------------------------------------------------


def test_calibrated_confidence_rewards_separation():
    clear = calibrated_confidence(0.9, 0.1)   # runaway winner
    tie = calibrated_confidence(0.9, 0.85)    # near tie
    assert clear > tie
    assert 0.0 <= tie <= 1.0


def test_calibrated_confidence_clamps_out_of_range():
    assert calibrated_confidence(2.0, 0.0) <= 1.0
    assert calibrated_confidence(-1.0, -2.0) >= 0.0


def test_sold_out_top_pick_forces_review():
    only = [_p("S", "Whole Milk, Carton", "Brand", organic=True, sold_out=True, size="1/2 gallon")]
    r = best_match("organic whole milk", only)
    assert r.pick.sku == "S"      # it's the only candidate
    assert r.needs_review         # but a sold-out pick is never proposed confidently


# --- resolve() AI branch (mocked client) -----------------------------------


@pytest.fixture
def no_alias(monkeypatch):
    """Skip the DB alias lookup so resolve() exercises heuristic + AI only."""
    monkeypatch.setattr("app.match.get_alias", lambda q, s=None: None)


@pytest.fixture
def ai_on(monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda s=None: True)


def test_resolve_ai_disabled_returns_heuristic(no_alias):
    r = resolve("organic whole milk", CANDIDATES, use_ai=False)
    assert r.method == "heuristic"
    assert r.pick.sku == "B"


def test_resolve_ai_pick_is_capped_at_heuristic_confidence(no_alias, ai_on, monkeypatch):
    # Claude confidently (0.9) picks A — a non-organic bottle — for an organic
    # need. The cap pulls confidence down to the heuristic's view of A and flags it.
    monkeypatch.setattr(ai, "rank_products", lambda q, c, **k: {"sku": "A", "confidence": 0.9})
    r = resolve("organic whole milk", CANDIDATES, use_ai=True)
    assert r.method == "ai"
    assert r.pick.sku == "A"
    assert r.confidence < 0.9    # not the inflated self-report
    assert r.needs_review        # poor heuristic fit → human look


def test_resolve_empty_sku_forces_review(no_alias, ai_on, monkeypatch):
    # "nothing fits" keeps the heuristic pick but flags it.
    monkeypatch.setattr(ai, "rank_products", lambda q, c, **k: {"sku": "", "confidence": 0.0})
    r = resolve("organic whole milk", CANDIDATES, use_ai=True)
    assert r.method == "heuristic"
    assert r.pick.sku == "B"
    assert r.needs_review


def test_resolve_hallucinated_sku_falls_back(no_alias, ai_on, monkeypatch):
    monkeypatch.setattr(ai, "rank_products", lambda q, c, **k: {"sku": "ZZZ", "confidence": 0.99})
    r = resolve("organic whole milk", CANDIDATES, use_ai=True)
    assert r.method == "heuristic"
    assert r.pick.sku == "B"


def test_resolve_sold_out_ai_pick_falls_back(no_alias, ai_on, monkeypatch):
    # D is in the shortlist but sold out; the AI pick is rejected.
    monkeypatch.setattr(ai, "rank_products", lambda q, c, **k: {"sku": "D", "confidence": 0.95})
    r = resolve("organic whole milk", CANDIDATES, use_ai=True)
    assert r.method == "heuristic"
    assert r.pick.sku != "D"


def test_resolve_no_key_returns_heuristic(no_alias, monkeypatch):
    monkeypatch.setattr(ai, "is_configured", lambda s=None: False)
    monkeypatch.setattr(ai, "rank_products", lambda *a, **k: pytest.fail("AI called without a key"))
    r = resolve("organic whole milk", CANDIDATES, use_ai=True)
    assert r.method == "heuristic"
