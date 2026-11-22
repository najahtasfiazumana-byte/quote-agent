"""Data models shared by every step of the pipeline.

The LLM only ever fills in `Extraction`. Everything after that (prices,
discounts, tax, totals, checks) is computed by deterministic Python code.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

BillingTerm = Literal["annual", "monthly", "one_time"]
Severity = Literal["block", "warn", "info"]


# ---------------------------------------------------------------------------
# Step 1: what the client asked for (filled by the LLM or the offline parser)
# ---------------------------------------------------------------------------


class Client(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None


class RequestedItem(BaseModel):
    requested_text: str = Field(description="The client's own words for this item")
    family: Optional[str] = Field(None, description="Catalog family, or null if not in catalog")
    sku: Optional[str] = Field(None, description="Exact catalog SKU, or null if not specified clearly")
    quantity: Optional[int] = None
    billing_term: Optional[BillingTerm] = None
    notes: Optional[str] = None

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("quantity must be a positive integer")
        return v


class Extraction(BaseModel):
    client: Client = Field(default_factory=Client)
    items: list[RequestedItem] = Field(default_factory=list)
    deadline: Optional[date] = None
    deadline_text: Optional[str] = None
    language: Literal["en", "ms", "zh", "other"] = "en"


# ---------------------------------------------------------------------------
# Step 2: what we still need to ask the client
# ---------------------------------------------------------------------------


class Clarification(BaseModel):
    item_index: Optional[int] = None
    field: str
    question: str
    options: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Step 3: pricing (pure Decimal maths)
# ---------------------------------------------------------------------------


class SupplierOption(BaseModel):
    supplier: str
    unit_cost: Decimal
    lead_time_days: int
    stock: Optional[int] = None  # None = not stock-limited (software)
    valid_until: date
    status: str  # "chosen", "eligible" or "excluded: <reason>"


class PricedLine(BaseModel):
    line_no: int
    sku: str
    name: str
    category: str
    billing_term: BillingTerm
    unit_label: str
    quantity: int
    supplier: str
    unit_cost: Decimal
    lead_time_days: int
    unit_price: Decimal
    line_subtotal: Decimal
    discount_rate: Decimal
    discount_amount: Decimal
    net_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    line_total: Decimal
    cost_total: Decimal
    margin_amount: Decimal
    margin_pct: Decimal
    deadline_risk: bool = False
    options_considered: list[SupplierOption] = Field(default_factory=list)


class QuoteTotals(BaseModel):
    subtotal: Decimal
    discount_total: Decimal
    net_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    cost_total: Decimal
    margin_total: Decimal
    margin_pct: Decimal


class UnpricedItem(BaseModel):
    requested_text: str
    reason: str


class PricedQuote(BaseModel):
    lines: list[PricedLine]
    totals: QuoteTotals
    unpriced: list[UnpricedItem] = Field(default_factory=list)
    currency: str = "MYR"


# ---------------------------------------------------------------------------
# Step 4: accuracy checks
# ---------------------------------------------------------------------------


class CheckResult(BaseModel):
    code: str
    severity: Severity
    message: str
    line_no: Optional[int] = None


# ---------------------------------------------------------------------------
# The full state of one quote run (saved to state.json)
# ---------------------------------------------------------------------------

Status = Literal["NEEDS_CLARIFICATION", "BLOCKED", "PENDING_APPROVAL", "APPROVED", "REJECTED"]


class QuoteState(BaseModel):
    quote_no: str
    created_on: date
    valid_until: date
    status: Status
    provider: str
    request_text: str
    extraction: Extraction
    clarifications: list[Clarification] = Field(default_factory=list)
    quote: Optional[PricedQuote] = None
    checks: list[CheckResult] = Field(default_factory=list)
    approved_by: Optional[str] = None
    crm: Optional[dict] = None

    @property
    def blocking(self) -> list[CheckResult]:
        return [c for c in self.checks if c.severity == "block"]
