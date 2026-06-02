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
from app.freshdirect.history import fetch_order_history, parse_pasted_history
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
