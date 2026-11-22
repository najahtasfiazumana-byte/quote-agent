"""Orchestrates one quote from request to CRM, and writes every artefact to disk.

    request text
      -> extract (LLM or offline parser) -> normalise against catalog (code)
      -> clarifications?  --yes-->  clarification email draft, STOP (NEEDS_CLARIFICATION)
      -> price (Decimal) -> checks (incl. independent recompute)
      -> blocking issue?  --yes-->  BLOCKED (draft PDF + review for the human)
      -> PENDING_APPROVAL (draft PDF with watermark, internal costing)
      -> human approves  -> final PDF, cover email, HubSpot deal  (APPROVED)

Each run lives in outputs/<quote_no>/ with state.json, so a quote can be
approved later (`quote-agent approve`) or continued when the client replies
(`quote-agent reply`).
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .catalog import Catalog
from .checks import run_checks
from .clarify import find_clarifications
from .config import Settings
from .extract import get_extractor, normalise
from .hubspot import push_to_hubspot
from .llm import LLM
from .models import QuoteState
from .pdf import render_quote_pdf
from .pricing import build_quote
from .writer import Writer


class ApprovalError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_quote_no(settings: Settings, text: str, today: date) -> str:
    digest = hashlib.sha1(f"{text}{time.time_ns()}".encode()).hexdigest()[:4].upper()
    return f"{settings.quote.number_prefix}-{today:%Y%m%d}-{digest}"


def audit(run_dir: Path, step: str, **detail) -> None:
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "step": step, **detail}
    with open(run_dir / "audit.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


def save_state(state: QuoteState, run_dir: Path) -> None:
    (run_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_state(run_dir: Path) -> QuoteState:
    return QuoteState.model_validate_json((Path(run_dir) / "state.json").read_text(encoding="utf-8"))


def write_internal_costing(state: QuoteState, path: Path) -> None:
    """Internal-only sheet: suppliers, costs and margins. Never sent to the client."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["line", "sku", "product", "qty", "billing_term", "supplier", "unit_cost", "unit_price",
                    "net_amount", "tax", "line_total", "cost_total", "margin", "margin_pct", "lead_time_days",
                    "deadline_risk", "other_options"])
        for l in state.quote.lines:
            others = "; ".join(f"{o.supplier} {o.unit_cost} ({o.status})" for o in l.options_considered if o.status != "chosen")
            w.writerow([l.line_no, l.sku, l.name, l.quantity, l.billing_term, l.supplier, l.unit_cost, l.unit_price,
                        l.net_amount, l.tax_amount, l.line_total, l.cost_total, l.margin_amount,
                        f"{l.margin_pct:.2%}", l.lead_time_days, l.deadline_risk, others])
        t = state.quote.totals
        w.writerow([])
        w.writerow(["TOTAL", "", "", "", "", "", "", "", t.net_total, t.tax_total, t.grand_total, t.cost_total,
                    t.margin_total, f"{t.margin_pct:.2%}"])


def review_markdown(state: QuoteState) -> str:
    """Human-readable summary for the approver (also printed by the CLI)."""
    ex = state.extraction
    out = [f"# {state.quote_no} - {state.status}", ""]
    out.append(f"**Client:** {ex.client.name or '?'} ({ex.client.email or 'no email'}), {ex.client.company or '?'}  ")
    out.append(f"**Deadline:** {ex.deadline or 'not given'}{f' ({ex.deadline_text})' if ex.deadline_text else ''}  ")
    out.append(f"**Language:** {ex.language} | **Provider:** {state.provider}")
    out += ["", "## What the client asked for", "", "| # | Client wording | SKU | Qty | Term |", "|---|---|---|---|---|"]
    for i, it in enumerate(ex.items, 1):
        out.append(f"| {i} | {it.requested_text} | {it.sku or '-'} | {it.quantity or '?'} | {it.billing_term or '?'} |")
    if state.clarifications:
        out += ["", "## Questions for the client", ""]
        out += [f"- {c.question}" + (f" ({' / '.join(c.options)})" if c.options else "") for c in state.clarifications]
    if state.quote:
        q, cur = state.quote, state.quote.currency
        out += ["", "## Pricing (internal view)", "",
                "| # | Product | Qty | Supplier | Unit cost | Unit price | Disc | Net | Margin |",
                "|---|---|---|---|---|---|---|---|---|"]
        for l in q.lines:
            out.append(f"| {l.line_no} | {l.name} ({l.billing_term}) | {l.quantity} | {l.supplier} | {l.unit_cost:,.2f} | "
                       f"{l.unit_price:,.2f} | {l.discount_rate:.1%} | {l.net_amount:,.2f} | {l.margin_pct:.1%} |")
        t = q.totals
        out += ["", f"Net {cur} {t.net_total:,.2f} + tax {t.tax_total:,.2f} = **{cur} {t.grand_total:,.2f}** "
                f"| cost {t.cost_total:,.2f} | margin {t.margin_total:,.2f} ({t.margin_pct:.1%})"]
        for u in q.unpriced:
            out.append(f"- Not priced: {u.requested_text} - {u.reason}")
    if state.checks:
        icon = {"block": "BLOCK", "warn": "WARN", "info": "info"}
        out += ["", "## Checks", ""] + [f"- **{icon[c.severity]}** `{c.code}` {c.message}" for c in state.checks]
    if state.approved_by:
        out += ["", f"Approved by **{state.approved_by}**."]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# main steps
