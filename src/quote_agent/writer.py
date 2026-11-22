"""Client-facing emails: clarification questions and the quote cover note.

The LLM is allowed to make the wording friendly, in the client's language,
but it is NOT allowed to introduce numbers. Every number in its draft must
already appear in the facts we gave it; otherwise the draft is thrown away
and a safe template is used instead. A wrong number in an email is as bad
as a wrong number on the quote.
"""

from __future__ import annotations

import re
from typing import Optional

from .config import Settings
from .llm import LLM, LLMError, MockLLM
from .models import Clarification, Extraction, QuoteState

# A percentage is a different fact from the plain number ("15-minute call" does not allow "15% off").
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\s?%?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").replace(" ", "").rstrip(".") for n in NUMBER.findall(text)}


def numbers_are_safe(draft: str, facts: str) -> bool:
    return _numbers(draft) <= _numbers(facts)


def _greeting_name(extraction: Extraction) -> str:
    name = extraction.client.name or ""
    return name.split()[0] if name else ("Tuan/Puan" if extraction.language == "ms" else "there")


# ---------------------------------------------------------------------------
# Templates (also the safe fallback)
# ---------------------------------------------------------------------------


def template_clarification(extraction: Extraction, questions: list[Clarification], settings: Settings) -> str:
    who = _greeting_name(extraction)
    lines = []
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q.question}")
        lines.extend(f"   - {opt}" for opt in q.options)
    body = "\n".join(lines)
    if extraction.language == "ms":
        return (
            f"Salam {who},\n\nTerima kasih atas permintaan sebut harga anda. Sebelum kami sediakan "
            f"sebut harga yang tepat, mohon sahkan perkara berikut:\n\n{body}\n\n"
            f"Sebaik sahaja kami terima jawapan, sebut harga akan dihantar dengan segera.\n\n"
            f"Terima kasih,\n{settings.company.name}"
        )
    return (
        f"Hi {who},\n\nThanks for your request. So that we quote exactly what you need, "
        f"could you confirm a few details?\n\n{body}\n\n"
        f"As soon as we hear back we'll send the quote over.\n\n"
        f"Best regards,\n{settings.company.name}"
    )


def template_quote_email(state: QuoteState, settings: Settings) -> str:
    ex = state.extraction
    who = _greeting_name(ex)
    total = f"{state.quote.currency} {state.quote.totals.grand_total:,.2f}"
    manual = [u.requested_text for u in state.quote.unpriced if u.reason.startswith("not in catalog")]
    if ex.language == "ms":
        extra = (
            f"\nUntuk {', '.join(manual)}, kami sedang menyemak dengan pembekal dan akan menghantar "
            "sebut harga berasingan.\n" if manual else ""
        )
        return (
            f"Salam {who},\n\nBersama-sama ini dilampirkan sebut harga {state.quote_no} berjumlah {total} "
            f"(termasuk {settings.tax.name}), sah sehingga {state.valid_until:%d %b %Y}.\n{extra}\n"
            f"Sila maklumkan jika ada sebarang pertanyaan.\n\nTerima kasih,\n{settings.company.name}"
        )
    extra = (
        f"\nFor {', '.join(manual)}, we're checking with our suppliers and will send a separate quote shortly.\n"
        if manual else ""
    )
    return (
        f"Hi {who},\n\nPlease find attached quotation {state.quote_no} for {total} "
        f"(including {settings.tax.name}), valid until {state.valid_until:%d %b %Y}.\n{extra}\n"
        f"Reply to this email to confirm and we'll place the order straight away.\n\n"
        f"Best regards,\n{settings.company.name}"
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

WRITER_SYSTEM = """You write short, warm, professional emails for an IT sourcing company in Malaysia.
Write in the language code given (en = English, ms = Bahasa Malaysia, zh = Simplified Chinese).
Use ONLY the facts provided. Do not add any prices, quantities, dates, percentages or other
numbers that are not in the facts. Plain text, no markdown, under 160 words.
Sign off with the company name given."""


class Writer:
    def __init__(self, llm: LLM, settings: Settings):
        self.llm = llm
        self.settings = settings
        self.last_fallback_reason: Optional[str] = None

    def _polish(self, facts: str, fallback: str) -> str:
        self.last_fallback_reason = None
        if isinstance(self.llm, MockLLM):
            return fallback
        try:
            draft = self.llm.complete_text(WRITER_SYSTEM, facts).strip()
        except LLMError as exc:
            self.last_fallback_reason = f"LLM error: {exc}"
            return fallback
        if not draft:
            self.last_fallback_reason = "empty draft"
            return fallback
        if not numbers_are_safe(draft, facts):
            self.last_fallback_reason = f"draft contained numbers not in the facts: {_numbers(draft) - _numbers(facts)}"
            return fallback
        return draft

    def clarification_email(self, extraction: Extraction, questions: list[Clarification]) -> str:
        fallback = template_clarification(extraction, questions, self.settings)
        facts = (
            f"Language: {extraction.language}\nClient first name: {_greeting_name(extraction)}\n"
            f"Company name to sign with: {self.settings.company.name}\n"
            "Purpose: thank them and ask these questions before we can quote (keep every option listed):\n"
            + "\n".join(f"- {q.question} Options: {', '.join(q.options) or 'open answer'}" for q in questions)
        )
        return self._polish(facts, fallback)

    def quote_email(self, state: QuoteState) -> str:
        fallback = template_quote_email(state, self.settings)
        manual = [u.requested_text for u in state.quote.unpriced if u.reason.startswith("not in catalog")]
        facts = (
            f"Language: {state.extraction.language}\nClient first name: {_greeting_name(state.extraction)}\n"
            f"Company name to sign with: {self.settings.company.name}\n"
            f"Purpose: cover email for the attached PDF quotation.\n"
            f"Quote number: {state.quote_no}\n"
            f"Grand total: {state.quote.currency} {state.quote.totals.grand_total:,.2f} including {self.settings.tax.name}\n"
            f"Valid until: {state.valid_until:%d %b %Y}\n"
            f"Items we will quote separately after checking suppliers: {', '.join(manual) or 'none'}\n"
            "Ask them to reply to confirm so we can place the order."
        )
        return self._polish(facts, fallback)
