"""Step 1 - turn a messy request (email / WhatsApp / follow-up thread) into structured data.

Two extractors share one interface:

* `LLMExtractor` - asks the model for JSON, validates it with Pydantic and
  retries once with the validation error if the model gets it wrong.
* `RuleBasedExtractor` - an offline keyword parser. It is deliberately simple
  and only exists so tests and demos run with no API key and no cost.

Whatever the extractor returns is then passed through `normalise()`, which
re-checks every SKU against the catalog in code. The model never gets the
final say on what product is being quoted.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from typing import Optional

from pydantic import ValidationError

from .catalog import Catalog
from .llm import LLM, LLMError, MockLLM
from .models import Client, Extraction, RequestedItem

# ---------------------------------------------------------------------------
# LLM extractor
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """You are the intake assistant for an IT sourcing company in Malaysia.
You read client purchase requests (emails, WhatsApp messages, follow-up threads; English,
Bahasa Malaysia or Mandarin) and convert them into JSON for a quoting system.

Rules:
- Never invent information. Use null for anything the client did not state.
- Map each requested product to the catalog below.
  * Set "sku" ONLY if the client clearly identified that exact product
    (e.g. "Business Standard" -> M365-BS). Words like "Microsoft 365" or "laptops"
    alone are NOT specific: set "family" and leave "sku" null.
  * If the product is not in the catalog at all, set family=null and sku=null and keep
    the client's wording in "requested_text".
- "quantity" is an integer count of users/seats/units, or null.
- "billing_term" is "annual", "monthly" or "one_time" (hardware), or null if not stated
  for a subscription.
- "deadline" is an ISO date (YYYY-MM-DD) for when the client needs the goods/service,
  resolved relative to TODAY. Keep the client's words in "deadline_text".
- If the text contains several messages (a thread), later messages override earlier
  ones. Return ONE entry per product, merged.
- "language" is the language the client wrote in: en, ms, zh or other.

