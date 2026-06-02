"""Thin Claude wrapper: structured output via tool use, with prompt caching.

Everything here degrades gracefully when no API key is configured — callers check
:func:`is_configured` first, and the heuristic paths elsewhere stand on their own.
The anthropic SDK is imported lazily so the rest of the app runs without a key.
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.config import Settings, get_settings

# Reused across calls; marked cacheable so repeated requests hit the prompt cache.
_PROFILE_SYSTEM = (
    "You are a grocery analyst. Given structured signals derived from a "
    "household's real purchase history and a heuristic first-draft profile, "
    "produce a refined dietary profile. Stay strictly grounded in the data — do "
    "not invent restrictions the purchases don't support. Prefer concise, "
    "concrete phrasing a meal planner can act on."
)

# Mirrors app.profile.DietaryProfile so tool output maps straight onto it.
_PROFILE_TOOL = {
    "name": "record_dietary_profile",
    "description": "Record the refined household dietary profile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "diet_style": {"type": "string", "description": "One concise phrase."},
            "organic_preference": {"type": "string", "enum": ["high", "medium", "low"]},
            "plant_forward": {"type": "boolean"},
            "favored_proteins": {"type": "array", "items": {"type": "string"}},
            "likely_avoids": {"type": "array", "items": {"type": "string"}},
            "household_notes": {"type": "array", "items": {"type": "string"}},
            "staple_brands": {"type": "array", "items": {"type": "string"}},
            "top_products": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["diet_style", "organic_preference", "plant_forward"],
    },
}


def is_configured(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"))


def _client(settings: Settings):
    import anthropic

    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    )


def synthesize_profile(signals, draft, settings: Settings | None = None):
    """Refine a heuristic DietaryProfile with Claude; fall back to the draft on error."""
    from app.profile import DietaryProfile  # local import avoids a cycle

    settings = settings or get_settings()
    if not is_configured(settings):
        return draft

    payload = {
        "signals": {
            "total_items": signals.total_items,
            "distinct_products": signals.distinct_products,
            "category_counts": signals.category_counts,
            "top_brands": signals.top_brands,
            "top_products": signals.top_products[:20],
            "organic_ratio": round(signals.organic_ratio, 3),
            "protein_hits": signals.protein_hits,
            "plant_dairy_hits": signals.plant_dairy_hits,
            "diet_flag_hits": signals.diet_flag_hits,
            "has_baby": signals.has_baby,
        },
        "heuristic_draft": draft.model_dump(),
    }

    try:
        resp = _client(settings).messages.create(
            model=settings.planner_model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _PROFILE_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_PROFILE_TOOL],
            tool_choice={"type": "tool", "name": "record_dietary_profile"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Refine this household's dietary profile from the data "
                        "below.\n\n" + json.dumps(payload, indent=2)
                    ),
                }
            ],
        )
    except Exception as exc:  # network/auth/etc. — never block on the AI path
        draft.notes.append(f"(AI refinement skipped: {type(exc).__name__})")
        return draft

    data = _first_tool_input(resp)
    if not data:
        return draft
    merged = {**draft.model_dump(), **data, "source": "heuristic+ai"}
    return DietaryProfile.model_validate(merged)


def _first_tool_input(resp) -> dict[str, Any] | None:
    for block in getattr(resp, "content", []):
        if getattr(block, "type", None) == "tool_use":
            return dict(block.input)
    return None
