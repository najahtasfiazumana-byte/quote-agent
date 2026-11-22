"""Product catalog and supplier price lists (plain CSV files, easy to swap for Google Sheets)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

FAMILY_LABELS = {
    "microsoft_365": "Microsoft 365 plan",
    "visio": "Visio plan",
    "google_workspace": "Google Workspace plan",
    "adobe_cc": "Adobe Creative Cloud plan",
    "zoom": "Zoom plan",
    "claude": "Claude plan",
    "laptop": "laptop model",
    "monitor": "monitor model",
    "dock": "docking station",
}

UNIT_LABELS = {
    ("user", "annual"): "per user / year",
    ("user", "monthly"): "per user / month",
    ("unit", "one_time"): "per unit",
}


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    family: str
    category: str
    billing_terms: tuple[str, ...]
    unit: str
    aliases: tuple[str, ...]

    def unit_label(self, term: str) -> str:
        return UNIT_LABELS.get((self.unit, term), f"per {self.unit}")

    @property
    def is_subscription(self) -> bool:
        return "one_time" not in self.billing_terms


@dataclass(frozen=True)
class SupplierPrice:
    supplier: str
    sku: str
    billing_term: str
    unit_cost: Decimal
    stock: Optional[int]
    lead_time_days: int
    min_qty: int
    valid_until: date


@dataclass
class Catalog:
    products: dict[str, Product]
    prices: list[SupplierPrice] = field(default_factory=list)

    # -- lookups ---------------------------------------------------------
    def get(self, sku: Optional[str]) -> Optional[Product]:
        return self.products.get(sku) if sku else None

    def family_members(self, family: Optional[str]) -> list[Product]:
        return [p for p in self.products.values() if p.family == family]

    @property
    def families(self) -> set[str]:
        return {p.family for p in self.products.values()}

    def prices_for(self, sku: str, billing_term: str) -> list[SupplierPrice]:
        return [p for p in self.prices if p.sku == sku and p.billing_term == billing_term]

    def as_prompt_table(self) -> str:
        """Compact catalog listing given to the LLM so it can map requests to SKUs."""
        rows = ["sku | name | family | billing_terms"]
        for p in self.products.values():
            rows.append(f"{p.sku} | {p.name} | {p.family} | {'/'.join(p.billing_terms)}")
        return "\n".join(rows)


def supplier_display_name(file_stem: str) -> str:
    words = file_stem.replace("_", " ").split()
    return " ".join(w.upper() if len(w) <= 2 else w.capitalize() for w in words)


def load_catalog(data_dir: Path) -> Catalog:
    data_dir = Path(data_dir)
    products: dict[str, Product] = {}
    with open(data_dir / "catalog.csv", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            products[row["sku"]] = Product(
                sku=row["sku"].strip(),
                name=row["name"].strip(),
                family=row["family"].strip(),
                category=row["category"].strip(),
                billing_terms=tuple(t.strip() for t in row["billing_terms"].split("|")),
                unit=row["unit"].strip(),
                aliases=tuple(a.strip().lower() for a in row["aliases"].split(";") if a.strip()),
            )

    prices: list[SupplierPrice] = []
    for path in sorted((data_dir / "suppliers").glob("*.csv")):
        supplier = supplier_display_name(path.stem)
        with open(path, newline="", encoding="utf-8") as fh:
            for i, row in enumerate(csv.DictReader(fh), start=2):
                sku = row["sku"].strip()
                if sku not in products:
                    raise ValueError(f"{path.name} line {i}: unknown SKU {sku!r} (not in catalog.csv)")
                prices.append(
                    SupplierPrice(
                        supplier=supplier,
                        sku=sku,
                        billing_term=row["billing_term"].strip(),
                        unit_cost=Decimal(row["unit_cost"].strip()),
                        stock=int(row["stock"]) if row["stock"].strip() else None,
                        lead_time_days=int(row["lead_time_days"]),
                        min_qty=int(row["min_qty"] or 1),
                        valid_until=date.fromisoformat(row["valid_until"].strip()),
                    )
                )
    return Catalog(products=products, prices=prices)
