"""Step 5 - render the client-facing PDF quote (ReportLab, free and offline).

Supplier names and costs are internal and never appear on the PDF; they go
to internal_costing.csv instead. Unapproved quotes carry a DRAFT watermark.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as esc

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import Settings
from .models import QuoteState

INK = colors.HexColor("#1F2A37")
MUTED = colors.HexColor("#6B7280")
ACCENT = colors.HexColor("#0F766E")
RULE = colors.HexColor("#D1D5DB")
BAND = colors.HexColor("#F3F4F6")


class _QuoteDoc(SimpleDocTemplate):
    """Draws the DRAFT watermark after the page content, so tables can't hide it."""

    draft = False

    def afterPage(self):
        if not self.draft:
            return
        c = self.canv
        c.saveState()
        c.setFont("Helvetica-Bold", 110)
        c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.15))
        c.translate(A4[0] / 2, A4[1] / 2)
        c.rotate(35)
        c.drawCentredString(0, -30, "DRAFT")
        c.restoreState()


def _styles():
    base = getSampleStyleSheet()
    return {
        "company": ParagraphStyle("company", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=14, textColor=INK, leading=17),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8.5, textColor=MUTED, leading=11),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, textColor=INK, leading=12.5),
        "bold": ParagraphStyle("bold", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9.5, textColor=INK, leading=12.5),
        "title": ParagraphStyle("title", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=20, textColor=ACCENT, alignment=TA_RIGHT, leading=24),
        "right": ParagraphStyle("right", parent=base["Normal"], fontSize=9.5, textColor=INK, alignment=TA_RIGHT, leading=12.5),
        "h": ParagraphStyle("h", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=MUTED, leading=12, spaceBefore=2),
    }


def _m(value) -> str:
    return f"{value:,.2f}"


def render_quote_pdf(state: QuoteState, settings: Settings, path: Path, draft: bool) -> Path:
    s = _styles()
    q = state.quote
    co = settings.company
    client = state.extraction.client
    cur = q.currency

    doc = _QuoteDoc(
        str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title=f"Quotation {state.quote_no}", author=co.name,
    )
    width = doc.width
    story = []

    # Header -----------------------------------------------------------------
    left = [Paragraph(esc(co.name), s["company"])]
    left += [Paragraph(esc(line), s["small"]) for line in co.address]
    left.append(Paragraph(f"Reg. No. {co.registration_no} &nbsp;|&nbsp; {settings.tax.name} No. {co.tax_registration_no}", s["small"]))
    left.append(Paragraph(f"{co.email} &nbsp;|&nbsp; {co.phone}", s["small"]))
    right = [
        Paragraph("QUOTATION", s["title"]),
        Paragraph(f"<b>No.</b> {state.quote_no}", s["right"]),
        Paragraph(f"<b>Date</b> {state.created_on:%d %b %Y}", s["right"]),
        Paragraph(f"<b>Valid until</b> {state.valid_until:%d %b %Y}", s["right"]),
    ]
    header = Table([[left, right]], colWidths=[width * 0.6, width * 0.4])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 6 * mm)]

    # Bill to ------------------------------------------------------------------
    bill = [Paragraph("PREPARED FOR", s["h"])]
    for value in (client.name, client.company, client.email):
        if value:
            bill.append(Paragraph(esc(value), s["body"]))
    if state.extraction.deadline:
        bill.append(Paragraph(f"Required by: {state.extraction.deadline:%d %b %Y}", s["small"]))
    story += bill + [Spacer(1, 5 * mm)]

    # Line items ----------------------------------------------------------------
    rows = [["#", "Description", "Qty", f"Unit price ({cur})", "Discount", f"Amount ({cur})"]]
    for line in q.lines:
        desc = Paragraph(
            f"<b>{esc(line.name)}</b><br/><font size=8 color='#6B7280'>{line.sku} &middot; "
            f"{line.billing_term.replace('_', '-')} &middot; {line.unit_label} &middot; "
            f"{settings.tax.name} {line.tax_rate:.0%}</font>", s["body"]
        )
        disc = f"{line.discount_rate:.1%}" if line.discount_rate else "-"
        rows.append([str(line.line_no), desc, str(line.quantity), _m(line.unit_price), disc, _m(line.net_amount)])
    col = [8 * mm, width - 8 * mm - 14 * mm - 30 * mm - 20 * mm - 30 * mm, 14 * mm, 30 * mm, 20 * mm, 30 * mm]
    items = Table(rows, colWidths=col, repeatRows=1)
    items.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [items, Spacer(1, 4 * mm)]

    # Totals -------------------------------------------------------------------
    t = q.totals
    tot_rows = [["Subtotal", _m(t.subtotal)]]
    if t.discount_total:
        tot_rows.append(["Volume discount", f"-{_m(t.discount_total)}"])
        tot_rows.append(["Net amount", _m(t.net_total)])
    tot_rows.append([f"{settings.tax.name}", _m(t.tax_total)])
    tot_rows.append([f"TOTAL ({cur})", _m(t.grand_total)])
    totals = Table(tot_rows, colWidths=[40 * mm, 32 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, -1), (-1, -1), 11),
        ("TEXTCOLOR", (0, -1), (-1, -1), ACCENT),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [totals, Spacer(1, 6 * mm)]

    # Notes & terms --------------------------------------------------------------
    manual = [u.requested_text for u in q.unpriced if u.reason.startswith("not in catalog")]
    if manual:
        story.append(Paragraph("NOT INCLUDED IN THIS QUOTE", s["h"]))
        story.append(Paragraph(f"{esc(', '.join(manual))}: we are confirming availability with suppliers and will quote separately.", s["body"]))
        story.append(Spacer(1, 3 * mm))

    lead = max((l.lead_time_days for l in q.lines), default=0)
    tax_rates = [f"{r:.0%}" for r in sorted({l.tax_rate for l in q.lines})]
    terms = [
        f"Prices are in {cur} and valid until {state.valid_until:%d %b %Y}.",
        f"{settings.tax.name} charged at {', '.join(tax_rates)} as shown on each item.",
        f"Estimated delivery / activation: up to {lead} day(s) after purchase order, subject to supplier confirmation.",
        f"Payment terms: {settings.quote.payment_terms}.",
    ]
    if any(l.billing_term == "annual" for l in q.lines):
        terms.append("Annual subscriptions are a 12-month commitment and cannot be reduced mid-term.")
    if any(l.billing_term == "monthly" for l in q.lines):
        terms.append("Monthly subscription prices are per month and renew automatically until cancelled.")
    story.append(Paragraph("TERMS", s["h"]))
    story += [Paragraph(f"&bull; {term}", s["small"]) for term in terms]

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"{co.name} - Quotation {state.quote_no}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.draft = draft
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path