# ---------------------------------------------------------------------------


def start_quote(
    request_text: str,
    *,
    settings: Settings,
    catalog: Catalog,
    llm: LLM,
    out_root: Path,
    today: Optional[date] = None,
    quote_no: Optional[str] = None,
) -> tuple[QuoteState, Path]:
    today = today or date.today()
    quote_no = quote_no or make_quote_no(settings, request_text, today)
    run_dir = Path(out_root) / quote_no
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "request.txt").write_text(request_text, encoding="utf-8")
    audit(run_dir, "received", chars=len(request_text), provider=llm.label)

    raw = get_extractor(llm).extract(request_text, catalog, today)
    extraction = normalise(raw, catalog)
    (run_dir / "extraction.json").write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
    audit(run_dir, "extracted", items=len(extraction.items),
          changed_by_normalise=raw.model_dump() != extraction.model_dump())

    state = QuoteState(
        quote_no=quote_no, created_on=today, valid_until=today + timedelta(days=settings.quote.validity_days),
        status="PENDING_APPROVAL", provider=llm.label, request_text=request_text, extraction=extraction,
    )
    writer = Writer(llm, settings)

    state.clarifications = find_clarifications(extraction, catalog)
    if state.clarifications:
        state.status = "NEEDS_CLARIFICATION"
        email = writer.clarification_email(extraction, state.clarifications)
        (run_dir / "clarification_email.txt").write_text(email, encoding="utf-8")
        audit(run_dir, "clarification_needed", questions=len(state.clarifications),
              writer_fallback=writer.last_fallback_reason)
    else:
        state.quote = build_quote(extraction, catalog, settings, today)
        state.checks = run_checks(state.quote, extraction, settings, today, state.valid_until)
        state.status = "BLOCKED" if state.blocking else "PENDING_APPROVAL"
        write_internal_costing(state, run_dir / "internal_costing.csv")
        if state.quote.lines:
            render_quote_pdf(state, settings, run_dir / "quote_DRAFT.pdf", draft=True)
        audit(run_dir, "priced", lines=len(state.quote.lines), grand_total=state.quote.totals.grand_total,
              blocking=[c.code for c in state.blocking])

    save_state(state, run_dir)
    (run_dir / "review.md").write_text(review_markdown(state), encoding="utf-8")
    return state, run_dir


def continue_with_reply(
    run_dir: Path, reply_text: str, *, settings: Settings, catalog: Catalog, llm: LLM, today: Optional[date] = None
) -> tuple[QuoteState, Path]:
    """The client answered our questions: re-read the whole thread under the same quote number."""
    previous = load_state(run_dir)
    thread = f"{previous.request_text.rstrip()}\n\n--- client reply ---\n{reply_text.strip()}\n"
    audit(Path(run_dir), "client_reply", chars=len(reply_text))
    return start_quote(thread, settings=settings, catalog=catalog, llm=llm, out_root=Path(run_dir).parent,
                       today=today, quote_no=previous.quote_no)


def approve_quote(
    run_dir: Path, approver: str, *, settings: Settings, llm: LLM, crm_dry_run: Optional[bool] = None
) -> QuoteState:
    run_dir = Path(run_dir)
    state = load_state(run_dir)
    if state.status == "APPROVED":
        raise ApprovalError(f"{state.quote_no} is already approved")
    if state.status != "PENDING_APPROVAL":
        raise ApprovalError(f"{state.quote_no} is {state.status}; only PENDING_APPROVAL quotes can be approved")
    if state.blocking:  # defensive: never approve with a blocking check
        raise ApprovalError("Quote has blocking checks: " + ", ".join(c.code for c in state.blocking))
    if not approver.strip():
        raise ApprovalError("Approver name is required")

    state.status = "APPROVED"
    state.approved_by = approver.strip()
    audit(run_dir, "approved", approver=state.approved_by)

    pdf_path = render_quote_pdf(state, settings, run_dir / f"{state.quote_no}.pdf", draft=False)
    writer = Writer(llm, settings)
    (run_dir / "quote_email.txt").write_text(writer.quote_email(state), encoding="utf-8")
    audit(run_dir, "documents_ready", pdf=pdf_path.name, writer_fallback=writer.last_fallback_reason)

    state.crm = push_to_hubspot(state, settings, pdf_path, run_dir, dry_run=crm_dry_run)
    audit(run_dir, "crm", **state.crm)

    save_state(state, run_dir)
    (run_dir / "review.md").write_text(review_markdown(state), encoding="utf-8")
    return state


def reject_quote(run_dir: Path, approver: str, reason: str) -> QuoteState:
    run_dir = Path(run_dir)
    state = load_state(run_dir)
    state.status = "REJECTED"
    state.approved_by = None
    audit(run_dir, "rejected", by=approver, reason=reason)
    save_state(state, run_dir)
    (run_dir / "review.md").write_text(review_markdown(state) + f"\nRejected by {approver}: {reason}\n", encoding="utf-8")
    return state
