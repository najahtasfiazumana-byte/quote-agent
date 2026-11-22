"""Load config.yaml into typed settings. Money values become Decimal."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"


class CompanyConfig(BaseModel):
    name: str
    registration_no: str = ""
    tax_registration_no: str = ""
    address: list[str] = []
    email: str = ""
    phone: str = ""


class QuoteConfig(BaseModel):
    validity_days: int = 14
    payment_terms: str = "30 days from invoice date"
    number_prefix: str = "Q"


class VolumeDiscount(BaseModel):
    min_qty: int
    rate: Decimal


class PricingConfig(BaseModel):
    target_margin: dict[str, Decimal]
    margin_floor: Decimal
    volume_discounts: list[VolumeDiscount] = []
    rounding: str = "ROUND_HALF_UP"


class TaxConfig(BaseModel):
    name: str = "Tax"
    rates: dict[str, Decimal]


class ChecksConfig(BaseModel):
    manager_approval_threshold: Decimal = Decimal("50000")
    price_expiry_warning_days: int = 3


class HubSpotConfig(BaseModel):
    pipeline: str = "default"
    deal_stage_quote_sent: str = "presentationscheduled"


class Settings(BaseModel):
    company: CompanyConfig
    currency: str = "MYR"
    quote: QuoteConfig = QuoteConfig()
    pricing: PricingConfig
    tax: TaxConfig
    checks: ChecksConfig = ChecksConfig()
    hubspot: HubSpotConfig = HubSpotConfig()


def load_settings(path: Optional[Path] = None) -> Settings:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    # Read numbers as strings first so 0.12 never becomes a binary float.
    return Settings.model_validate(_floats_to_str(raw))


def _floats_to_str(obj):
    if isinstance(obj, float):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _floats_to_str(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_str(v) for v in obj]
    return obj
