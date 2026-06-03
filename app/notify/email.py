"""The weekly email digest: a summary of the draft cart + a link to review.

``render_digest`` is pure (plan in, subject/text/html out) so it's easy to test.
``send_digest`` sends it over SMTP — and if SMTP isn't configured, it falls back
to writing the HTML under ``data/digests/`` and returns its path, so the
autonomous weekly run never hard-fails just because email isn't set up yet.
"""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from app.config import Settings, get_settings
from app.models import PlanRow
from app.money import dollars


@dataclass
class DigestResult:
    sent: bool
    """True if an email actually went out over SMTP."""
    detail: str
    """Human-readable outcome (recipient, or the preview path, or why skipped)."""
    preview_path: str | None = None


def _plan_url(plan: PlanRow, settings: Settings) -> str:
    return f"{settings.dashboard_url.rstrip('/')}/plan/{plan.id}"


def _delivery_line(plan: PlanRow) -> str | None:
    bits = []
    when = plan.delivery_when()
    if when:
        bits.append(when)
    if plan.address1:
        bits.append(plan.address_one_line())
    if plan.tip_cents:
        bits.append(f"tip {dollars(plan.tip_cents)}")
    return " · ".join(bits) or None


def render_digest(plan: PlanRow, settings: Settings | None = None) -> tuple[str, str, str]:
    """Return (subject, plain_text, html) for a plan's review digest."""
    settings = settings or get_settings()
    url = _plan_url(plan, settings)

    included = [ln for ln in plan.lines if ln.included]
    subtotal = sum(ln.line_cents for ln in included)
    review = [ln for ln in included if ln.needs_review]
    delivery = _delivery_line(plan)

    cap = plan.budget_cap_cents
    if cap:
        over = subtotal - cap
        budget = (
            f"{dollars(subtotal)} of {dollars(cap)} — over by {dollars(over)}"
            if over > 0
            else f"{dollars(subtotal)} of {dollars(cap)} — under budget"
        )
    else:
        budget = f"{dollars(subtotal)} (no budget cap)"

    subject = (
        f"Your FreshDirect draft — week of {plan.week_of} · "
        f"{len(included)} items · {dollars(subtotal)}"
    )
    if not included:
        subject = f"FreshDirect: nothing due — week of {plan.week_of}"

    # --- plain text ---
    lines = [
        f"Draft cart for the week of {plan.week_of}",
        "",
        f"Items : {len(included)}",
        f"Total : {budget}",
    ]
    if delivery:
        lines.append(f"Deliver: {delivery}")
    if review:
        lines.append(f"Review : {len(review)} line(s) flagged — a match looked uncertain")
    lines.append("")
    if included:
        lines.append("Cart:")
        for ln in included:
            flag = "  [review]" if ln.needs_review else ""
            qty = f"{ln.quantity:g}× " if ln.quantity and ln.quantity != 1 else ""
            name = ln.selected_name or ln.need
            lines.append(f"  • {qty}{name} — {dollars(ln.line_cents)}{flag}")
    else:
        lines.append("Nothing is due for restock this week.")
    lines += ["", f"Review, edit, and approve: {url}", ""]
    text = "\n".join(lines)

    # --- html ---
    rows = ""
    for ln in included:
        qty = f"{ln.quantity:g}× " if ln.quantity and ln.quantity != 1 else ""
        brand = f'<span style="color:#888">{ln.selected_brand}</span> ' if ln.selected_brand else ""
        flag = (
            ' <span style="background:#fde68a;color:#7c5b00;border-radius:3px;'
            'padding:1px 5px;font-size:11px">review</span>'
            if ln.needs_review
            else ""
        )
        rows += (
            f'<tr><td style="padding:4px 0">{qty}{brand}{ln.selected_name or ln.need}{flag}</td>'
            f'<td style="padding:4px 0;text-align:right;white-space:nowrap">{dollars(ln.line_cents)}</td></tr>'
        )
    cart_html = (
        f'<table style="width:100%;border-collapse:collapse">{rows}</table>'
        if included
        else '<p style="color:#666">Nothing is due for restock this week.</p>'
    )
    deliver_html = f'<p style="color:#444;margin:4px 0">🗓 {delivery}</p>' if delivery else ""
    review_html = (
        f'<p style="color:#7c5b00;margin:4px 0">⚠️ {len(review)} line(s) flagged for review.</p>'
        if review
        else ""
    )
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;margin:0 auto;color:#222">
  <h2 style="margin-bottom:2px">Your FreshDirect draft</h2>
  <p style="color:#666;margin-top:0">Week of {plan.week_of}</p>
  <p style="font-size:18px;margin:8px 0"><strong>{budget}</strong> · {len(included)} items</p>
  {deliver_html}
  {review_html}
  <div style="border:1px solid #eee;border-radius:8px;padding:12px 16px;margin:12px 0">
    {cart_html}
  </div>
  <a href="{url}" style="display:inline-block;background:#137333;color:#fff;
     text-decoration:none;padding:10px 18px;border-radius:6px;font-weight:600">
     Review &amp; approve →</a>
  <p style="color:#999;font-size:12px;margin-top:18px">
     Nothing is ordered until you approve and check out yourself.</p>
</div>"""
    return subject, text, html


def send_digest(plan: PlanRow, settings: Settings | None = None) -> DigestResult:
    """Email the digest, or write an HTML preview if SMTP isn't configured."""
    settings = settings or get_settings()
    subject, text, html = render_digest(plan, settings)

    if not settings.smtp_configured:
        settings.digests_dir.mkdir(parents=True, exist_ok=True)
        path = settings.digests_dir / f"plan-{plan.id}.html"
        path.write_text(html, encoding="utf-8")
        return DigestResult(
            sent=False,
            detail=f"SMTP not configured — wrote preview to {path}",
            preview_path=str(path),
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.digest_from or settings.smtp_user or settings.digest_to
    msg["To"] = settings.digest_to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)

    return DigestResult(sent=True, detail=f"Sent digest to {settings.digest_to}")
