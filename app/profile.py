"""Infer a household dietary profile from what actually gets bought.

The signal extraction (:func:`compute_signals`) and the heuristic mapping
(:func:`draft_profile`) are pure functions over line items, so they're tested
without a DB and run fully offline. An optional Claude pass (:mod:`app.ai`) can
later turn the draft into richer prose, but the structured profile stands on its
own. The result is persisted to ``data/profile.json`` and is meant to be
human-editable — it's a starting point, not a verdict.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field

from app.config import Settings, get_settings

# Keyword groups used to read intent out of free-text product names.
_ORGANIC = ("organic",)
_PLANT_DAIRY = ("soy", "almond milk", "oat milk", "oatmilk", "soymilk", "plant-based", "plant based", "dairy-free", "dairy free")
_PROTEINS = {
    "chicken": ("chicken",),
    "beef": ("beef", "steak"),
    "turkey": ("turkey",),
    "pork": ("pork", "bacon", "sausage", "ham"),
    "salmon": ("salmon",),
    "fish": ("fish", "tuna", "cod", "tilapia"),
    "shrimp": ("shrimp", "prawn"),
    "tofu": ("tofu", "tempeh", "edamame"),
    "eggs": ("egg", "eggs"),
}
_BABY = ("baby", "toddler", "infant", "stage 1", "stage 2", "stage 3", "puree", "formula")
_DIET_FLAGS = ("gluten free", "gluten-free", "vegan", "grass-fed", "grassfed", "grass fed",
               "cage-free", "cage free", "kosher", "non-gmo", "keto", "no sugar")
_BABY_CATEGORIES = ("baby",)


def _wcount(text: str, term: str) -> int:
    """Count whole-word occurrences of ``term`` (so 'ham' ≠ 'graham', 'egg' ≠ 'Eggo')."""
    return len(re.findall(rf"\b{re.escape(term)}\b", text))


@dataclass
class ProfileSignals:
    """Raw, structured stats extracted from purchase history."""

    total_items: int
    distinct_products: int
    category_counts: dict[str, int] = field(default_factory=dict)
    top_brands: list[tuple[str, int]] = field(default_factory=list)
    top_products: list[tuple[str, int]] = field(default_factory=list)
    organic_ratio: float = 0.0
    protein_hits: dict[str, int] = field(default_factory=dict)
    plant_dairy_hits: int = 0
    diet_flag_hits: dict[str, int] = field(default_factory=dict)
    has_baby: bool = False

    def meat_share(self) -> float:
        meat = sum(self.category_counts.get(c, 0) for c in ("Meat", "Poultry", "Seafood"))
        return meat / self.total_items if self.total_items else 0.0


class DietaryProfile(BaseModel):
    """A household's inferred eating profile — editable, not authoritative."""

    diet_style: str = "omnivore"
    organic_preference: str = "medium"  # high | medium | low
    plant_forward: bool = False
    favored_proteins: list[str] = Field(default_factory=list)
    likely_avoids: list[str] = Field(default_factory=list)
    household_notes: list[str] = Field(default_factory=list)
    staple_brands: list[str] = Field(default_factory=list)
    top_products: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: str = "heuristic"

    def save(self, settings: Settings | None = None) -> Path:
        settings = settings or get_settings()
        settings.ensure_dirs()
        path = settings.data_dir / "profile.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, settings: Settings | None = None) -> "DietaryProfile | None":
        settings = settings or get_settings()
        path = settings.data_dir / "profile.json"
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text())


