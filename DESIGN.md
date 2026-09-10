# Design System — FreshDirect Weekly Planner

Read this before any visual or UI change to `app/web/`. Don't deviate without
saying so out loud; the choices below hold together, and pulling one thread
loosens the others.

## Product Context

- **What this is** — the household manager's review-and-approve dashboard for a
  weekly FreshDirect order that an agent drafted.
- **Who it's for** — one person, usually on a phone, usually standing in a
  kitchen, deciding whether a cart somebody else built is right.
- **Space** — grocery e-commerce, but only the last mile of it.
- **Project type** — server-rendered internal tool. Jinja + one stylesheet + one
  enhancement script. No build step, no framework, no package manager at runtime.

**The one thing to remember:** *I can clear this cart in ninety seconds on my
phone without thinking about it.*

## The insight this system is built on

Every grocery UI — Instacart, Amazon Fresh, FreshDirect's own — is image-led and
browse-first, because they assume the user is **shopping**. This screen is the
opposite: the cart is already built and the user is **auditing** it. The data
model has no product images at all.

So the reference class is not a storefront. It is a code-review diff or an
expense-report approval: state-first rows, one binary decision per row, a running
total that reacts, and never losing your place. Every layout decision below
follows from that.

## Aesthetic Direction

- **Direction** — industrial / utilitarian, softened. An editable receipt.
- **Decoration** — minimal. Anything on screen that is not state, money, or an
  action is competing with the thing the person came to do.
- **Mood** — quiet, legible, fast. It should feel like checking a list, not
  like being sold to.

## Typography

| Role | Family | Notes |
| --- | --- | --- |
| Everything | `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif` | System stack |
| Data / money | Same stack + `font-variant-numeric: tabular-nums` | Prices and counts are compared down a column; proportional digits shimmy as values change |

**Deliberate deviation:** a system stack is normally the wrong default — it has
no voice. It is right *here*. This tool is local-first and runs on the
household's own machine, sometimes on bad kitchen wifi. A webfont round trip
buys personality and costs a flash of invisible text on the one screen that has
to feel instant. Revisit only if the fonts are self-hosted from `static/`.

Scale (`--fs-*`): `2xs` 11 · `xs` 12.5 · `sm` 13.5 · `base` 15 · `md` 16 ·
`lg` 18.

**`--fs-md` (16px) is load-bearing, not taste.** Any `<input>` or `<select>`
under 16px makes iOS Safari zoom the viewport on focus — which is its own page
jump. Form controls drop to 14.5px only above the desktop breakpoint.

## Color

Approach: keep FreshDirect's own brand pair, add state colors, add nothing else.
No gradients, no accent color of our own.

| Token | Hex | Role |
| --- | --- | --- |
| `--green` | `#298321` | Brand, primary action, "in the cart" |
| `--green-soft` | `#eaf5e8` | Included-row toggle fill, success banners |
| `--green-edge` | `#b9d9b4` | Border of an included toggle |
| `--orange` | `#e8730c` | Brand second half, swapped/handed-off tags |
| `--ink` | `#1a1a1a` | Body text |
| `--muted` | `#6b7280` | Secondary text, labels, the "why" column |
| `--line` | `#e5e7eb` | Borders |
| `--hairline` | `#f1f2f1` | Row separators |
| `--canvas` / `--surface` | `#f7f8f7` / `#fff` | Page / card |
| `--warn` / `--warn-bg` / `--warn-tint` | `#b45309` / `#fef3c7` / `#fffdf5` | Low-confidence line flagged for review |
| `--over` | `#b00020` | Over budget |

Dark mode: not implemented. If added, redesign the surfaces rather than
inverting, and pull saturation down 10–20% — `--green` at full strength on a
dark ground reads neon.

## Spacing

4px base. `--sp-2xs` 2 · `xs` 4 · `sm` 8 · `ms` 12 · `md` 16 · `lg` 24 ·
`xl` 32 · `2xl` 48.

Density is tighter on the phone (card padding `--sp-md`) than on the desktop
(`--sp-lg`), because vertical space is the scarce resource on a phone and
horizontal space is the scarce resource nowhere.

## Layout

**Mobile-first.** The base rules in `style.css` *are* the phone layout. There is
exactly one breakpoint, `@media (min-width: 800px)`, which restores the desktop
table.

**Why 800 and not 720:** the cart's six columns need roughly 775px before they
stop squeezing — measured, not guessed. Below that the stacked layout is
genuinely better, and `.cart-wrap` carries `overflow-x: auto` above it as a
backstop.

**The cart row.** Under 800px each `<tr>` is a grid:

```
"toggle need     price"
"toggle prod     prod"
"toggle qty      qty"
"toggle why      why"
```

The include/exclude control sits in a fixed 44px rail down the left, so the
decision is always in the same place no matter how long the product name is.

**ARIA is mandatory here.** Overriding `display` on `<table>`/`<tr>`/`<td>` drops
the implicit table semantics, so the template spells out `role="table"`,
`role="rowgroup"`, `role="row"`, `role="cell"`. Don't remove them; a test guards
this.

