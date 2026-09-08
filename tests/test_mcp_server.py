import asyncio

from app.mcp_server import _jsonable, mcp


def test_tools_registered() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "fd_session_status",
        "fd_history",
        "fd_backfill",
        "fd_spend",
        "fd_due",
        "fd_search",
        "fd_match",
        "fd_teach_match",
        "fd_profile",
        "fd_request",
        "fd_requests",
    }


def test_session_status_without_a_saved_session(tmp_path, monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setattr(
        "app.mcp_server.get_settings", lambda: Settings(data_dir=tmp_path)
    )
    result = asyncio.run(mcp.call_tool("fd_session_status", {}))
    payload = result[0].text
    assert '"session_saved": false' in payload
    assert "fdplanner login" in payload


def test_jsonable_converts_decimal_and_date() -> None:
    from datetime import date
    from decimal import Decimal

    assert _jsonable(Decimal("4.99")) == "4.99"
    assert _jsonable(date(2026, 1, 2)) == "2026-01-02"
    assert _jsonable({"a": [Decimal("1.00")]}) == {"a": ["1.00"]}
