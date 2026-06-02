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
