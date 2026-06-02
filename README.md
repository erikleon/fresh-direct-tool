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
# One-time: opens a real browser window so you can log in (handles 2FA/captcha).
# The resulting session is encrypted and saved under ./data/.
uv run fdplanner login

# Scrape and print your most recent orders to confirm the pipeline works.
uv run fdplanner history --limit 5

# If automation is blocked, paste an order export instead:
uv run fdplanner import-paste path/to/orders.txt
```

## Security notes

- The Playwright `storage_state` is encrypted with Fernet (`app/security.py`).
- The key lives in `data/secret.key` (0600) unless you set
  `FDPLANNER_ENCRYPTION_KEY`. Keep the key and the session blob in separate backups.
- `data/`, `.env`, and `storage_state*` are git-ignored. Never commit them.

## Layout

```
app/
  config.py            # settings (env-driven)
  security.py          # session encryption
  cli.py               # Phase 0 CLI (login / history / import-paste)
  freshdirect/         # Playwright adapter, isolated behind an interface
    base.py            #   adapter Protocol + domain models
    session.py         #   login capture + session reuse
    history.py         #   order-history scrape + paste fallback
```
