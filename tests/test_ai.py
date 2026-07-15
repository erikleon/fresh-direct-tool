"""Tests for the optional Claude layer (app.ai) with a fully mocked client.

These cover plumbing — parsing tool output, graceful fallback, the additive
profile merge — not the quality of Claude's actual picks (that stays gated by the
human-review surface; a real-API eval is deferred, see TODOS.md).
"""

import types
from decimal import Decimal

import pytest

from app import ai
from app.config import Settings
from app.freshdirect.base import Product
from app.profile import DietaryProfile, ProfileSignals


def _prod(sku, name="Whole Milk", brand=None):
    return Product(sku=sku, name=name, brand=brand, price=Decimal("1.00"))


def _settings():
    return Settings(anthropic_api_key="test-key")


def _nokey(monkeypatch):
    """A Settings with no key configured, immune to any ambient env key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FDPLANNER_ANTHROPIC_API_KEY", raising=False)
    return Settings(anthropic_api_key=None)


class _ToolBlock:
    def __init__(self, data):
        self.type = "tool_use"
        self.input = data


class _TextBlock:
    type = "text"


class _Resp:
    def __init__(self, content):
        self.content = content


def _fake_client(response=None, raise_exc=None):
    class _Messages:
        def create(self, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            return response

    return types.SimpleNamespace(messages=_Messages())


# --- is_configured ---------------------------------------------------------


def test_is_configured_from_settings():
    assert ai.is_configured(_settings())


def test_is_configured_from_env(monkeypatch):
    monkeypatch.delenv("FDPLANNER_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert ai.is_configured(Settings(anthropic_api_key=None))


def test_not_configured(monkeypatch):
    assert not ai.is_configured(_nokey(monkeypatch))


# --- rank_products ---------------------------------------------------------


def test_rank_products_success(monkeypatch):
    resp = _Resp([_ToolBlock({"sku": "B", "confidence": 0.8, "reason": "closest"})])
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(response=resp))
    out = ai.rank_products("milk", [_prod("A"), _prod("B")], settings=_settings())
    assert out == {"sku": "B", "confidence": 0.8, "reason": "closest"}


def test_rank_products_no_key_short_circuits(monkeypatch):
    # No client call should happen when unconfigured.
    monkeypatch.setattr(ai, "_client", lambda s: pytest.fail("client built without a key"))
    assert ai.rank_products("milk", [_prod("A")], settings=_nokey(monkeypatch)) is None


def test_rank_products_no_candidates():
    assert ai.rank_products("milk", [], settings=_settings()) is None


def test_rank_products_api_error_degrades(monkeypatch, caplog):
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(raise_exc=RuntimeError("429")))
    with caplog.at_level("DEBUG"):
        out = ai.rank_products("milk", [_prod("A")], settings=_settings())
    assert out is None
    assert any("rank" in r.message.lower() for r in caplog.records)


def test_rank_products_sdk_missing_degrades(monkeypatch):
    # A missing SDK surfaces as ImportError from _client; must not crash.
    def _boom(_s):
        raise ImportError("no module named 'anthropic'")

    monkeypatch.setattr(ai, "_client", _boom)
    assert ai.rank_products("milk", [_prod("A")], settings=_settings()) is None


def test_rank_products_without_tool_use(monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(response=_Resp([_TextBlock()])))
    assert ai.rank_products("milk", [_prod("A")], settings=_settings()) is None


# --- synthesize_profile ----------------------------------------------------


def _signals():
    return ProfileSignals(total_items=10, distinct_products=5)


def _draft():
    return DietaryProfile(diet_style="omnivore", favored_proteins=["chicken"], notes=[])


def test_synthesize_no_key_returns_draft(monkeypatch):
    draft = _draft()
    out = ai.synthesize_profile(_signals(), draft, settings=_nokey(monkeypatch))
    assert out is draft


def test_synthesize_success_merges(monkeypatch):
    resp = _Resp([_ToolBlock({
        "diet_style": "pescatarian",
        "organic_preference": "high",
        "plant_forward": True,
    })])
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(response=resp))
    out = ai.synthesize_profile(_signals(), _draft(), settings=_settings())
    assert out.diet_style == "pescatarian"
    assert out.organic_preference == "high"
    assert out.source == "heuristic+ai"


def test_synthesize_does_not_clobber_with_empty_values(monkeypatch):
    # AI returns an empty proteins list; the heuristic's proteins must survive.
    resp = _Resp([_ToolBlock({
        "diet_style": "omnivore",
        "organic_preference": "medium",
        "plant_forward": False,
        "favored_proteins": [],
    })])
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(response=resp))
    out = ai.synthesize_profile(_signals(), _draft(), settings=_settings())
    assert out.favored_proteins == ["chicken"]


def test_synthesize_api_error_returns_draft_with_note(monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(raise_exc=RuntimeError("boom")))
    out = ai.synthesize_profile(_signals(), _draft(), settings=_settings())
    assert out.source == "heuristic"  # never flipped to heuristic+ai
    assert any("AI refinement skipped" in n for n in out.notes)


def test_synthesize_without_tool_use_returns_draft(monkeypatch):
    monkeypatch.setattr(ai, "_client", lambda s: _fake_client(response=_Resp([_TextBlock()])))
    draft = _draft()
    assert ai.synthesize_profile(_signals(), draft, settings=_settings()) is draft


# --- _first_tool_input -----------------------------------------------------


def test_first_tool_input_finds_block():
    assert ai._first_tool_input(_Resp([_TextBlock(), _ToolBlock({"sku": "X"})])) == {"sku": "X"}


def test_first_tool_input_none_when_absent():
    assert ai._first_tool_input(_Resp([_TextBlock()])) is None
