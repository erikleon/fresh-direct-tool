"""Phase 0 CLI: capture a session and verify the order-history pipeline.

    fdplanner login                    # one-time interactive login (headed)
    fdplanner history --limit 5        # read recent orders via GraphQL
    fdplanner history --limit 3 --details   # ...including line items
    fdplanner import-paste FILE        # parse a pasted export instead
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.freshdirect.base import Order, SessionExpired
from app.freshdirect.client import fetch_order_history
from app.freshdirect.history import parse_pasted_history
from app.freshdirect.session import capture_session

app = typer.Typer(add_completion=False, help="FreshDirect Weekly Planner (Phase 0)")
console = Console()


@app.command()
def login() -> None:
    """Open real Chrome, log into FreshDirect, and persist the session profile."""
    capture_session(get_settings())


@app.command()
def history(
    limit: int = typer.Option(10, help="How many recent orders to show"),
    details: bool = typer.Option(False, help="Also fetch line items (slower)"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Read recent orders from the saved session (via GraphQL) and print them."""
    try:
        orders = fetch_order_history(
            get_settings(), limit=limit, with_details=details, headed=headed
        )
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    _render(orders)


@app.command("debug-dump")
def debug_dump(
    url: str = typer.Option(None, help="Override the orders-page URL to capture"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Dev tool: capture the live orders page (HTML + screenshot) for selector tuning."""
    from app.freshdirect.debug import dump_orders_page

    try:
        result = dump_orders_page(get_settings(), url=url, headed=headed)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"Final URL : {result.final_url}")
    console.print(f"Title     : {result.title}")
    console.print(f"HTML      : {result.html_path}")
    console.print(f"Screenshot: {result.screenshot_path}")
    if result.looks_logged_out:
        console.print("[yellow]Page looks logged-out — session may be expired.[/yellow]")


@app.command("debug-net")
def debug_net(
    url: str = typer.Option(None, help="Override the orders-page URL"),
    headed: bool = typer.Option(False, help="Run visibly"),
) -> None:
    """Dev tool: boot the SPA, open orders, and record XHR/fetch to find the API."""
    from app.freshdirect.debug import capture_network

    try:
        result = capture_network(get_settings(), url=url, headed=headed)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"Final URL : {result.final_url}")
    console.print(f"Network log: {result.log_path}")
    console.print(f"\nSaved JSON bodies ({len(result.saved_bodies)}):")
    for line in result.saved_bodies:
        console.print(f"  {line}")
    console.print(f"\nAPI-ish endpoints seen ({len(result.candidates)}):")
    for url_ in result.candidates:
        console.print(f"  {url_}")


@app.command()
def backfill(
    limit: int = typer.Option(500, help="Max recent orders to sync"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Sync full order history (with line items) into the local database.

    Resumable: re-running only fetches orders whose details aren't stored yet.
    """
    from app.ingest import backfill as run_backfill

    try:
        result = run_backfill(
            get_settings(), limit=limit, headed=headed, progress=console.print
        )
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"\n[green]Backfill done.[/green] {result.orders_seen} orders seen, "
        f"{result.details_fetched} newly detailed, {result.skipped} already current."
    )


@app.command()
def spend() -> None:
    """Show spend totals, monthly trend, and category breakdown from the DB."""
    from app.analytics import spend_summary
    from app.money import dollars

    s = spend_summary(get_settings())
    if s.order_count == 0:
        console.print("[yellow]No orders yet — run `fdplanner backfill` first.[/yellow]")
        return

    console.print(
        f"[bold]{s.order_count} orders[/bold] · {dollars(s.total_cents)} total · "
        f"{dollars(s.avg_order_cents)} avg · {s.first_date} → {s.last_date}\n"
    )

    by_month = Table(title="Spend by month", title_justify="left")
    by_month.add_column("Month")
    by_month.add_column("Orders", justify="right")
    by_month.add_column("Spent", justify="right")
    for ym, cents, count in s.by_month:
        by_month.add_row(ym, str(count), dollars(cents))
    console.print(by_month)

    by_cat = Table(title="Spend by category", title_justify="left")
    by_cat.add_column("Category")
    by_cat.add_column("Spent", justify="right")
    for cat, cents in s.by_category[:15]:
        by_cat.add_row(cat, dollars(cents))
    console.print(by_cat)


@app.command()
def due(
    limit: int = typer.Option(25, help="How many predictions to show"),
    min_purchases: int = typer.Option(3, help="Min times bought to predict"),
    horizon: int = typer.Option(
        7, help="Only items due within this many days (use --all to ignore)"
    ),
    all_: bool = typer.Option(
        False, "--all", help="Include out-of-rotation and far-future items"
    ),
) -> None:
    """Show active staples predicted due for restock, most overdue first.

    By default this is a *this-week* shopping signal: items still in rotation,
    due within `horizon` days. `--all` shows everything (including items that
    fell out of rotation, which read as wildly overdue).
    """
    from app.analytics import replenishment

    preds = replenishment(get_settings(), min_purchases=min_purchases)
    if not preds:
        console.print(
            "[yellow]Not enough detailed history yet — run `fdplanner backfill`.[/yellow]"
        )
        return

    if not all_:
        preds = [p for p in preds if p.active and p.days_overdue >= -horizon]

    title = (
        "Replenishment forecast (all items)"
        if all_
        else f"Active staples — due now, or within {horizon}d"
    )
    table = Table(title=title, title_justify="left")
    table.add_column("Item")
    table.add_column("×", justify="right")
    table.add_column("Every", justify="right")
    table.add_column("Last")
    table.add_column("Predicted")
    table.add_column("Status")
    for p in preds[:limit]:
        if p.days_overdue >= 0:
            status = f"[red]due (+{p.days_overdue}d)[/red]"
        else:
            status = f"[green]in {-p.days_overdue}d[/green]"
        if all_ and not p.active:
            status += " [dim](dropped)[/dim]"
        table.add_row(
            p.name,
            str(p.times_bought),
            f"{p.mean_interval_days:g}d",
            str(p.last_purchased),
            str(p.predicted_next),
            status,
        )
    console.print(table)
    if not all_:
        console.print(f"\n[green]{len(preds)} active item(s) due within {horizon}d.[/green]")


@app.command()
def search(
    query: str = typer.Argument(..., help="Product search text"),
    limit: int = typer.Option(12, help="Max results to show"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Search the live FreshDirect catalog and print candidate products."""
    from app.freshdirect.client import FreshDirectClient

    try:
        products = FreshDirectClient(get_settings(), headed=headed).search_products(
            query, limit=limit
        )
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not products:
        console.print(f"[yellow]No results for '{query}'.[/yellow]")
        return
    table = Table(title=f"Results for '{query}'", title_justify="left")
    table.add_column("SKU")
    table.add_column("Brand")
    table.add_column("Product")
    table.add_column("Size")
    table.add_column("Price", justify="right")
    table.add_column("", justify="left")
    for p in products:
        table.add_row(
            p.sku, p.brand or "", p.name, p.unit_size or "",
            p.formatted_price or "", "[red]sold out[/red]" if p.sold_out else "",
        )
    console.print(table)


@app.command()
def match(
    item: str = typer.Argument(..., help="Free-text item to resolve to a SKU"),
    brand: str = typer.Option(None, help="Preferred brand"),
    teach: str = typer.Option(None, help="Record this SKU as the alias for the item"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Resolve a grocery need to a real FreshDirect product (alias → heuristic)."""
    from app.freshdirect.client import FreshDirectClient
    from app.match import resolve, set_alias

    if teach:
        set_alias(item, teach, settings=get_settings())
        console.print(f"[green]Learned:[/green] '{item}' → SKU {teach}")
        return

    try:
        candidates = FreshDirectClient(get_settings(), headed=headed).search_products(
            item, limit=30
        )
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    result = resolve(item, candidates, preferred_brand=brand, settings=get_settings())
    if result.pick is None:
        console.print(f"[yellow]No match for '{item}'.[/yellow]")
        return

    flag = "[red](needs review)[/red]" if result.needs_review else "[green]✓[/green]"
    console.print(
        f"{flag}  [bold]{result.pick.one_line()}[/bold]\n"
        f"   SKU {result.pick.sku} · {result.method} · confidence {result.confidence:.0%}"
    )
    if result.alternatives:
        console.print("\n   Alternatives:")
        for alt in result.alternatives:
            console.print(f"    · {alt.one_line()}  [dim]({alt.sku})[/dim]")
    console.print(
        f"\n[dim]Wrong pick? Teach it: fdplanner match \"{item}\" --teach <SKU>[/dim]"
    )


@app.command()
def profile(
    no_ai: bool = typer.Option(False, "--no-ai", help="Heuristics only, skip Claude"),
) -> None:
    """Infer (and save) a household dietary profile from purchase history.

    Works fully offline from the data; if an Anthropic API key is configured it
    also refines the draft with Claude.
    """
    from app import ai
    from app.profile import infer_profile

    profile, signals = infer_profile(get_settings(), use_ai=not no_ai)
    if signals.total_items == 0:
        console.print("[yellow]No items yet — run `fdplanner backfill` first.[/yellow]")
        return
    path = profile.save(get_settings())

    console.print(f"[bold]Dietary profile[/bold]  [dim]({profile.source})[/dim]\n")
    console.print(f"  Diet style       : {profile.diet_style}")
    console.print(f"  Organic pref     : {profile.organic_preference}")
    console.print(f"  Plant-forward    : {'yes' if profile.plant_forward else 'no'}")
    console.print(f"  Favored proteins : {', '.join(profile.favored_proteins) or '—'}")
    console.print(f"  Likely avoids    : {', '.join(profile.likely_avoids) or '—'}")
    for note in profile.household_notes:
        console.print(f"  Household        : {note}")
    console.print(f"  Staple brands    : {', '.join(profile.staple_brands[:8])}")
    if profile.notes:
        console.print("\n  Notes:")
        for n in profile.notes:
            console.print(f"   • {n}")
    if not no_ai and not ai.is_configured(get_settings()):
        console.print(
            "\n[dim]No Anthropic key set — used heuristics only. Set "
            "FDPLANNER_ANTHROPIC_API_KEY to enrich with Claude.[/dim]"
        )
    console.print(f"\n[green]Saved to {path}[/green]")


@app.command()
def plan(
    horizon: int = typer.Option(7, help="Plan for items due within this many days"),
    max_items: int = typer.Option(25, help="Cap how many items to price"),
    budget: float = typer.Option(None, help="Weekly $ cap (overrides config)"),
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Build a restock draft cart from items due, priced live, under budget."""
    from app.money import dollars
    from app.planner import build_plan

    try:
        draft = build_plan(
            get_settings(), horizon_days=horizon, max_items=max_items,
            budget=budget, headed=headed, progress=console.print,
        )
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not draft.lines:
        console.print("\n[yellow]Nothing due — no draft plan. Try a larger --horizon.[/yellow]")
        return

    table = Table(title=f"\nDraft cart — week of {draft.week_of}", title_justify="left")
    table.add_column("Item")
    table.add_column("Chosen product")
    table.add_column("Price", justify="right")
    table.add_column("Why")
    table.add_column("", justify="left")
    for ln in draft.lines:
        chosen = ln.chosen
        swapped = " [yellow](swapped ↓)[/yellow]" if ln.swap else ""
        flag = "[red]review[/red]" if ln.needs_review else ""
        table.add_row(
            ln.need,
            (chosen.one_line()) + swapped,
            dollars(ln.line_cents),
            ln.reason,
            flag,
        )
    console.print(table)

    sub = dollars(draft.subtotal_cents)
    if draft.budget_cap_cents is None:
        console.print(f"\n[bold]Subtotal: {sub}[/bold]  (no budget cap set)")
    elif draft.over_by_cents > 0:
        console.print(
            f"\n[bold]Subtotal: {sub}[/bold] · cap {dollars(draft.budget_cap_cents)} · "
            f"[red]over by {dollars(draft.over_by_cents)}[/red] even after swaps"
        )
    else:
        console.print(
            f"\n[bold]Subtotal: {sub}[/bold] · cap {dollars(draft.budget_cap_cents)} · "
            f"[green]under budget[/green]"
        )
    if draft.review_lines:
        console.print(f"[dim]{len(draft.review_lines)} line(s) flagged for review.[/dim]")


@app.command()
def addresses(
    headed: bool = typer.Option(False, help="Run visibly (clears some bot challenges)"),
) -> None:
    """Fetch and cache your saved FreshDirect delivery addresses."""
    from app.delivery import fetch_addresses

    try:
        addrs = fetch_addresses(get_settings(), headed=headed)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if not addrs:
        console.print("[yellow]No saved addresses found.[/yellow]")
        return
    for a in addrs:
        mark = "[green]●[/green]" if a.selected else " "
        console.print(f" {mark} {a.one_line()}  [dim]({a.id})[/dim]")
    console.print(f"\n[green]Cached {len(addrs)} address(es).[/green]")


@app.command()
def digest(
    plan_id: int = typer.Option(None, help="Which plan to summarize (default: latest)"),
) -> None:
    """Email a review digest for a plan (or write an HTML preview if SMTP is unset)."""
    from app.notify.email import send_digest
    from app.plans import get_plan, latest_plan_id

    pid = plan_id or latest_plan_id(get_settings())
    if pid is None:
        console.print("[yellow]No plan yet — run `fdplanner plan` or `fdplanner run-weekly`.[/yellow]")
        raise typer.Exit(code=1)
    plan = get_plan(pid, get_settings())
    result = send_digest(plan, get_settings())
    color = "green" if result.sent else "yellow"
    console.print(f"[{color}]{result.detail}[/{color}]")


@app.command("run-weekly")
def run_weekly_cmd() -> None:
    """Run the weekly job once now: build this week's draft and send the digest."""
    from app.scheduler import run_weekly

    try:
        result = run_weekly(get_settings(), progress=console.print)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    sent = "emailed" if result.digest_sent else "previewed"
    console.print(
        f"\n[green]Weekly run done.[/green] Plan #{result.plan_id} drafted and {sent}."
    )


@app.command()
def schedule() -> None:
    """Start the blocking weekly scheduler (wakes on schedule, drafts, emails)."""
    from app.scheduler import start_scheduler

    try:
        start_scheduler(get_settings(), progress=console.print)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
) -> None:
    """Run the review-and-approve web dashboard."""
    import uvicorn

    console.print(f"Dashboard at [bold]http://{host}:{port}[/bold]")
    uvicorn.run("app.web.server:app", host=host, port=port, reload=reload)


@app.command("import-paste")
def import_paste(file: Path = typer.Argument(..., help="Text file of pasted orders")) -> None:
    """Parse a pasted order export (offline fallback) and print it."""
    if not file.exists():
        console.print(f"[red]No such file: {file}[/red]")
        raise typer.Exit(code=1)
    _render(parse_pasted_history(file.read_text()))


def _render(orders: list[Order]) -> None:
    if not orders:
        console.print("[yellow]No orders found.[/yellow]")
        return
    for order in orders:
        total = f"${order.total}" if order.total is not None else "total n/a"
        title = f"Order #{order.order_id} — {order.ordered_on} ({total})"
        meta = []
        if order.status:
            meta.append(order.status)
        if order.address:
            meta.append(order.address.one_line())
        if order.delivery_start:
            meta.append(f"deliver {order.delivery_start:%a %b %d %H:%M}")
        if meta:
            title += "\n" + "  ·  ".join(meta)

        if order.items:
            table = Table(title=title, title_justify="left", show_lines=False)
            table.add_column("Qty", justify="right")
            table.add_column("Item")
            table.add_column("Brand")
            table.add_column("Dept")
            table.add_column("Line", justify="right")
            for item in order.items:
                table.add_row(
                    f"{item.quantity:g}",
                    item.name + (" [sub]" if item.substituted else ""),
                    item.brand or "",
                    item.category or "",
                    f"${item.total_price}" if item.total_price is not None else "",
                )
            console.print(table)
        else:
            console.print(title)
            console.print("")
    console.print(f"\n[green]{len(orders)} order(s).[/green]")


if __name__ == "__main__":
    app()