`--tap: 44px` is the floor for every interactive target. Content max width 980px.
Radius: `--r-sm` 6 · `--r-md` 8 · `--r-lg` 12 · `--r-pill` 999.

## Motion

Micro only — feedback, never choreography. Enter `ease-out`
(`cubic-bezier(.2,.8,.3,1)`), and `prefers-reduced-motion: reduce` cuts
everything to ~0.

| Token | Duration | Used for |
| --- | --- | --- |
| `--t-micro` | 90ms | Toggle press, colour change |
| `--t-short` | 150ms | Busy-state tint |
| `--t-med` | 250ms | Budget bar fill |

The budget bar's transition is the answer to "did that do anything?" — it is the
only animation on the page that carries information, so it should be visible.

## The no-jump rule

**The page must not move under a thumb that is mid-decision.** This is the
constraint the cart layout exists to satisfy, and it has four separate parts —
all four are covered by tests:

1. **No scroll reset.** `live.js` posts the line forms in the background and
   swaps three regions in place, so nothing navigates. Without JS, the server
   redirects to `#line-<id>` (`_line_anchor` in `server.py`) so the browser lands
   back on the row that was edited rather than at the top of the page.
2. **No row-height change.** A dropped row keeps its full height and every
   control it had; it is dimmed and its price struck through, never collapsed.
   Collapsing it would shift every row below out from under the thumb that just
   tapped. **Do not "tidy this up" by hiding dropped rows.**
3. **No control-width change.** The toggle is a fixed 44×44 box with the glyph
   centred, so ✓ ↔ + cannot reflow the row. (The original used `✓` and a
   full-width `＋`, which are different advance widths.)
4. **No reflow above the cart.** The budget label's over/under clause is a block,
   so the label is exactly two lines whether it reads "· under budget" or
   "· over by $12.40". That block sits above the table, and any rewrap there
   shifts the whole cart.

## Intrinsic width: the `min-width: auto` trap

**Every flex or grid child that can hold long content needs `min-width: 0`.**

A flex/grid child defaults to `min-width: auto`, which floors it at its
min-content width. For a `<select>` that floor is its *widest `<option>`* — and
this cart's options are whole product names, measuring 556px against a 390px
viewport. Chromium clamps a select's intrinsic width and absorbs the problem;
WebKit honours it. The result was a document roughly twice the viewport width on
iOS, which Safari resolves by zooming out to fit — so the page looked shrunk with
dead space beside it, while every Chromium measurement said zero overflow.

Testing "does it overflow in Chromium" cannot catch this. `min-content.mjs`
tests the engine-independent property instead: no in-flow flex/grid child may
combine `min-width: auto` with a min-content width wider than the viewport.

## Progressive enhancement

`live.js` is enhancement only. Every form works with the file deleted.

- It posts the existing form to the existing route and lets `fetch` follow the
  existing 303, then swaps `#cart-body`, `#budget-region` and `#actions-region`
  from the returned page. **Deliberately no fragment endpoint** — one render
  path, so the live update cannot drift from the reload it replaces.
- Requests are serialised. Two POSTs racing could apply out of order and show a
  subtotal that never existed.
- The toggle flips optimistically before the round trip, which is safe precisely
  because of rule 2 above: the row's height is identical in both states.
- With JS live it adds `.js-live` to `<html>`, which hides the `.js-optional`
  confirm buttons ("swap", "set") and submits those controls on `change`
  instead — one tap per edit instead of two. Without JS the buttons stay, because
  they are the only way to submit.
- Any failure falls back to a real `form.submit()`.

## Decisions Log

| Date | Decision | Rationale |
| --- | --- | --- |
| 2026-09-10 | Mobile-first rewrite of `style.css` with one 800px breakpoint | The stylesheet had **zero** media queries; the cart rendered 775px wide in a 390px viewport, 426px of horizontal overflow |
| 2026-09-10 | `<tr>` becomes a grid under 800px, with explicit ARIA roles | Stacked rows beat sideways scrolling; overriding `display` costs the implicit table semantics |
| 2026-09-10 | Kept the system font stack | Local-first tool on possibly-bad wifi; a webfont round trip costs more than it buys |
| 2026-09-10 | Kept FreshDirect green/orange, added state tokens only | The brand pair is doing its job; the screen needed structure, not a new palette |
| 2026-09-10 | Dropped rows keep full height and all controls | Collapsing them shifts rows under a thumb mid-tap — the reported bug |
| 2026-09-10 | Line edits redirect to `#line-<id>` | Redirect-after-post lands at scroll-top; on row 12 that throws you to the header |
| 2026-09-10 | `live.js` re-parses the full page rather than a fragment route | One render path; a fragment endpoint is a second source of truth that drifts |
| 2026-09-10 | 44px minimum tap targets | The include/exclude control was 25×22 |
| 2026-09-10 | 16px form controls below 800px | Anything smaller makes iOS Safari zoom on focus |
| 2026-09-10 | `min-width: 0` on selects, cart cells and the line forms | A select's min-content width is its widest option (556px here); WebKit honours that floor and Chromium does not, so iOS Safari zoomed the whole page out to fit |
| 2026-09-10 | Running total pinned beside Approve on phones | The budget bar scrolls away after three rows; the number you are deciding against should stay put |
