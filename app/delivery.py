"""Saved FreshDirect delivery addresses (read-only), cached to disk.

Addresses change rarely and fetching them drives the live browser, so we cache
the list to ``data/addresses.json`` and let the dashboard read that. Delivery
*timeslots* are perishable and reserved interactively at checkout, so the planner
records a preferred date + tip rather than holding a live slot.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from app.config import Settings, get_settings

_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def next_preferred_delivery(
    after: date | None = None, day: str = "sun", hour: int = 19
) -> datetime:
    """The next ``day`` on or after ``after`` (default today), at ``hour``:00.

    Used to suggest an "ideal" delivery slot on a fresh draft, e.g. Sunday
    evening after 7pm. The household manager confirms the actual slot at checkout.
    """
    after = after or date.today()
    target = _DOW[day.lower()[:3]]
    delta = (target - after.weekday()) % 7  # 0 if `after` is already that weekday
    return datetime.combine(after + timedelta(days=delta), time(hour, 0))


@dataclass
class SavedAddress:
    id: str
    address1: str | None
    apartment: str | None
    city: str | None
    state: str | None
    zip_code: str | None
    selected: bool = False

    def one_line(self) -> str:
        parts = [self.address1, self.apartment, self.city, self.state, self.zip_code]
        return ", ".join(p for p in parts if p)


def _cache_path(settings: Settings):
    return settings.data_dir / "addresses.json"


def fetch_addresses(settings: Settings | None = None, headed: bool = False) -> list[SavedAddress]:
    """Read saved addresses from the live account and cache them."""
    settings = settings or get_settings()
    from app.freshdirect.client import FreshDirectClient

    client = FreshDirectClient(settings, headed=headed)
    with client.session() as fd:
        # The checkout page populates userDeliveryAddresses.
        fd._goto(f"{settings.fd_base_url}/checkout")  # noqa: SLF001 - same package intent
        uda = fd._wait_for("userDeliveryAddresses")  # noqa: SLF001

    addresses = _parse_addresses(uda or {})
    settings.ensure_dirs()
    _cache_path(settings).write_text(
        json.dumps([asdict(a) for a in addresses], indent=2), encoding="utf-8"
    )
    return addresses


def load_addresses(settings: Settings | None = None) -> list[SavedAddress]:
    """Return cached addresses (empty list if not fetched yet)."""
    settings = settings or get_settings()
    path = _cache_path(settings)
    if not path.exists():
        return []
    return [SavedAddress(**d) for d in json.loads(path.read_text())]


def _parse_addresses(uda: dict) -> list[SavedAddress]:
    selected_id = (((uda.get("selectedAddress") or {}).get("address") or {}).get("id"))
    entries = (
        (uda.get("homeAddresses") or [])
        + (uda.get("corpAddresses") or [])
        + (uda.get("pickUpDepotsAddresses") or [])
    )
    out: list[SavedAddress] = []
    for entry in entries:
        addr = entry.get("address") or {}
        aid = addr.get("id")
        if not aid:
            continue
        out.append(
            SavedAddress(
                id=str(aid),
                address1=addr.get("address1"),
                apartment=addr.get("apartment"),
                city=addr.get("city"),
                state=addr.get("state"),
                zip_code=addr.get("zipCode"),
                selected=(str(aid) == str(selected_id)),
            )
        )
    return out
