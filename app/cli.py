"""Phase 0 CLI: capture a session and verify the order-history pipeline.

    fdplanner login                 # one-time interactive login (headed)
    fdplanner history --limit 5     # scrape + print recent orders
    fdplanner import-paste FILE     # parse a pasted export instead of scraping
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.freshdirect.base import Order, SessionExpired
from app.freshdirect.history import parse_pasted_history, scrape_order_history
from app.freshdirect.session import capture_session

app = typer.Typer(add_completion=False, help="FreshDirect Weekly Planner (Phase 0)")
console = Console()


@app.command()
def login() -> None:
    """Open a browser, log into FreshDirect, and save the session (encrypted)."""
    capture_session(get_settings())


@app.command()
def history(limit: int = typer.Option(10, help="How many recent orders to show")) -> None:
    """Scrape recent orders from the saved session and print them."""
    try:
        orders = scrape_order_history(get_settings(), limit=limit)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    _render(orders)


@app.command("debug-dump")
def debug_dump(
    url: str = typer.Option(None, help="Override the orders-page URL to capture"),
) -> None:
    """Dev tool: capture the live orders page (HTML + screenshot) for selector tuning."""
    from app.freshdirect.debug import dump_orders_page

    try:
        result = dump_orders_page(get_settings(), url=url)
    except SessionExpired as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"Final URL : {result.final_url}")
    console.print(f"Title     : {result.title}")
    console.print(f"HTML      : {result.html_path}")
    console.print(f"Screenshot: {result.screenshot_path}")
    if result.looks_logged_out:
        console.print("[yellow]Page looks logged-out — session may be expired.[/yellow]")
    console.print("\nSelector hit counts:")
    for name, count in result.selector_hits.items():
        colour = "green" if count else "red"
        console.print(f"  [{colour}]{count:>4}[/{colour}]  {name}")


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
        table = Table(
            title=f"Order #{order.order_id} — {order.ordered_on} "
            f"({f'${order.total}' if order.total is not None else 'total n/a'})",
            title_justify="left",
            show_lines=False,
        )
        table.add_column("Qty", justify="right")
        table.add_column("Item")
        table.add_column("Unit", justify="right")
        table.add_column("Line", justify="right")
        for item in order.items:
            table.add_row(
                f"{item.quantity:g}",
                item.name,
                f"${item.unit_price}" if item.unit_price is not None else "",
                f"${item.total_price}" if item.total_price is not None else "",
            )
        console.print(table)
    console.print(f"\n[green]{len(orders)} order(s).[/green]")


if __name__ == "__main__":
    app()
