# FreshDirect Weekly Planner

An AI agent (plus web app) that plans a household's weekly **FreshDirect** grocery
order. It studies your order history, predicts which staples are due for restock,
proposes a few meals, builds a draft cart with live prices, keeps the total under
a weekly budget (suggesting cheaper swaps when it runs over), and presents the
whole thing to the household manager for review and approval. On approval it hands
off a ready-to-checkout cart.

> Local-first and single-household by design: a stored FreshDirect session grants
> full access to your account, so everything runs on your own machine/server and
> the session is encrypted at rest.

## Status

**Phase 0 — automation spike: done.** We log into FreshDirect through real Chrome
(past Akamai) and read full order history + line items via their GraphQL API.

| Phase | Scope | State |
|------|-------|-------|
| 0 | FD login + order history & line items via GraphQL (+ paste fallback) | ✅ done |
| 1 | SQLite persistence, resumable backfill, spend tracking + replenishment | ✅ done (CLI) |
| 2 | Profile ✅ · search ✅ · SKU match ✅ · restock draft plan + budget ✅ · meals (later) | ✅ done (CLI) |
| 3 | Review dashboard ✅ · edit/approve ✅ · cart hand-off ✅ · delivery, scheduler | in progress |
| 4 | (future) Auto-checkout behind a flag | planned |

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                       # install dependencies
uv run playwright install chromium
```

## Phase 0 usage

```bash
# One-time: opens real Google Chrome so you can log in (handles 2FA/captcha).
# The session persists in a dedicated Chrome profile under ./data/.
uv run fdplanner login

# Read your recent orders to confirm the pipeline works.
uv run fdplanner history --limit 5
# Include line items (one extra page load per order):
uv run fdplanner history --limit 3 --details
# If a headless run gets challenged, run it visibly on the warm profile:
uv run fdplanner history --limit 5 --headed

# If automation is ever blocked, paste an order export instead:
uv run fdplanner import-paste path/to/orders.txt
```

## Phase 1 usage (analytics)

```bash
# One-time (resumable): sync full history + line items into ./data/fdplanner.sqlite
uv run fdplanner backfill

# Spend totals, monthly trend, category breakdown
uv run fdplanner spend

# Items predicted due for restock, most overdue first
uv run fdplanner due

# Infer a dietary profile from what you buy (saved to data/profile.json)
uv run fdplanner profile

# Search the live catalog
uv run fdplanner search "organic whole milk"

# Resolve a free-text need to a real SKU (alias → heuristic → optional Claude)
uv run fdplanner match "organic whole milk"
# Teach the right product so future matches resolve instantly
uv run fdplanner match "organic whole milk" --teach DAI0059088

# Build a restock draft cart from items due, priced live, under a budget cap
uv run fdplanner plan --horizon 7 --budget 200

# Or use the web dashboard: review, edit, swap, and approve in the browser
uv run fdplanner serve            # → http://127.0.0.1:8000
```

The dashboard (`app/web/`, FastAPI + Jinja, server-rendered so it works without
client JS) lets the household manager **review** the draft cart, **edit**
quantities, **remove** items, **swap** to a cheaper/different product (which is
learned as an alias), watch the **budget bar**, **approve**, and then **send the
approved cart to FreshDirect** behind an explicit confirm. Hand-off
(`app/handoff.py`) drives the real "Add to bag" buttons to populate your cart
(quantity 1 per line; set higher quantities in the cart) and returns a checkout
link — it never places the order or pays. You review and check out yourself.
Delivery address/window/tip pickers and the weekly scheduler are next.

The draft plan (`app/planner.py`) takes the items due, prices each against the
live catalog — preferring the *exact* product you've bought before (its
historical `product_id`) — then reconciles against the weekly cap with
cheaper-swap suggestions (`app/budget.py`, pure + tested). Low-confidence matches
are flagged for review. Meal planning will layer on top once an API key is set.

Matching (`app/match.py`) scores search candidates on term overlap, brand,
organic, and single-unit-vs-multipack, with **ambiguity-aware confidence** (a
toss-up isn't reported as certain) so weak matches flag for review. Corrections
are remembered as aliases; with an Anthropic key, Claude re-ranks the shortlist.

`profile` works fully offline from the data (organic preference, plant-forward
lean, proteins, household signals like a baby in the house). If
`FDPLANNER_ANTHROPIC_API_KEY` is set it also refines the draft with Claude
(`app/ai.py`, prompt-cached, structured tool output).

Money is stored as integer cents (`app/money.py`) so sums stay exact. The
replenishment forecast (`app/analytics.py`) estimates each item's buying cadence
from how often it appears across orders and flags what's due.

> **Why real Chrome?** FreshDirect uses Akamai Bot Manager, which 403s throwaway
> automation (Playwright's bundled Chromium advertises `navigator.webdriver`). We
> drive real Google Chrome (`channel="chrome"`) from a dedicated persistent
> profile with the automation flags removed, so the browser looks genuine.

> **How history is read.** FreshDirect's account pages are a React SPA backed by
> a GraphQL API. Instead of scraping hashed-classname DOM, we boot the app and
> intercept its GraphQL **responses** by operation name — `ordersHistory` (the
> order list) and `order` (each order's line items) — and parse that JSON. Far
> more robust than CSS selectors. See `app/freshdirect/history.py`.

## Security notes

- The FreshDirect session lives in a **dedicated** Chrome profile at
  `data/chrome_profile/` — never your main Chrome profile. Its cookies are
  encrypted at rest by the OS keychain (Chrome Safe Storage).
- `data/` and `.env` are git-ignored. Never commit them.

## Layout

```
app/
  config.py            # settings (env-driven)
  cli.py               # CLI: login / history / backfill / spend / due / import-paste
  money.py             # Decimal dollars <-> integer cents
  db.py, models.py     # SQLite engine + SQLModel tables (orders, order_items)
  ingest.py            # resumable backfill into the DB
  analytics.py         # spend summary + replenishment cadence (pure + DB-backed)
  freshdirect/         # automation adapter, isolated behind an interface
    base.py            #   adapter Protocol + domain models (Order/OrderItem/Address)
    session.py         #   real-Chrome login + persistent-profile reuse
    client.py          #   booted SPA session: order_summaries / order_lines
    parse.py           #   GraphQL/text → domain models (pure, tested)
    history.py         #   paste fallback
    debug.py           #   dev tools: dump page HTML, capture network/API
```
