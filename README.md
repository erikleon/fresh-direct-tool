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
| 1 | Data model, replenishment cadence, spend-tracking dashboard | planned |
| 2 | AI meal planning, SKU matching, budget swaps → draft plan | planned |
| 3 | Review dashboard, approve → cart hand-off, email digest, scheduler | planned |
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
  cli.py               # Phase 0 CLI (login / history / import-paste / debug-*)
  freshdirect/         # automation adapter, isolated behind an interface
    base.py            #   adapter Protocol + domain models (Order/OrderItem/Address)
    session.py         #   real-Chrome login + persistent-profile reuse
    history.py         #   GraphQL interception (ordersHistory/order) + paste fallback
    debug.py           #   dev tools: dump page HTML, capture network/API
```
