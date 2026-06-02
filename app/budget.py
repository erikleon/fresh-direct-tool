"""Draft-plan types and pure budget reconciliation.

Kept free of any network/LLM/DB so the budget math is unit-tested directly.
:mod:`app.planner` builds the live plan and hands its lines here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.freshdirect.base import Product
from app.money import to_cents


@dataclass
class PlanLine:
    """One line of a draft plan: a chosen product for a need, plus fallbacks."""

    need: str
    product: Product
    quantity: float = 1.0
    source: str = "replenish"  # replenish | meal | manual
    confidence: float = 1.0
    reason: str = ""
    alternatives: list[Product] = field(default_factory=list)
    swap: Product | None = None  # a cheaper product suggested to hit budget

    @property
    def chosen(self) -> Product:
        return self.swap or self.product

    @property
    def line_cents(self) -> int:
        price = self.chosen.price or Decimal(0)
        return to_cents(price * Decimal(str(self.quantity))) or 0

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.5

    def cheapest_alternative(self) -> Product | None:
        """The cheapest in-stock alternative strictly cheaper than the pick."""
        base = self.product.price
        if base is None:
            return None
        cheaper = [
            a for a in self.alternatives
            if a.price is not None and a.price < base and not a.sold_out
        ]
        return min(cheaper, key=lambda a: a.price) if cheaper else None


@dataclass
class DraftPlan:
    week_of: date
    lines: list[PlanLine] = field(default_factory=list)
    budget_cap_cents: int | None = None

    @property
    def subtotal_cents(self) -> int:
        return sum(line.line_cents for line in self.lines)

    @property
    def over_by_cents(self) -> int:
        if self.budget_cap_cents is None:
            return 0
        return max(0, self.subtotal_cents - self.budget_cap_cents)

    @property
    def review_lines(self) -> list[PlanLine]:
        return [ln for ln in self.lines if ln.needs_review]


@dataclass
class SwapSuggestion:
    line: PlanLine
    to: Product
    saves_cents: int


def reconcile(plan: DraftPlan) -> list[SwapSuggestion]:
    """If over cap, greedily apply cheaper swaps until under it (or out of swaps).

    Mutates lines (sets ``line.swap``) and returns the swaps applied, biggest
    saver first. A no-op when there's no cap or we're already under it.
    """
    if plan.budget_cap_cents is None or plan.subtotal_cents <= plan.budget_cap_cents:
        return []

    # Rank lines by how much a swap would save, then apply until under budget.
    candidates: list[SwapSuggestion] = []
    for line in plan.lines:
        alt = line.cheapest_alternative()
        if alt is None:
            continue
        base = to_cents((line.product.price or Decimal(0)) * Decimal(str(line.quantity))) or 0
        new = to_cents((alt.price or Decimal(0)) * Decimal(str(line.quantity))) or 0
        saving = base - new
        if saving > 0:
            candidates.append(SwapSuggestion(line=line, to=alt, saves_cents=saving))

    candidates.sort(key=lambda s: s.saves_cents, reverse=True)

    applied: list[SwapSuggestion] = []
    for swap in candidates:
        if plan.subtotal_cents <= plan.budget_cap_cents:
            break
        swap.line.swap = swap.to
        applied.append(swap)
    return applied
