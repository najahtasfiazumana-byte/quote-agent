from datetime import date
from decimal import Decimal

from conftest import TODAY

from quote_agent.catalog import SupplierPrice
from quote_agent.models import Extraction, RequestedItem
from quote_agent.pricing import build_quote, choose_supplier, money, price_line, volume_discount_rate

D = Decimal


def offer(supplier, cost, stock=None, lead=1, min_qty=1, valid=date(2027, 12, 31)):
    return SupplierPrice(supplier, "X", "annual", D(cost), stock, lead, min_qty, valid)


def test_money_rounds_half_up():
    assert money(D("1.005")) == D("1.01")
    assert money(D("2.675")) == D("2.68")  # a binary float would give 2.67
    assert money(D("1.004")) == D("1.00")


def test_m365_line_worked_example(settings, catalog):
    """Hand-checked: 552 / 0.88 = 627.2727 -> 627.27; x25 = 15,681.75;
    1.5% discount = 235.23; net 15,446.52; SST 8% = 1,235.72; total 16,682.24."""
    product = catalog.get("M365-BS")
    line = price_line(1, product, "annual", 25, offer("A", "552.00"), settings)
    assert line.unit_price == D("627.27")
    assert line.line_subtotal == D("15681.75")
    assert line.discount_rate == D("0.015")
    assert line.discount_amount == D("235.23")
    assert line.net_amount == D("15446.52")
    assert line.tax_amount == D("1235.72")
    assert line.line_total == D("16682.24")
    assert line.cost_total == D("13800.00")
    assert line.margin_amount == D("1646.52")


def test_margin_is_on_sell_price_not_markup(settings, catalog):
    line = price_line(1, catalog.get("VISIO-P1"), "annual", 1, offer("A", "88.00"), settings)
    assert line.unit_price == D("100.00")  # 12% margin: 88 / 0.88
    assert line.margin_pct == D("0.1200")


def test_volume_discount_tiers(settings):
    assert volume_discount_rate(19, settings) == D("0")
    assert volume_discount_rate(20, settings) == D("0.015")
    assert volume_discount_rate(49, settings) == D("0.015")
    assert volume_discount_rate(50, settings) == D("0.03")


def test_supplier_cheapest_valid_wins():
    offers = [offer("Cheap but expired", "90", valid=date(2026, 1, 1)), offer("B", "100"), offer("C", "95")]
    chosen, risk, opts = choose_supplier(offers, 5, TODAY, None)
    assert chosen.supplier == "C" and not risk
    statuses = {o.supplier: o.status for o in opts}
    assert statuses["Cheap but expired"].startswith("excluded: price list expired")
    assert statuses["C"] == "chosen"


def test_supplier_min_qty_and_stock_exclusions():
    offers = [offer("MinQty", "90", min_qty=10), offer("LowStock", "91", stock=3), offer("OK", "99", stock=50)]
    chosen, _, opts = choose_supplier(offers, 5, TODAY, None)
    assert chosen.supplier == "OK"
    assert {o.supplier: o.status for o in opts}["MinQty"] == "excluded: minimum order 10"
    assert {o.supplier: o.status for o in opts}["LowStock"] == "excluded: only 3 in stock"


def test_supplier_prefers_on_time_over_cheapest():
    offers = [offer("Cheap slow", "90", lead=10), offer("Fast", "95", lead=2)]
    chosen, risk, _ = choose_supplier(offers, 1, TODAY, date(2026, 11, 25))
    assert chosen.supplier == "Fast" and not risk


def test_supplier_deadline_risk_when_nobody_is_on_time():
    offers = [offer("Slow", "90", lead=10), offer("Slower", "85", lead=14)]
    chosen, risk, _ = choose_supplier(offers, 1, TODAY, date(2026, 11, 24))
    assert chosen.supplier == "Slower" and risk  # cheapest valid, flagged


def test_no_valid_supplier_returns_none():
    chosen, _, _ = choose_supplier([offer("X", "1", stock=0)], 1, TODAY, None)
    assert chosen is None


def test_build_quote_totals_add_up(settings, catalog):
    ex = Extraction(items=[
        RequestedItem(requested_text="m365", family="microsoft_365", sku="M365-BS", quantity=25, billing_term="annual"),
        RequestedItem(requested_text="laptop", family="laptop", sku="LAP-DELL-5450", quantity=5, billing_term="one_time"),
        RequestedItem(requested_text="AutoCAD", quantity=2),
    ])
    q = build_quote(ex, catalog, settings, TODAY)
    assert len(q.lines) == 2
    assert q.unpriced[0].requested_text == "AutoCAD"
    t = q.totals
    assert t.grand_total == sum(l.line_total for l in q.lines)
    assert t.net_total == t.subtotal - t.discount_total
    assert t.grand_total == t.net_total + t.tax_total


def test_expired_supplier_row_is_skipped_in_real_data(settings, catalog):
    ex = Extraction(items=[RequestedItem(requested_text="x", sku="M365-BS", family="microsoft_365",
                                         quantity=25, billing_term="annual")])
    line = build_quote(ex, catalog, settings, TODAY).lines[0]
    assert line.supplier == "Puncak IT Supply"  # Lautan is cheaper but its annual price expired
    assert line.unit_cost == D("552.00")