Return JSON with exactly this shape:
{
  "client": {"name": str|null, "email": str|null, "company": str|null},
  "items": [{"requested_text": str, "family": str|null, "sku": str|null,
             "quantity": int|null, "billing_term": "annual"|"monthly"|"one_time"|null,
             "notes": str|null}],
  "deadline": "YYYY-MM-DD"|null,
  "deadline_text": str|null,
  "language": "en"|"ms"|"zh"|"other"
}"""


class LLMExtractor:
    def __init__(self, llm: LLM):
        self.llm = llm

    def extract(self, text: str, catalog: Catalog, today: date) -> Extraction:
        user = (
            f"TODAY: {today.isoformat()} ({today.strftime('%A')})\n\n"
            f"CATALOG:\n{catalog.as_prompt_table()}\n\n"
            f"CLIENT REQUEST:\n<<<\n{text}\n>>>"
        )
        last_error: Optional[str] = None
        for _ in range(2):
            prompt = user if last_error is None else (
                f"{user}\n\nYour previous answer was invalid: {last_error}\nReturn corrected JSON."
            )
            try:
                data = self.llm.complete_json(EXTRACTION_SYSTEM, prompt)
                return Extraction.model_validate(data)
            except (ValidationError, LLMError, json.JSONDecodeError) as exc:
                last_error = str(exc)[:500]
        raise LLMError(f"Extraction failed after retry: {last_error}")


# ---------------------------------------------------------------------------
# Offline rule-based extractor (tests / zero-cost demo)
# ---------------------------------------------------------------------------

# Generic words that identify a product family but not an exact SKU.
FAMILY_KEYWORDS = {
    "microsoft_365": ["microsoft 365", "office 365", "m365", "o365"],
    "visio": ["visio"],
    "google_workspace": ["google workspace"],
    "laptop": ["laptops", "laptop", "notebooks", "notebook", "komputer riba"],
    "monitor": ["monitors", "monitor"],
    "dock": ["docking stations", "docking station", "docks"],
}

ANNUAL_WORDS = ["annual", "annually", "yearly", "per year", "1 year", "12 months", "tahunan", "setahun"]
MONTHLY_WORDS = ["monthly", "per month", "month-to-month", "bulanan", "sebulan"]
NOT_QUANTITY_AFTER = re.compile(
    r"^\s*(days?|hari|weeks?|minggu|months?|bulan|years?|tahun|pm|am|hours?|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b"
)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}
MALAY_HINTS = ["boleh", "terima kasih", "sebut harga", "lesen", "salam", "kami", "perlukan", "tahunan", "bulanan"]
UNKNOWN_PRODUCT = re.compile(
    r"(\d+)\s+(?:seats?|licen[cs]es?|lesen|units?|copies|subscriptions?)\s+(?:of\s+)?"
    r"([A-Z][\w\-]*(?:\s+[A-Z][\w\-]*)*)"
)


class RuleBasedExtractor:
    """Keyword parser. Good enough for the sample requests, not for production."""

    def extract(self, text: str, catalog: Catalog, today: date) -> Extraction:
        # Blank out e-mail headers (keeps character offsets) so a subject line like
        # "need M365 + laptops" doesn't decide the item order.
        lower = re.sub(r"(?im)^(from|to|cc|subject|date):.*$", lambda m: " " * len(m.group(0)), text).lower()
        aliases = self._alias_table(catalog)
        matches = self._find_matches(lower, aliases)

        merged: dict[str, RequestedItem] = {}  # key: family (later mentions override earlier)
        order: list[str] = []
        for start, end, family, sku in matches:
            sentence = _span_around(lower, start, end, r"[\n.!?]")
            clause = _span_around(lower, start, end, CLAUSE_BOUNDARY)
            qty = _find_quantity(clause, lower[start:end])
            term = _find_term(sentence)
            product = catalog.get(sku)
            if product and len(product.billing_terms) == 1:
                term = product.billing_terms[0]
            item = RequestedItem(
                requested_text=text[start:end], family=family, sku=sku, quantity=qty, billing_term=term
            )
            if family in merged:
                old = merged[family]
                merged[family] = RequestedItem(
                    requested_text=item.requested_text if sku else old.requested_text,
                    family=family,
                    sku=sku or old.sku,
                    quantity=qty if qty is not None else old.quantity,
                    billing_term=term or old.billing_term,
                )
            else:
                merged[family] = item
                order.append(family)

        items = [merged[f] for f in order]
        for m in UNKNOWN_PRODUCT.finditer(text):
            if not any(s <= m.start(2) < e for s, e, _, _ in matches):
                items.append(
                    RequestedItem(requested_text=m.group(2), family=None, sku=None, quantity=int(m.group(1)))
                )

        deadline, deadline_text = _find_deadline(lower, today)
        return Extraction(
            client=_find_client(text),
            items=items,
            deadline=deadline,
            deadline_text=deadline_text,
            language="ms" if sum(w in lower for w in MALAY_HINTS) >= 2 else "en",
        )

    @staticmethod
    def _alias_table(catalog: Catalog) -> list[tuple[str, str, Optional[str]]]:
        table: list[tuple[str, str, Optional[str]]] = []
        for p in catalog.products.values():
            for alias in {p.name.lower(), *p.aliases}:
                table.append((alias, p.family, p.sku))
        for family, words in FAMILY_KEYWORDS.items():
            table.extend((w, family, None) for w in words)
        return sorted(table, key=lambda t: (-len(t[0]), t[0]))  # longest match wins, deterministic

    @staticmethod
    def _find_matches(lower: str, aliases) -> list[tuple[int, int, str, Optional[str]]]:
        taken: list[tuple[int, int]] = []
        found = []
        for alias, family, sku in aliases:
            for m in re.finditer(r"(?<![\w])" + re.escape(alias) + r"(?![\w])", lower):
                s, e = m.span()
                if any(s < te and ts < e for ts, te in taken):
                    continue
                taken.append((s, e))
                found.append((s, e, family, sku))
        found.sort()
        # "30 Lenovo ThinkPad T14 laptops": a specific SKU and a generic family word
        # in the same clause are one item, not two.
        result = []
        for s, e, family, sku in found:
            if sku is None:
                left, right = _clause_bounds(lower, s, e)
                if any(f == family and k and left <= fs and fe <= right for fs, fe, f, k in found):
                    continue
            result.append((s, e, family, sku))
        return result


CLAUSE_BOUNDARY = r"[\n.!?,;]|\band\b|\bplus\b|\bdan\b"


def _bounds(text: str, start: int, end: int, boundary: str) -> tuple[int, int]:
    left = 0
    for m in re.finditer(boundary, text[:start]):
        left = m.end()
    right_match = re.search(boundary, text[end:])
    right = end + right_match.start() if right_match else len(text)
    return left, right


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    return _bounds(text, start, end, CLAUSE_BOUNDARY)


def _span_around(text: str, start: int, end: int, boundary: str) -> str:
    left, right = _bounds(text, start, end, boundary)
    return text[left:right]


def _find_quantity(clause: str, alias_text: str) -> Optional[int]:
    clause = clause.replace(alias_text, " ", 1)  # never read "365" or "5450" as a quantity
    for m in re.finditer(r"\b(\d{1,4}|" + "|".join(NUMBER_WORDS) + r")\b", clause):
        if NOT_QUANTITY_AFTER.match(clause[m.end():]):
            continue
        if re.search(r"\b(plan|gen)\s*$", clause[: m.start()]):
            continue
        token = m.group(1)
        return int(token) if token.isdigit() else NUMBER_WORDS[token]
    return None


def _find_term(sentence: str) -> Optional[str]:
    if any(w in sentence for w in ANNUAL_WORDS):
        return "annual"
    if any(w in sentence for w in MONTHLY_WORDS):
        return "monthly"
    return None


def _find_deadline(lower: str, today: date) -> tuple[Optional[date], Optional[str]]:
    m = re.search(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{4})?", lower)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        d = date(year, MONTHS[m.group(2)], int(m.group(1)))
        if not m.group(3) and d < today:
            d = date(year + 1, d.month, d.day)
        return d, m.group(0).strip()
    m = re.search(r"within\s+(\d+)\s+(days?|hari)", lower)
    if m:
        return today + timedelta(days=int(m.group(1))), m.group(0)
    m = re.search(r"end of (the )?month|hujung bulan", lower)
    if m:
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]), m.group(0)
    m = re.search(r"next month|bulan depan", lower)
    if m:
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return first_next, m.group(0)
    m = re.search(r"this week|minggu ini", lower)
    if m:
        return today + timedelta(days=(4 - today.weekday()) % 7), m.group(0)
    return None, None


def _find_client(text: str) -> Client:
    name = email = company = None
    header = re.search(r"^From:\s*(.*?)\s*<([^>]+)>", text, re.M)
    if header:
        name, email = header.group(1).strip() or None, header.group(2).strip()
    else:
        found = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        email = found.group(0) if found else None
    first_message = re.split(r"^-{3}.*-{3}\s*$", text, flags=re.M)[0]  # stop at "--- client reply ---"
    lines = [ln.strip() for ln in first_message.strip().splitlines()
             if ln.strip() and not re.match(r"(?i)(from|to|cc|subject|date):", ln.strip())]
    if lines:
        last = lines[-1].split(",")[-1].strip()
        if last and last != name and not last.startswith(("Subject", "From")) and len(last.split()) <= 6:
            company = last
    return Client(name=name, email=email, company=company)


# ---------------------------------------------------------------------------
# Normalisation - deterministic, catalog-checked
# ---------------------------------------------------------------------------


def normalise(extraction: Extraction, catalog: Catalog) -> Extraction:
    """Validate LLM output against the catalog and fill in anything that follows from it."""
    items: list[RequestedItem] = []
    for item in extraction.items:
        data = item.model_dump()
        product = catalog.get(item.sku)
        if item.sku and not product:
            data["notes"] = f"Model suggested unknown SKU {item.sku!r}; ignored"
            data["sku"] = None
        if product:
            data["family"] = product.family
        elif data["family"] and data["family"] not in catalog.families:
            data["family"] = None
        if not product and data["family"]:
            members = catalog.family_members(data["family"])
            if len(members) == 1:
                product = members[0]
                data["sku"] = product.sku
        if product and data["billing_term"] is None and len(product.billing_terms) == 1:
            data["billing_term"] = product.billing_terms[0]
        items.append(RequestedItem.model_validate(data))
    return extraction.model_copy(update={"items": items})


def get_extractor(llm: LLM):
    return RuleBasedExtractor() if isinstance(llm, MockLLM) else LLMExtractor(llm)
