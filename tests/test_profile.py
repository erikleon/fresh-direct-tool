"""Tests for dietary-profile inference.

The pure signal/draft tests need no DB. The `infer_profile` gating tests use an
isolated temp DB and mock `app.ai` — no real client is built.
"""

from dataclasses import dataclass

from app.profile import compute_signals, draft_profile


@dataclass
class FakeItem:
    name: str
    brand: str | None = None
    category: str | None = None


def _plant_forward_basket() -> list[FakeItem]:
    items: list[FakeItem] = []
    # Organic-heavy produce.
    for _ in range(20):
        items.append(FakeItem("Organic Baby Spinach", "Earthbound", "Vegetables"))
    for _ in range(15):
        items.append(FakeItem("Organic Banana", None, "Fruit"))
    # Plant-based dairy + real dairy.
    for _ in range(12):
        items.append(FakeItem("Organic Soy Milk", "Silk", "Dairy"))
    for _ in range(8):
        items.append(FakeItem("Greek Yogurt", "Fage", "Dairy"))
    # A little tofu/chicken, no pork at all.
    for _ in range(6):
        items.append(FakeItem("Organic Tofu, Firm", "Nasoya", "Pantry"))
    for _ in range(3):
        items.append(FakeItem("Chicken Thighs", None, "Poultry"))
    # Baby food.
    for _ in range(5):
        items.append(FakeItem("Stage 2 Apple Puree", "Once Upon a Farm", "Baby"))
    return items


def test_signals_counts_and_ratios():
    s = compute_signals(_plant_forward_basket())
    assert s.total_items == 69
    assert s.category_counts["Vegetables"] == 20
    assert s.organic_ratio > 0.6  # most lines say "organic"
    assert s.has_baby
    assert s.plant_dairy_hits >= 12  # soy milk lines
    assert "pork" not in s.protein_hits


def test_draft_profile_is_plant_forward_and_organic():
    p = draft_profile(compute_signals(_plant_forward_basket()))
    assert p.organic_preference == "high"
    assert p.plant_forward
    assert "plant-forward" in p.diet_style
    assert "pork" in p.likely_avoids
    assert any("young child" in n for n in p.household_notes)
    assert p.source == "heuristic"


def test_meat_heavy_basket_is_not_plant_forward():
    items = [FakeItem("Ribeye Steak", None, "Meat") for _ in range(20)]
    items += [FakeItem("Pork Sausage", None, "Meat") for _ in range(10)]
    items += [FakeItem("White Bread", "Wonder", "Bakery") for _ in range(5)]
    p = draft_profile(compute_signals(items))
    assert not p.plant_forward
    assert p.organic_preference == "low"
    assert "pork" not in p.likely_avoids  # pork is present here
    assert not p.household_notes


def test_word_boundary_avoids_false_protein_matches():
    # 'graham' must not count as ham/pork; 'eggplant'/'Eggo' must not count as eggs.
    items = [
        FakeItem("Graham Crackers", "Honey Maid", "Snacks"),
        FakeItem("Organic Eggplant", None, "Vegetables"),
        FakeItem("Eggo Waffles, Buttermilk", "Eggo", "Frozen"),
    ]
    s = compute_signals(items)
    assert "pork" not in s.protein_hits  # graham != ham
    assert "eggs" not in s.protein_hits  # eggplant/Eggo != eggs


def test_empty_basket():
    s = compute_signals([])
    assert s.total_items == 0
    assert s.organic_ratio == 0.0
    # draft_profile must not divide-by-zero on an empty basket.
    p = draft_profile(s)
    assert p.organic_preference == "low"


# --- infer_profile AI gating (isolated DB, mocked AI) ----------------------


def test_infer_profile_skips_ai_without_key(monkeypatch, tmp_path):
    from app import profile as profmod
    from app.config import Settings
    from app.db import init_db

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FDPLANNER_ANTHROPIC_API_KEY", raising=False)
    s = Settings(data_dir=tmp_path, anthropic_api_key=None)
    init_db(s)  # empty DB is fine — we're testing the gate, not the signals
    prof, _ = profmod.infer_profile(s, use_ai=True)
    assert prof.source == "heuristic"  # AI path never ran


def test_infer_profile_uses_ai_when_configured(monkeypatch, tmp_path):
    from app import ai
    from app import profile as profmod
    from app.config import Settings
    from app.db import init_db

    s = Settings(data_dir=tmp_path, anthropic_api_key="test-key")
    init_db(s)
    sentinel = profmod.DietaryProfile(diet_style="ai-refined", source="heuristic+ai")
    monkeypatch.setattr(ai, "synthesize_profile", lambda sig, draft, settings=None: sentinel)
    prof, _ = profmod.infer_profile(s, use_ai=True)
    assert prof is sentinel
