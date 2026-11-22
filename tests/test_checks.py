from datetime import date, timedelta
from decimal import Decimal

from conftest import TODAY

from quote_agent.checks import independent_recompute, run_checks
from quote_agent.models import Client, Extraction, RequestedItem
from quote_agent.pricing import build_quote


def make(settings, catalog, items, deadline=None, email="a@b.example"):
    ex = Extraction(client=Client(email=email), items=items, deadline=deadline)
    q = build_quote(ex, catalog, settings, TODAY)
    return ex, q


def item(sku, qty, term, family=None):
    return RequestedItem(requested_text=sku, sku=sku, family=family, quantity=qty, billing_term=term)


def codes(results):
    return {r.code: r.severity for r in results}


def test_clean_quote_has_no_blocks(settings, catalog):
    ex, q = make(settings, catalog, [item("VISIO-P1", 3, "annual")])
    results = run_checks(q, ex, settings, TODAY, TODAY + timedelta(days=14))
    assert not [r for r in results if r.severity == "block"]
    assert independent_recompute(q, settings) == []


def test_recompute_catches_a_tampered_total(settings, catalog):
    ex, q = make(settings, catalog, [item("VISIO-P1", 3, "annual")])
    q.lines[0].line_total += Decimal("0.01")
    results = run_checks(q, ex, settings, TODAY, TODAY + timedelta(days=14))
    assert codes(results)["RECOMPUTE_MISMATCH"] == "block"


def test_margin_floor_blocks(settings, catalog):
    settings.pricing.target_margin["software"] = Decimal("0.02")
    ex, q = make(settings, catalog, [item("VISIO-P1", 3, "annual")])
    assert codes(run_checks(q, ex, settings, TODAY, TODAY))["MARGIN_BELOW_FLOOR"] == "block"


def test_deadline_risk_and_manager_approval(settings, catalog):
    ex, q = make(settings, catalog, [item("LAP-LEN-T14", 30, "one_time")], deadline=TODAY + timedelta(days=3))
    c = codes(run_checks(q, ex, settings, TODAY, TODAY + timedelta(days=14)))
    assert c["DEADLINE_RISK"] == "warn"
    assert c["MANAGER_APPROVAL"] == "warn"


def test_out_of_stock_everywhere_blocks(settings, catalog):
    ex, q = make(settings, catalog, [item("LAP-DELL-7450", 500, "one_time")])
    assert codes(run_checks(q, ex, settings, TODAY, TODAY))["CANNOT_PRICE"] == "block"


def test_unknown_product_is_a_warning_not_a_block(settings, catalog):
    ex, q = make(settings, catalog, [item("VISIO-P1", 3, "annual"),
                                     RequestedItem(requested_text="Autodesk AutoCAD", quantity=8)])
    assert codes(run_checks(q, ex, settings, TODAY, TODAY))["MANUAL_SOURCING"] == "warn"


def test_supplier_price_expiring_before_quote_validity(settings, catalog):
    late_today = date(2027, 12, 25)  # prices in the sample data expire 2027-12-31
    ex = Extraction(client=Client(email="a@b.example"), items=[item("VISIO-P1", 3, "annual")])
    q = build_quote(ex, catalog, settings, late_today)
    c = codes(run_checks(q, ex, settings, late_today, late_today + timedelta(days=14)))
    assert c["PRICE_EXPIRES_BEFORE_QUOTE"] == "warn"


def test_missing_email_and_deadline(settings, catalog):
    ex, q = make(settings, catalog, [item("VISIO-P1", 3, "annual")], email=None)
    c = codes(run_checks(q, ex, settings, TODAY, TODAY))
    assert c["NO_CLIENT_EMAIL"] == "warn" and c["NO_DEADLINE"] == "info"
