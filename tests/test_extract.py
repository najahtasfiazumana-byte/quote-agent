import json
from datetime import date

import pytest
from conftest import TODAY, ScriptedLLM

from quote_agent.clarify import find_clarifications
from quote_agent.extract import LLMExtractor, RuleBasedExtractor, normalise
from quote_agent.llm import LLMError, parse_json_object
from quote_agent.models import Extraction, RequestedItem


def run_rules(text, catalog):
    return normalise(RuleBasedExtractor().extract(text, catalog, TODAY), catalog)


def by_sku(ex):
    return {i.sku: (i.quantity, i.billing_term) for i in ex.items}


# -- offline parser on the sample requests -------------------------------------

def test_clear_request(sample, catalog):
    ex = run_rules(sample("01_clear_request.txt"), catalog)
    assert by_sku(ex) == {"M365-BS": (25, "annual"), "VISIO-P1": (3, "annual"), "LAP-DELL-5450": (5, "one_time")}
    assert ex.deadline == date(2026, 12, 15)
    assert ex.client.email == "aisyah.rahman@kenanga-logistics.example"
    assert ex.client.company == "Kenanga Logistics Sdn Bhd"
    assert find_clarifications(ex, catalog) == []


def test_messy_request_needs_clarification(sample, catalog):
    ex = run_rules(sample("02_messy_request.txt"), catalog)
    assert ex.deadline == date(2026, 11, 30)  # "end of month"
    fields = [(c.item_index, c.field) for c in find_clarifications(ex, catalog)]
    assert ("sku" in {f for _, f in fields}) and ("billing_term" in {f for _, f in fields})
    assert {i.family for i in ex.items} == {"microsoft_365", "visio", "laptop"}
    assert {i.quantity for i in ex.items} == {25, 3, 5}


def test_followup_resolves_everything(sample, catalog):
    thread = sample("02_messy_request.txt") + "\n--- client reply ---\n" + sample("02_followup_reply.txt")
    ex = run_rules(thread, catalog)
    assert by_sku(ex) == {"M365-BS": (25, "annual"), "VISIO-P1": (3, "annual"), "LAP-DELL-5450": (5, "one_time")}
    assert ex.client.company == "Sunway Pixel Studio"
    assert find_clarifications(ex, catalog) == []


def test_rush_order(sample, catalog):
    ex = run_rules(sample("03_rush_order.txt"), catalog)
    assert by_sku(ex) == {"LAP-LEN-T14": (30, "one_time"), "DOCK-DELL-WD19S": (30, "one_time")}
    assert ex.deadline == date(2026, 11, 24)  # "within 3 days" - the 3 is not a quantity


def test_unknown_product_kept_for_manual_sourcing(sample, catalog):
    ex = run_rules(sample("04_unknown_product.txt"), catalog)
    unknown = [i for i in ex.items if i.sku is None]
    assert unknown[0].requested_text == "Autodesk AutoCAD" and unknown[0].quantity == 8
    assert by_sku(ex)["ZOOM-PRO"] == (8, "annual")


def test_bahasa_malaysia(sample, catalog):
    ex = run_rules(sample("05_bahasa_mix.txt"), catalog)
    assert ex.language == "ms"
    assert by_sku(ex) == {"GWS-BS": (12, "annual"), "LAP-MBA-M4": (2, "one_time")}  # "tahunan" = annual


# -- normalisation: the catalog, not the model, has the final say ---------------

def test_normalise_drops_invented_sku_and_fills_single_options(catalog):
    ex = Extraction(items=[
        RequestedItem(requested_text="M365 E5", family="microsoft_365", sku="M365-E5", quantity=3),
        RequestedItem(requested_text="docks", family="dock", quantity=2),
        RequestedItem(requested_text="adobe", sku="ADOBE-CCAA", quantity=1),
    ])
    out = normalise(ex, catalog).items
    assert out[0].sku is None and "unknown SKU" in out[0].notes
    assert out[1].sku == "DOCK-DELL-WD19S" and out[1].billing_term == "one_time"
    assert out[2].family == "adobe_cc" and out[2].billing_term == "annual"


# -- LLM extractor with a scripted model ---------------------------------------

GOOD = {
    "client": {"name": "A", "email": "a@x.example", "company": "X"},
    "items": [{"requested_text": "Visio P1", "family": "visio", "sku": "VISIO-P1", "quantity": 2,
               "billing_term": "annual", "notes": None}],
    "deadline": "2026-12-01", "deadline_text": "1 Dec", "language": "en",
}


def test_llm_extractor_accepts_fenced_json(catalog):
    llm = ScriptedLLM(["```json\n" + json.dumps(GOOD) + "\n```"])
    ex = LLMExtractor(llm).extract("text", catalog, TODAY)
    assert ex.items[0].sku == "VISIO-P1"
    assert "TODAY: 2026-11-21" in llm.calls[0] and "VISIO-P1 | Visio Plan 1" in llm.calls[0]


def test_llm_extractor_retries_with_validation_error(catalog):
    bad = dict(GOOD, items=[dict(GOOD["items"][0], quantity=-4)])
    llm = ScriptedLLM([json.dumps(bad), json.dumps(GOOD)])
    ex = LLMExtractor(llm).extract("text", catalog, TODAY)
    assert ex.items[0].quantity == 2
    assert "previous answer was invalid" in llm.calls[1]


def test_llm_extractor_gives_up_after_retry(catalog):
    with pytest.raises(LLMError):
        LLMExtractor(ScriptedLLM(["not json", "still not json"])).extract("text", catalog, TODAY)


def test_parse_json_object_with_chatter():
    assert parse_json_object('Sure! {"a": 1} hope that helps') == {"a": 1}
