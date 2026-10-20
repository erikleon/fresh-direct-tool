"""What the household probably has: a confirmed file plus stock estimated from orders.

The confirmed file lists things a person says are on hand, one name per line.
It can hide a meal ingredient from the shopping list. The estimate comes from
buying frequency and can only annotate: history shows when something was bought,
not how much is left.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.analytics import Prediction
from app.match import normalize_key

STALE_DAYS = 90
# A bought item counts as likely on hand while this fraction of its usual
# buying interval has not yet passed.
# shortcut: first-guess cutoff, tune against real order history.
ON_HAND_RATIO = 0.7

# Words ignored when comparing an ingredient with a pantry name.
DESCRIPTORS = frozenset({
    "organic", "large", "small", "medium", "raw", "diced", "chopped",
    "sliced", "minced", "whole", "ground", "dried", "frozen", "cooked",
})


def terms(text: str) -> frozenset[str]:
    return frozenset(normalize_key(text).split()) - DESCRIPTORS


def load_confirmed(path: Path | None) -> tuple[list[frozenset[str]], str | None]:
    """Read the confirmed list. Returns the items and a plan note when it was not used."""
    if path is None:
        return [], None
    try:
        age_days = (time.time() - path.stat().st_mtime) / 86400
        text = path.read_text()
    except OSError as err:
        return [], f"Pantry list could not be read ({err.strerror}); it was not used."
    if age_days > STALE_DAYS:
        return [], f"Pantry list was last updated {int(age_days)} days ago and was not used."
    lines = (ln.strip() for ln in text.splitlines())
    items = [terms(ln) for ln in lines if ln and not ln.startswith("#")]
    return [item for item in items if item], None


def is_confirmed(query: str, confirmed: list[frozenset[str]]) -> bool:
    """Exact match only. A missed match costs one extra line; a false one hides a need."""
    wanted = terms(query)
    return bool(wanted) and wanted in confirmed


def likely_on_hand(pred: Prediction) -> bool:
    return pred.days_since_last / pred.mean_interval_days < ON_HAND_RATIO


def on_hand_note(pred: Prediction) -> str:
    return (f"Bought {pred.days_since_last} days ago, usually every "
            f"{round(pred.mean_interval_days)}. Likely on hand.")