def compute_signals(items: Iterable) -> ProfileSignals:
    """Extract structured signals from line items (objects with name/brand/category)."""
    items = list(items)
    names = [(getattr(it, "name", "") or "") for it in items]
    text_blob = " ".join(n.lower() for n in names)

    categories = Counter((getattr(it, "category", None) or "Uncategorized") for it in items)
    brands = Counter(
        b for it in items if (b := (getattr(it, "brand", None) or "").strip())
    )
    products = Counter(n for n in names if n)

    organic_lines = sum(1 for n in names if "organic" in n.lower())
    protein_hits = {
        key: sum(_wcount(text_blob, kw) for kw in kws)
        for key, kws in _PROTEINS.items()
    }
    protein_hits = {k: v for k, v in protein_hits.items() if v}
    diet_flags = {f: _wcount(text_blob, f) for f in _DIET_FLAGS}
    diet_flags = {f: c for f, c in diet_flags.items() if c}

    has_baby = (
        any(categories.get(c.title(), 0) or categories.get(c.capitalize(), 0) for c in _BABY_CATEGORIES)
        or any(kw in text_blob for kw in _BABY)
    )

    return ProfileSignals(
        total_items=len(items),
        distinct_products=len(set(names)),
        category_counts=dict(categories),
        top_brands=brands.most_common(15),
        top_products=products.most_common(20),
        organic_ratio=organic_lines / len(items) if items else 0.0,
        protein_hits=dict(sorted(protein_hits.items(), key=lambda kv: kv[1], reverse=True)),
        plant_dairy_hits=sum(_wcount(text_blob, kw) for kw in _PLANT_DAIRY),
        diet_flag_hits=diet_flags,
        has_baby=has_baby,
    )


def draft_profile(signals: ProfileSignals) -> DietaryProfile:
    """Map raw signals to a first-draft :class:`DietaryProfile` (heuristics only)."""
    if signals.organic_ratio >= 0.15:
        organic = "high"
    elif signals.organic_ratio >= 0.05:
        organic = "medium"
    else:
        organic = "low"

    low_meat = signals.meat_share() < 0.05
    plant_forward = signals.plant_dairy_hits >= 10 and low_meat

    proteins = [k for k, _ in list(signals.protein_hits.items())[:5]]

    avoids: list[str] = []
    if signals.protein_hits.get("pork", 0) == 0:
        avoids.append("pork")
    if low_meat:
        avoids.append("heavy red meat")

    household: list[str] = []
    if signals.has_baby:
        household.append("young child in household (baby/toddler food bought regularly)")

    notes: list[str] = []
    if organic == "high":
        notes.append(f"strong organic preference (~{signals.organic_ratio:.0%} of lines)")
    if signals.plant_dairy_hits >= 10:
        notes.append("buys plant-based dairy alternatives alongside real dairy")
    if signals.diet_flag_hits.get("grass-fed") or signals.diet_flag_hits.get("grassfed"):
        notes.append("prefers grass-fed where applicable")

    if plant_forward:
        diet_style = "plant-forward flexitarian (low meat, produce-heavy)"
    elif low_meat:
        diet_style = "light-meat omnivore (produce-heavy)"
    else:
        diet_style = "omnivore"

    staple_brands = [b for b, _ in signals.top_brands[:10]]

    return DietaryProfile(
        diet_style=diet_style,
        organic_preference=organic,
        plant_forward=plant_forward,
        favored_proteins=proteins,
        likely_avoids=avoids,
        household_notes=household,
        staple_brands=staple_brands,
        top_products=[p for p, _ in signals.top_products[:12]],
        notes=notes,
        source="heuristic",
    )


def infer_profile(
    settings: Settings | None = None, use_ai: bool = True
) -> tuple[DietaryProfile, ProfileSignals]:
    """Compute signals from the DB, draft a profile, and optionally enrich via AI."""
    settings = settings or get_settings()
    from app.db import session_scope
    from app.models import OrderItemRow
    from sqlmodel import select

    with session_scope(settings) as db:
        items = db.exec(select(OrderItemRow)).all()

    signals = compute_signals(items)
    profile = draft_profile(signals)

    if use_ai:
        from app import ai

        if ai.is_configured(settings):
            profile = ai.synthesize_profile(signals, profile, settings)

    return profile, signals
