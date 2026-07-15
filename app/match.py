"""Resolve a free-text grocery need to a real FreshDirect product (SKU).

Three tiers, cheapest first:
1. **Alias** — a learned generic→SKU mapping from a past human correction.
2. **Heuristic** — pure token/brand/organic/size scoring over search candidates
   (this module's core; fully unit-tested, no network or LLM).
3. **Claude** — optional re-rank of the top heuristic candidates for hard cases.

Replenishment lines usually skip all this: history already gives the exact SKU.
Matching mainly serves *new* meal ingredients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.freshdirect.base import Product

_STOPWORDS = {
    "the", "a", "an", "of", "with", "and", "or", "for", "to", "in", "by",
    "fresh", "natural", "premium", "value", "pack", "size", "each", "ea",
}
_TOKEN = re.compile(r"[a-z0-9]+")
# Signals a bulk/multipack format (e.g. "12ct", "6-pack", "/cs", "boxes").
_MULTIPACK = re.compile(r"\b\d+\s*(ct|pk|pack|count)\b|/cs\b|\bcase\b|\b(boxes|cartons|bottles)\b")


def normalize_key(text: str) -> str:
    """Stable key for alias lookups: lowercased, meaningful tokens, sorted."""
    return " ".join(sorted(_meaningful(text)))


def _meaningful(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


@dataclass
class ScoredProduct:
    product: Product
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    query: str
    pick: Product | None
    confidence: float
    method: str  # alias | heuristic | ai | none
    alternatives: list[Product] = field(default_factory=list)
    force_review: bool = False  # set when the AI signals doubt (e.g. "nothing fits")

    @property
    def needs_review(self) -> bool:
        return self.force_review or self.pick is None or self.confidence < 0.5


def score_candidates(
    query: str,
    candidates: list[Product],
    want_organic: bool | None = None,
    preferred_brand: str | None = None,
) -> list[ScoredProduct]:
    """Score/rank candidates against a need (pure; highest score first)."""
    q_tokens = set(_meaningful(query))
    if want_organic is None:
        want_organic = "organic" in (query or "").lower()

    scored: list[ScoredProduct] = []
    for p in candidates:
        haystack = " ".join(filter(None, [p.name, p.brand, p.description, p.unit_size]))
        p_tokens = set(_meaningful(haystack))
        reasons: list[str] = []

        coverage = len(q_tokens & p_tokens) / len(q_tokens) if q_tokens else 0.0
        score = coverage
        reasons.append(f"{int(coverage * 100)}% term match")

        if preferred_brand and p.brand and preferred_brand.lower() in p.brand.lower():
            score += 0.25
            reasons.append("preferred brand")

        is_organic = "organic" in haystack.lower()
        if want_organic and is_organic:
            score += 0.15
            reasons.append("organic")
        elif want_organic and not is_organic:
            score -= 0.20
            reasons.append("not organic")

        # Most needs are for a single unit; demote cases/multipacks unless the
        # query asks for one, in which case prefer them.
        wants_multi = bool(_MULTIPACK.search((query or "").lower()))
        if _MULTIPACK.search(haystack.lower()):
            if wants_multi:
                score += 0.30
                reasons.append("matches multipack request")
            else:
                score -= 0.30
                reasons.append("multipack")

        if p.sold_out:
            score -= 1.0
            reasons.append("sold out")

        scored.append(ScoredProduct(product=p, score=round(score, 3), reasons=reasons))

    scored.sort(key=lambda s: (s.score, -len(s.product.name)), reverse=True)
    return scored


def calibrated_confidence(top_score: float, runner_up_score: float) -> float:
    """Blend absolute fit with separation from the runner-up into a 0-1 score.

    A high raw score alone isn't enough: when the runner-up is nearly as good
    (an ambiguous match), confidence should read lower. Shared by the matcher
    and the planner so both agree on what a given confidence means.
    """
    base = max(0.0, min(1.0, top_score))
    separation = min(1.0, max(0.0, top_score - runner_up_score) / 0.3)
    return round(base * (0.6 + 0.4 * separation), 3)


def best_match(
    query: str,
    candidates: list[Product],
    want_organic: bool | None = None,
    preferred_brand: str | None = None,
    alias_sku: str | None = None,
) -> MatchResult:
    """Pick the best product for a need, honoring a learned alias if present."""
    if not candidates:
        return MatchResult(query=query, pick=None, confidence=0.0, method="none")

    if alias_sku:
        for p in candidates:
            if p.sku == alias_sku and not p.sold_out:
                others = [c for c in candidates if c.sku != p.sku][:4]
                return MatchResult(query, p, confidence=0.95, method="alias", alternatives=others)

    ranked = score_candidates(query, candidates, want_organic, preferred_brand)
    top = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0

    confidence = calibrated_confidence(top.score, runner_up)
    # A sold-out top pick only wins when everything else is sold out too (the
    # -1.0 penalty sinks it otherwise); when it does, force a human look rather
    # than quietly proposing an unbuyable item.
    if top.product.sold_out:
        confidence = min(confidence, 0.4)

    return MatchResult(
        query=query,
        pick=top.product,
        confidence=confidence,
        method="heuristic",
        alternatives=[s.product for s in ranked[1:5]],
    )


# --------------------------------------------------------------------------- #
# Alias persistence (learned corrections)
# --------------------------------------------------------------------------- #


def get_alias(query: str, settings: Settings | None = None) -> str | None:
    from app.db import init_db, session_scope
    from app.models import ProductAlias

    settings = settings or get_settings()
    init_db(settings)  # idempotent; ensures product_aliases exists on older DBs
    with session_scope(settings) as db:
        row = db.get(ProductAlias, normalize_key(query))
        return row.sku if row else None


def set_alias(query: str, sku: str, name: str | None = None, settings: Settings | None = None) -> None:
    from datetime import datetime

    from app.db import init_db, session_scope
    from app.models import ProductAlias

    settings = settings or get_settings()
    init_db(settings)
    key = normalize_key(query)
    with session_scope(settings) as db:
        row = db.get(ProductAlias, key) or ProductAlias(generic_key=key, sku=sku)
        row.sku = sku
        row.product_name = name
        row.updated_at = datetime.utcnow()
        db.add(row)
        db.commit()


def resolve(
    query: str,
    candidates: list[Product],
    want_organic: bool | None = None,
    preferred_brand: str | None = None,
    use_ai: bool = True,
    profile_hint: str | None = None,
    settings: Settings | None = None,
) -> MatchResult:
    """Full resolve: alias → heuristic, then an optional Claude re-rank.

    A learned alias wins outright. Otherwise the heuristic produces a ranked
    list; if an Anthropic key is configured, Claude re-ranks the top candidates
    (cheap, bounded) and its pick is used when valid. Falls back silently to the
    heuristic when no key is set or anything goes wrong.
    """
    result = best_match(
        query,
        candidates,
        want_organic=want_organic,
        preferred_brand=preferred_brand,
        alias_sku=get_alias(query, settings),
    )
    if not use_ai or result.method == "alias":
        return result

    from app import ai

    if not ai.is_configured(settings):
        return result

    # Hand Claude the heuristic's shortlist (pick + alternatives), not all 30,
    # and only accept a pick from what it actually saw.
    shortlist = [result.pick, *result.alternatives] if result.pick else candidates[:8]
    by_sku = {p.sku: p for p in shortlist}
    choice = ai.rank_products(query, shortlist, profile_hint=profile_hint, settings=settings)
    if not choice:
        return result

    sku = choice.get("sku", "")
    if not sku:
        # Claude judged nothing in the shortlist fits — its most useful signal.
        # Keep the heuristic pick but force a human look.
        result.force_review = True
        return result

    pick = by_sku.get(sku)
    if pick is None or pick.sold_out:
        # Off-shortlist (hallucinated) or unbuyable sku — trust the heuristic.
        return result

    # Cap the AI's self-reported confidence at what the heuristic thinks of the
    # *same* product, so the model can flag doubt but never inflate its way past
    # the review gate.
    scores = {
        s.product.sku: s.score
        for s in score_candidates(query, candidates, want_organic, preferred_brand)
    }
    pick_score = scores.get(pick.sku, 0.0)
    runner = max((sc for s, sc in scores.items() if s != pick.sku), default=0.0)
    heuristic_conf = calibrated_confidence(pick_score, runner)
    ai_conf = max(0.0, min(1.0, float(choice.get("confidence", 0.0) or 0.0)))
    return MatchResult(
        query=query,
        pick=pick,
        confidence=min(ai_conf, heuristic_conf),
        method="ai",
        alternatives=[p for p in shortlist if p.sku != pick.sku][:4],
    )
