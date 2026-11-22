"""Step 4 - accuracy checks run before a human ever sees the quote.

`block` = the quote cannot be approved until fixed.
`warn`  = the approver must look at it, but can still approve.
`info`  = context for the approver.

The most important one is RECOMPUTE: every line and total is calculated a
second time with exact fractions (`fractions.Fraction`), completely
independently of pricing.py, and the two results must match to the cent.
"""

from __future__ import annotations

from datetime import date, timedelta
from fractions import Fraction
from math import floor

from .config import Settings
from .models import CheckResult, Extraction, PricedQuote

LARGE_QTY = 500


def _round_half_up_cents(x: Fraction) -> Fraction:
    return Fraction(floor(x * 100 + Fraction(1, 2)), 100)


def independent_recompute(quote: PricedQuote, settings: Settings) -> list[str]:
    """Return a list of mismatch descriptions (empty list = everything adds up)."""
    problems: list[str] = []
    grand = Fraction(0)
    for line in quote.lines:
        margin = Fraction(str(settings.pricing.target_margin[line.category]))
        tax = Fraction(str(settings.tax.rates[line.category]))
        disc = Fraction(str(line.discount_rate))
        unit = _round_half_up_cents(Fraction(str(line.unit_cost)) / (1 - margin))
        subtotal = _round_half_up_cents(unit * line.quantity)
        net = subtotal - _round_half_up_cents(subtotal * disc)
        total = net + _round_half_up_cents(net * tax)
        grand += total
        for label, expected, actual in (
            ("unit price", unit, line.unit_price),
            ("net amount", net, line.net_amount),
            ("line total", total, line.line_total),
        ):
            if Fraction(str(actual)) != expected:
                problems.append(f"line {line.line_no} {label}: got {actual}, expected {float(expected):.2f}")
    if Fraction(str(quote.totals.grand_total)) != grand:
        problems.append(f"grand total: got {quote.totals.grand_total}, expected {float(grand):.2f}")
    return problems


def run_checks(
    quote: PricedQuote, extraction: Extraction, settings: Settings, today: date, valid_until: date
) -> list[CheckResult]:
    results: list[CheckResult] = []
    add = lambda code, sev, msg, line=None: results.append(  # noqa: E731
        CheckResult(code=code, severity=sev, message=msg, line_no=line)
    )

    if not quote.lines:
        add("NO_LINES", "block", "Nothing could be priced, so there is no quote to send.")

    for item in quote.unpriced:
        if item.reason.startswith("not in catalog"):
            add("MANUAL_SOURCING", "warn",
                f"'{item.requested_text}' is not in the catalog. Quote it separately after checking with suppliers.")
        else:
            add("CANNOT_PRICE", "block", f"'{item.requested_text}': {item.reason}")

    for problem in independent_recompute(quote, settings):
        add("RECOMPUTE_MISMATCH", "block", f"Independent recalculation disagrees: {problem}")

    floor_ = settings.pricing.margin_floor
    for line in quote.lines:
        if line.margin_pct < floor_:
            add("MARGIN_BELOW_FLOOR", "block",
                f"{line.name}: margin {line.margin_pct:.2%} is below the {floor_:.0%} floor.", line.line_no)
        if line.deadline_risk:
            add("DEADLINE_RISK", "warn",
                f"{line.name}: fastest valid supplier needs {line.lead_time_days} days, "
                f"client deadline is {extraction.deadline}.", line.line_no)
        if line.quantity > LARGE_QTY:
            add("LARGE_QUANTITY", "warn", f"{line.name}: quantity {line.quantity} is unusually large.", line.line_no)
        chosen = next((o for o in line.options_considered if o.status == "chosen"), None)
        if chosen and chosen.valid_until < valid_until:
            add("PRICE_EXPIRES_BEFORE_QUOTE", "warn",
                f"{line.name}: {chosen.supplier} price expires {chosen.valid_until}, before this quote "
                f"(valid until {valid_until}). Shorten validity or reconfirm the price.", line.line_no)
        elif chosen and chosen.valid_until <= today + timedelta(days=settings.checks.price_expiry_warning_days):
            add("PRICE_EXPIRES_SOON", "warn", f"{line.name}: supplier price expires {chosen.valid_until}.", line.line_no)
        expired = [o.supplier for o in line.options_considered if "expired" in o.status]
        if expired:
            add("EXPIRED_PRICE_SKIPPED", "info",
                f"{line.name}: ignored expired price list from {', '.join(expired)}.", line.line_no)

    if quote.totals.grand_total > settings.checks.manager_approval_threshold:
        add("MANAGER_APPROVAL", "warn",
            f"Total {quote.currency} {quote.totals.grand_total:,.2f} is above "
            f"{settings.checks.manager_approval_threshold:,.0f}: needs a second approver.")
    if extraction.deadline is None:
        add("NO_DEADLINE", "info", "Client gave no deadline; standard lead times apply.")
    if not extraction.client.email:
        add("NO_CLIENT_EMAIL", "warn", "No client email found; CRM contact cannot be matched.")
    return results
