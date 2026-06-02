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

Building **Phase 0 — automation spike**: prove we can log into FreshDirect and
scrape order history. See [the plan](~/.claude/plans) for the full roadmap.

| Phase | Scope | State |
|------|-------|-------|
| 0 | FD session capture + order-history scrape (+ paste fallback) | in progress |
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

# Scrape and print your most recent orders to confirm the pipeline works.
uv run fdplanner history --limit 5
# If a headless scrape gets challenged, run it visibly on the warm profile:
uv run fdplanner history --limit 5 --headed

# If automation is blocked, paste an order export instead:
uv run fdplanner import-paste path/to/orders.txt
```

> **Why real Chrome?** FreshDirect uses Akamai Bot Manager, which 403s throwaway
> automation (Playwright's bundled Chromium advertises `navigator.webdriver`). We
> drive real Google Chrome (`channel="chrome"`) from a dedicated persistent
> profile with the automation flags removed, so the browser looks genuine.

## Security notes

- The FreshDirect session lives in a **dedicated** Chrome profile at
  `data/chrome_profile/` — never your main Chrome profile. Its cookies are
  encrypted at rest by the OS keychain (Chrome Safe Storage).
- `data/` and `.env` are git-ignored. Never commit them.

## Layout

```
app/
  config.py            # settings (env-driven)
  cli.py               # Phase 0 CLI (login / history / debug-dump / import-paste)
  freshdirect/         # Playwright adapter, isolated behind an interface
    base.py            #   adapter Protocol + domain models
    session.py         #   real-Chrome login + persistent-profile reuse
    history.py         #   order-history scrape + paste fallback
    debug.py           #   dev tool: dump live DOM for selector tuning
```
