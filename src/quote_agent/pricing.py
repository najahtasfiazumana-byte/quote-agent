"""Step 3 - supplier selection and quote maths. No LLM anywhere in this file.

All money is `decimal.Decimal`, rounded to 2 decimal places with
ROUND_HALF_UP at every step a human would round (unit price, discount,
tax), so the PDF always adds up exactly.

    unit_price      = unit_cost / (1 - target_margin)          (margin on sell price)
    line_subtotal   = unit_price * quantity
    discount_amount = line_subtotal * volume_discount_rate
    net_amount      = line_subtotal - discount_amount
    tax_amount      = net_amount * tax_rate
    line_total      = net_amount + tax_amount
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from .catalog import Catalog, Product, SupplierPrice
from .config import Settings
from .models import (
    Extraction,
    PricedLine,
    PricedQuote,
    QuoteTotals,
    SupplierOption,
    UnpricedItem,
)

CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def pct(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Supplier selection
# ---------------------------------------------------------------------------


def choose_supplier(
    offers: list[SupplierPrice], quantity: int, today: date, deadline: Optional[date]
) -> tuple[Optional[SupplierPrice], bool, list[SupplierOption]]:
    """Pick the cheapest valid offer that can meet the deadline.

    Returns (chosen_offer, deadline_risk, options_considered).
    If nobody can meet the deadline, the cheapest valid offer is chosen and
    deadline_risk=True so the approver sees it.
    """
    days_available = (deadline - today).days if deadline else None
    options: list[SupplierOption] = []
    valid: list[SupplierPrice] = []

    for offer in offers:
        reason = None
        if offer.valid_until < today:
            reason = f"price list expired {offer.valid_until.isoformat()}"
        elif quantity < offer.min_qty:
            reason = f"minimum order {offer.min_qty}"
        elif offer.stock is not None and offer.stock < quantity:
            reason = f"only {offer.stock} in stock"
        if reason is None:
            valid.append(offer)
        options.append(
            SupplierOption(
                supplier=offer.supplier,
                unit_cost=offer.unit_cost,
                lead_time_days=offer.lead_time_days,
                stock=offer.stock,
                valid_until=offer.valid_until,
                status=f"excluded: {reason}" if reason else "eligible",
            )
        )

    if not valid:
        return None, False, options

    on_time = [o for o in valid if days_available is None or o.lead_time_days <= days_available]
    pool = on_time or valid
    chosen = min(pool, key=lambda o: (o.unit_cost, o.lead_time_days, o.supplier))
    deadline_risk = not on_time

    for opt in options:
        if opt.supplier == chosen.supplier and opt.status == "eligible":
            opt.status = "chosen"
        elif opt.status == "eligible" and days_available is not None and opt.lead_time_days > days_available:
            opt.status = f"eligible (lead time {opt.lead_time_days}d > {days_available}d to deadline)"
    return chosen, deadline_risk, options


# ---------------------------------------------------------------------------
# Line and quote maths
# ---------------------------------------------------------------------------


def volume_discount_rate(quantity: int, settings: Settings) -> Decimal:
    tiers = sorted(settings.pricing.volume_discounts, key=lambda t: t.min_qty, reverse=True)
    for tier in tiers:
        if quantity >= tier.min_qty:
            return tier.rate
    return ZERO


def price_line(
    line_no: int,
    product: Product,
    billing_term: str,
    quantity: int,
    offer: SupplierPrice,
    settings: Settings,
    deadline_risk: bool = False,
    options: Optional[list[SupplierOption]] = None,
) -> PricedLine:
    margin = settings.pricing.target_margin[product.category]
    tax_rate = settings.tax.rates[product.category]
    disc_rate = volume_discount_rate(quantity, settings)

    unit_price = money(offer.unit_cost / (Decimal(1) - margin))
    line_subtotal = money(unit_price * quantity)
    discount_amount = money(line_subtotal * disc_rate)
    net_amount = line_subtotal - discount_amount
    tax_amount = money(net_amount * tax_rate)
    line_total = net_amount + tax_amount
    cost_total = money(offer.unit_cost * quantity)
    margin_amount = net_amount - cost_total
    margin_pct = pct(margin_amount / net_amount) if net_amount else ZERO

    return PricedLine(
        line_no=line_no,
        sku=product.sku,
        name=product.name,
        category=product.category,
        billing_term=billing_term,
        unit_label=product.unit_label(billing_term),
        quantity=quantity,
        supplier=offer.supplier,
        unit_cost=offer.unit_cost,
        lead_time_days=offer.lead_time_days,
        unit_price=unit_price,
        line_subtotal=line_subtotal,
        discount_rate=disc_rate,
        discount_amount=discount_amount,
        net_amount=net_amount,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        line_total=line_total,
        cost_total=cost_total,
        margin_amount=margin_amount,
        margin_pct=margin_pct,
        deadline_risk=deadline_risk,
        options_considered=options or [],
    )


def sum_totals(lines: list[PricedLine]) -> QuoteTotals:
    subtotal = sum((l.line_subtotal for l in lines), ZERO)
    discount_total = sum((l.discount_amount for l in lines), ZERO)
    net_total = sum((l.net_amount for l in lines), ZERO)
    tax_total = sum((l.tax_amount for l in lines), ZERO)
    cost_total = sum((l.cost_total for l in lines), ZERO)
    margin_total = net_total - cost_total
    return QuoteTotals(
        subtotal=subtotal,
        discount_total=discount_total,
        net_total=net_total,
        tax_total=tax_total,
        grand_total=net_total + tax_total,
        cost_total=cost_total,
        margin_total=margin_total,
        margin_pct=pct(margin_total / net_total) if net_total else ZERO,
    )


def build_quote(extraction: Extraction, catalog: Catalog, settings: Settings, today: date) -> PricedQuote:
    lines: list[PricedLine] = []
    unpriced: list[UnpricedItem] = []
    for item in extraction.items:
        product = catalog.get(item.sku)
        if product is None:
            unpriced.append(UnpricedItem(requested_text=item.requested_text, reason="not in catalog - source manually"))
            continue
        if not item.quantity or not item.billing_term:
            unpriced.append(UnpricedItem(requested_text=item.requested_text, reason="missing quantity or billing term"))
            continue
        offers = catalog.prices_for(product.sku, item.billing_term)
        chosen, risk, options = choose_supplier(offers, item.quantity, today, extraction.deadline)
        if chosen is None:
            why = "; ".join(f"{o.supplier}: {o.status}" for o in options) or "no supplier lists this product/term"
            unpriced.append(UnpricedItem(requested_text=f"{product.name} x{item.quantity}", reason=f"no valid offer ({why})"))
            continue
        lines.append(
            price_line(len(lines) + 1, product, item.billing_term, item.quantity, chosen, settings, risk, options)
        )
    return PricedQuote(lines=lines, totals=sum_totals(lines), unpriced=unpriced, currency=settings.currency)
