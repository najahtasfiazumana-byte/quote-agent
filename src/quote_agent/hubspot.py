"""Step 7 - push the approved quote into HubSpot (free CRM).

What gets created:
  1. Contact  (found by email, or created)
  2. Deal     (amount = grand total, stage = "quote sent", associated to the contact)
  3. Line items (one per quote line, associated to the deal)
  4. The PDF  (uploaded to HubSpot Files and attached to the deal through a note)

With no HUBSPOT_TOKEN set (or --crm dry-run) nothing is sent: every request
is written to hubspot_requests.json instead, so you can show exactly what
would happen without a CRM account.

Association type IDs used (HubSpot-defined):
  deal -> contact = 3, line item -> deal = 20, note -> deal = 214
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import requests

from .config import Settings
from .models import QuoteState

BASE = "https://api.hubapi.com"
DEAL_TO_CONTACT = 3
LINE_ITEM_TO_DEAL = 20
NOTE_TO_DEAL = 214


class HubSpotError(RuntimeError):
    pass


class HubSpotClient:
    def __init__(self, token: Optional[str] = None, dry_run: Optional[bool] = None):
        self.token = token or os.getenv("HUBSPOT_TOKEN")
        self.dry_run = (not self.token) if dry_run is None else dry_run
        self.log: list[dict] = []
        self._fake_id = 1000

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, *, json_body: Optional[dict] = None, files=None, data=None) -> dict:
        entry = {"method": method, "url": BASE + path, "body": json_body if json_body is not None else data}
        if self.dry_run:
            self._fake_id += 1
            entry["response"] = {"id": f"DRYRUN-{self._fake_id}", "results": []}
            self.log.append(entry)
            return entry["response"]
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = requests.request(method, BASE + path, headers=headers, json=json_body, files=files, data=data, timeout=30)
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.request(method, BASE + path, headers=headers, json=json_body, files=files, data=data, timeout=30)
        entry["status"] = resp.status_code
        self.log.append(entry)
        if resp.status_code >= 400:
            raise HubSpotError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
        return resp.json() if resp.content else {}

    # -- objects -----------------------------------------------------------
    def find_or_create_contact(self, email: Optional[str], name: Optional[str], company: Optional[str]) -> str:
        if email:
            found = self._request("POST", "/crm/v3/objects/contacts/search", json_body={
                "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                "properties": ["email"], "limit": 1,
            })
            if found.get("results"):
                return found["results"][0]["id"]
        first, _, last = (name or "").partition(" ")
        props = {k: v for k, v in {"email": email, "firstname": first or None, "lastname": last or None,
                                   "company": company}.items() if v}
        return self._request("POST", "/crm/v3/objects/contacts", json_body={"properties": props})["id"]

    def create_deal(self, state: QuoteState, settings: Settings, contact_id: str) -> str:
        client = state.extraction.client
        props = {
            "dealname": f"{client.company or client.name or 'Client'} - {state.quote_no}",
            "amount": str(state.quote.totals.grand_total),
            "pipeline": settings.hubspot.pipeline,
            "dealstage": settings.hubspot.deal_stage_quote_sent,
            "closedate": f"{state.valid_until.isoformat()}T00:00:00Z",
            "description": f"Quote {state.quote_no}, valid until {state.valid_until}. "
                           f"Margin {state.quote.totals.margin_pct:.1%}. Approved by {state.approved_by}.",
        }
        body = {"properties": props, "associations": [_assoc(contact_id, DEAL_TO_CONTACT)]}
        return self._request("POST", "/crm/v3/objects/deals", json_body=body)["id"]

    def add_line_items(self, state: QuoteState, deal_id: str) -> list[str]:
        ids = []
        for line in state.quote.lines:
            props = {
                "name": line.name,
                "hs_sku": line.sku,
                "quantity": str(line.quantity),
                "price": str(line.unit_price),
                "hs_discount_percentage": str(line.discount_rate * 100),
                "description": f"{line.billing_term} - {line.unit_label}",
            }
            body = {"properties": props, "associations": [_assoc(deal_id, LINE_ITEM_TO_DEAL)]}
            ids.append(self._request("POST", "/crm/v3/objects/line_items", json_body=body)["id"])
        return ids

    def attach_pdf(self, pdf_path: Path, deal_id: str, quote_no: str) -> str:
        options = json.dumps({"access": "PRIVATE"})
        if self.dry_run:
            file_id = self._request("POST", "/files/v3/files", data={"file": pdf_path.name, "folderPath": "/quotes", "options": options})["id"]
        else:
            with open(pdf_path, "rb") as fh:
                file_id = self._request(
                    "POST", "/files/v3/files",
                    files={"file": (pdf_path.name, fh, "application/pdf")},
                    data={"folderPath": "/quotes", "options": options},
                )["id"]
        note = {
            "properties": {
                "hs_timestamp": str(int(time.time() * 1000)),
                "hs_note_body": f"Quotation {quote_no} sent to client (PDF attached).",
                "hs_attachment_ids": str(file_id),
            },
            "associations": [_assoc(deal_id, NOTE_TO_DEAL)],
        }
        return self._request("POST", "/crm/v3/objects/notes", json_body=note)["id"]


def _assoc(to_id: str, type_id: int) -> dict:
    return {"to": {"id": to_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}]}


def push_to_hubspot(state: QuoteState, settings: Settings, pdf_path: Path, out_dir: Path,
                    dry_run: Optional[bool] = None) -> dict:
    hs = HubSpotClient(dry_run=dry_run)
    result: dict = {"mode": "dry-run" if hs.dry_run else "live"}
    try:
        c = state.extraction.client
        result["contact_id"] = hs.find_or_create_contact(c.email, c.name, c.company)
        result["deal_id"] = hs.create_deal(state, settings, result["contact_id"])
        result["line_item_ids"] = hs.add_line_items(state, result["deal_id"])
        result["note_id"] = hs.attach_pdf(pdf_path, result["deal_id"], state.quote_no)
        result["ok"] = True
    except (HubSpotError, requests.RequestException) as exc:
        result["ok"] = False
        result["error"] = str(exc)
    finally:
        (out_dir / "hubspot_requests.json").write_text(json.dumps(hs.log, indent=2, default=str), encoding="utf-8")
    return result
