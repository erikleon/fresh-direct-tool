# Plan — Harden the AI-assisted paths before relying on them
Produced by /plan-eng-review on 2026-07-14
Branch: bugfix/mcp-playwright-async

## Problem

An Anthropic API key was configured, which switches on ~180 lines of previously
dormant code (`app/ai.py`) plus the AI branches of `match.resolve()` and
`profile.infer_profile()`. That code had **zero test coverage** and a silent
failure swallow. This plan hardens the two surfaces that actually use the AI so
they are safe to depend on.

## Scope decision: HOLD

The single most important finding: **the weekly cart-builder does not use the AI
at all.** `planner.build_plan` calls the pure heuristic `score_candidates`
(planner.py:73) and prefers the exact historical SKU (planner.py:69). The API key
changes nothing about the cart.

```
API key ON  ─┬─►  fd_match   → match.resolve()   → ai.rank_products()
             └─►  fd_profile → infer_profile()   → ai.synthesize_profile()

build_plan (weekly cart)  ──►  score_candidates()   [heuristic; AI never consulted]
```

So scope is held to hardening `fd_match` + `fd_profile`. We are NOT wiring AI into
the cart-builder: restock's exact-history matching is more reliable than an LLM
guess, and adding it would mean ~20 Opus calls per plan.

## What already exists (reused, not rebuilt)

- Heuristic tiers (`best_match`, `score_candidates`, `draft_profile`,
  `compute_signals`) stand alone and are well-tested (16 tests). The AI layer is
  pure enrichment with graceful fallback when no key is set. This plan keeps that
  structure and hardens the enrichment layer only.

## Decisions

**D1 — AI confidence cannot bypass human review (refined).**
`resolve()` currently lets the model's self-reported confidence become the line's
confidence, which gates `needs_review` (match.py:56, 219). Refined mechanism:
- Validate the AI's sku against the **shortlist** (what Claude actually saw), not
  all 30 candidates.
- Empty sku ("nothing fits", allowed by the prompt at ai.py:122) → force
  `needs_review`, don't silently keep the heuristic pick.
- Cap AI confidence at the heuristic's calibrated confidence **for the AI-chosen
  product** — the AI can lower confidence but not inflate it. Needs per-product
  heuristic scores available inside `resolve()`.
- Drop the "force review if pick not in heuristic top-N" idea — dead code, since
  the shortlist already IS the heuristic top-5.

**D2 — Fix the silent failure without a new crash surface.**
`rank_products` does `except Exception: return None` with no log (ai.py:170).
Log narrow `anthropic.APIError` at debug with `exc_info`, AND keep a broad
`except` (covers the lazy `import anthropic` / `ImportError`) that still degrades
to the heuristic. Do the same for `synthesize_profile` (keep its skip-note).

**D3 — DRY the confidence formula.**
Extract `base * (0.6 + 0.4 * separation)` (duplicated at match.py:136-138 and
planner.py:76-78) into `calibrated_confidence(top, runner_up)` in match.py; call
it from `best_match` and `planner._line_for`. Unit-test the helper directly.

**D4 — Unit tests with a mocked client.**
Add coverage for every AI-activated path (see the test plan). Mock `ai._client`
so the real `messages.create` → `_first_tool_input` → fallback logic is exercised
without network. A real-API prompt-quality eval is deferred to TODOS.md.

**D5 — Keep Opus 4.8.**
`planner_model = claude-opus-4-8` for both calls. Volume is trivial (one call per
interactive `fd_match`); quality-first is the right call at one household.

## Accepted bug-fixes (surfaced by the outside voice, low-risk hardening)

- **Merge clobber (ai.py:114):** `{**draft, **data}` lets an AI empty list or
  vaguer string overwrite heuristic-derived profile fields. Make the merge prefer
  non-empty AI values / treat AI as additive, so it can't wipe real signal.
- **Sold-out heuristic pick (match.py:131):** `best_match` returns `ranked[0]`
  even when sold out (only the -1.0 penalty and the <0.5 threshold guard it). Add
  an explicit guard so a sold-out top pick forces review / no pick.

## NOT in scope

- Wiring AI into `build_plan` — deliberately held (see scope decision).
- Real-API prompt-quality eval — deferred to TODOS.md.
- Downgrading the model tier — considered, kept Opus (D5).
- Prompt-injection hardening beyond the existing forced tool schema — the schema
  already bounds blast radius; noted, no action.
- Reusing a single Anthropic client across calls — negligible at this volume.

## Failure modes (new/affected codepaths)

| Codepath | Realistic prod failure | Test? | Error handling? | User sees? |
|----------|------------------------|-------|-----------------|------------|
| rank_products API error | rate limit / network / bad key | add (D4) | yes, degrade + log (D2) | heuristic result, `method` reveals fallback |
| rank_products SDK missing | `[mcp]` extra not installed | add (D4) | yes, broad except (D2) | heuristic result |
| resolve empty-sku | Claude says "nothing fits" | add (D4) | yes, force review (D1) | line flagged for review |
| resolve hallucinated sku | model returns off-shortlist sku | add (D4) | yes, shortlist validation (D1) | heuristic result |
| synthesize_profile clobber | AI returns empty proteins list | add (D4) | yes, additive merge (accepted fix) | heuristic values preserved |

No critical gaps: after this plan every new failure mode has a test AND error
handling AND is observable (via `method`/`needs_review`), not silent.

## Parallelization

| Step | Modules touched | Depends on |
|------|-----------------|------------|
| S1 extract `calibrated_confidence` (D3) | app/match.py, app/planner.py | — |
| S2 refine resolve() AI branch (D1) | app/match.py | S1 (uses the helper for per-product conf) |
| S3 fix ai.py logging + clobber (D2, accepted) | app/ai.py | — |
| S4 tests (D4) | tests/test_ai.py, test_match.py, test_profile.py | S1-S3 |

- Lane A: S1 → S2 (both in match.py; serialize to avoid conflict).
- Lane B: S3 (app/ai.py) — parallel with Lane A, no shared file.
- Lane C: S4 tests — after A and B land.
- Conflict flag: S1 and S2 both touch match.py — same lane, not parallel.

## Completion summary

- Step 0 (Scope Challenge): HOLD — harden fd_match + fd_profile; cart-builder out
- Architecture review: 1 issue (confidence trust boundary → D1)
- Code Quality review: 2 issues (silent failure → D2, DRY → D3)
- Test review: coverage diagram produced, 20 gaps (all AI-activated paths) → D4
- Performance review: 1 issue (model tier → D5, kept Opus)
- Outside voice: ran (Claude subagent; codex binary missing) → SOUND-WITH-GAPS;
  revised D1 mechanism, revised D2, added 2 accepted bug-fixes
- NOT in scope: written
- What already exists: written
- TODOS.md: 1 item added (real-API eval)
- Failure modes: 0 critical gaps after plan
- Parallelization: 3 lanes
