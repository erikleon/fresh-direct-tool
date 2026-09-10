# Browser checks

The layout rules in `DESIGN.md` are geometry, and geometry needs a real engine to
verify. These two scripts do that. They are not part of `pytest` — they need
Chromium and a running server — so run them by hand when touching
`app/web/static/` or the cart markup.

```bash
uv sync --all-extras
npm install playwright          # anywhere; the scripts take no other deps

# Order matters: seeding replaces the sqlite file, and a server started first
# keeps an engine pointing at the old inode, so its writes fail.
uv run python tests/browser/seed_preview.py
FDPLANNER_DATA_DIR=/tmp/fdp-preview-data \
  uv run uvicorn app.web.server:app --port 8899 &

node tests/browser/no-jump.mjs   # the four no-jump rules, JS and no-JS paths
node tests/browser/widths.mjs    # horizontal overflow, 320px through 1280px
```

`no-jump.mjs` asserts that toggling a line leaves `scrollY`, the row's viewport
position, the row's height and the document height all unchanged; that the
budget block keeps its height when the total crosses the cap; that the confirm
buttons disappear only when JS is live; and that the no-JS redirect lands on
`#line-<id>` with the row on screen.

`widths.mjs` asserts zero horizontal overflow at every width from 320px up, and
that the cart flips from stacked rows to a table exactly at the 800px
breakpoint.

Both exit non-zero on failure.
