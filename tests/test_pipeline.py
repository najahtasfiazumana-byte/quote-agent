import json

import pytest
from conftest import TODAY, ScriptedLLM
from pypdf import PdfReader

from quote_agent.models import Extraction
from quote_agent.pipeline import ApprovalError, approve_quote, continue_with_reply, load_state, start_quote
from quote_agent.writer import Writer, numbers_are_safe


def run(text, settings, catalog, llm, tmp_path, quote_no="Q-TEST-0001"):
    return start_quote(text, settings=settings, catalog=catalog, llm=llm, out_root=tmp_path,
                       today=TODAY, quote_no=quote_no)


def test_happy_path_end_to_end(sample, settings, catalog, mock_llm, tmp_path):
    state, run_dir = run(sample("01_clear_request.txt"), settings, catalog, mock_llm, tmp_path)
    assert state.status == "PENDING_APPROVAL"
    for name in ("request.txt", "extraction.json", "state.json", "review.md", "internal_costing.csv",
                 "quote_DRAFT.pdf", "audit.jsonl"):
        assert (run_dir / name).exists(), name

    approved = approve_quote(run_dir, "Najah", settings=settings, llm=mock_llm, crm_dry_run=True)
    assert approved.status == "APPROVED" and approved.approved_by == "Najah"
    pdf_text = "".join(p.extract_text() for p in PdfReader(run_dir / "Q-TEST-0001.pdf").pages)
    assert f"{approved.quote.totals.grand_total:,.2f}" in pdf_text
    assert "Puncak" not in pdf_text and "Tropika" not in pdf_text  # supplier names stay internal
    assert f"{approved.quote.totals.grand_total:,.2f}" in (run_dir / "quote_email.txt").read_text()

    calls = json.loads((run_dir / "hubspot_requests.json").read_text())
    urls = [c["url"].split("hubapi.com")[1] for c in calls]
    assert urls.count("/crm/v3/objects/line_items") == 3
    assert "/crm/v3/objects/deals" in urls and "/files/v3/files" in urls and "/crm/v3/objects/notes" in urls
    deal = next(c for c in calls if c["url"].endswith("/deals"))
    assert deal["body"]["properties"]["amount"] == str(approved.quote.totals.grand_total)
    assert deal["body"]["associations"][0]["types"][0]["associationTypeId"] == 3
    note = next(c for c in calls if c["url"].endswith("/notes"))
    assert note["body"]["associations"][0]["types"][0]["associationTypeId"] == 214


def test_cannot_approve_twice_or_while_clarifying(sample, settings, catalog, mock_llm, tmp_path):
    state, run_dir = run(sample("02_messy_request.txt"), settings, catalog, mock_llm, tmp_path)
    assert state.status == "NEEDS_CLARIFICATION"
    assert (run_dir / "clarification_email.txt").exists()
    assert not (run_dir / "quote_DRAFT.pdf").exists()
    with pytest.raises(ApprovalError):
        approve_quote(run_dir, "Najah", settings=settings, llm=mock_llm, crm_dry_run=True)


def test_reply_continues_same_quote_number(sample, settings, catalog, mock_llm, tmp_path):
    _, run_dir = run(sample("02_messy_request.txt"), settings, catalog, mock_llm, tmp_path)
    state, run_dir2 = continue_with_reply(run_dir, sample("02_followup_reply.txt"), settings=settings,
                                          catalog=catalog, llm=mock_llm, today=TODAY)
    assert run_dir2 == run_dir and state.quote_no == "Q-TEST-0001"
    assert state.status == "PENDING_APPROVAL" and len(state.quote.lines) == 3
    assert "client reply" in load_state(run_dir).request_text


def test_blocked_quote_cannot_be_approved(settings, catalog, mock_llm, tmp_path):
    text = "From: A <a@x.example>\n\nPlease quote 500 units Dell Latitude 7450.\n"
    state, run_dir = run(text, settings, catalog, mock_llm, tmp_path)
    assert state.status == "BLOCKED"
    with pytest.raises(ApprovalError):
        approve_quote(run_dir, "Najah", settings=settings, llm=mock_llm, crm_dry_run=True)


def test_bahasa_request_gets_bahasa_email(sample, settings, catalog, mock_llm, tmp_path):
    _, run_dir = run(sample("05_bahasa_mix.txt"), settings, catalog, mock_llm, tmp_path)
    approve_quote(run_dir, "Najah", settings=settings, llm=mock_llm, crm_dry_run=True)
    assert (run_dir / "quote_email.txt").read_text().startswith("Salam Nurul")


# -- the writer may polish wording but never invent numbers ---------------------

def test_numbers_guard():
    facts = "Quote Q-1 total MYR 1,234.50 valid until 05 Dec 2026"
    assert numbers_are_safe("Your quote Q-1 is MYR 1,234.50, valid to 05 Dec 2026.", facts)
    assert not numbers_are_safe("Your quote is MYR 1,200.00 with a 5% discount.", facts)
    assert not numbers_are_safe("Valid until 05 Dec, 2026% cheaper", facts)  # % is a different fact


def test_writer_falls_back_when_llm_invents_a_number(settings):
    ex = Extraction.model_validate({"client": {"name": "Aisyah Rahman"}, "items": []})
    from quote_agent.models import Clarification
    qs = [Clarification(field="billing_term", question="Annual or monthly?")]
    w = Writer(ScriptedLLM(["Hi Aisyah, annual saves you 20%! Annual or monthly?"]), settings)
    out = w.clarification_email(ex, qs)
    assert "20%" not in out and out.startswith("Hi Aisyah")
    assert "not in the facts" in w.last_fallback_reason

    w2 = Writer(ScriptedLLM(["Hi Aisyah, would you prefer annual or monthly billing? Thanks!"]), settings)
    assert w2.clarification_email(ex, qs).startswith("Hi Aisyah, would you prefer")
    assert w2.last_fallback_reason is None


def test_llm_provider_path_end_to_end(settings, catalog, tmp_path):
    """Same pipeline, but extraction and emails come from an (scripted) LLM instead of the offline parser."""
    extraction = {
        "client": {"name": "Daniel Wong", "email": "daniel@x.example", "company": "Sunway Pixel Studio"},
        "items": [
            {"requested_text": "Business Standard", "family": "microsoft_365", "sku": "M365-BS",
             "quantity": 25, "billing_term": "annual", "notes": None},
            {"requested_text": "Visio", "family": "visio", "sku": "VISIO-P9",  # invented SKU
             "quantity": 3, "billing_term": "annual", "notes": None},
        ],
        "deadline": "2026-11-30", "deadline_text": "end of month", "language": "en",
    }
    llm = ScriptedLLM([json.dumps(extraction), "Hi Daniel, which Visio plan would you like: Plan 1 or Plan 2?"])
    state, run_dir = run("any text", settings, catalog, llm, tmp_path)
    assert state.status == "NEEDS_CLARIFICATION"  # the invented SKU was dropped, so it asks
    assert state.extraction.items[1].sku is None and "unknown SKU" in state.extraction.items[1].notes
    assert (run_dir / "clarification_email.txt").read_text().startswith("Hi Daniel, which Visio plan")
    assert state.provider == "scripted:test"
