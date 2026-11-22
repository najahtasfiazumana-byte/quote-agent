"""Streamlit review screen - the human-in-the-loop step, made visual for demos.

    pip install -e ".[ui]"
    streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

# Lets the app run without `pip install -e .` (e.g. on Streamlit Community Cloud).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from quote_agent.catalog import load_catalog
from quote_agent.config import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, load_settings
from quote_agent.llm import LLMError, default_provider_name, get_llm
from quote_agent.pipeline import ApprovalError, approve_quote, continue_with_reply, load_state, start_quote

st.set_page_config(page_title="Quote Agent", page_icon="🧾", layout="wide")
load_dotenv()
# On Streamlit Community Cloud, keys live in the app's Secrets settings. Copy them into the
# environment so the same code reads them locally (.env) and in the cloud.
try:
    for key, value in st.secrets.items():
        if isinstance(value, (str, int, float)):
            os.environ.setdefault(key, str(value))
except Exception:  # no secrets file locally - that's fine
    pass

settings = load_settings()
catalog = load_catalog(DEFAULT_DATA_DIR)
SAMPLES = sorted((DEFAULT_DATA_DIR / "requests").glob("*.txt"))

# -- sidebar ------------------------------------------------------------------
with st.sidebar:
    st.header("Settings")
    providers = ["gemini", "claude", "ollama", "mock"]
    provider = st.selectbox("LLM provider", providers, index=providers.index(default_provider_name()))
    today = st.date_input("Today", value=date.today(), help="Change to replay a request on a given date")
    approver = st.text_input("Approver name", value="")
    crm_mode = st.radio("HubSpot", ["auto", "dry-run", "live"], horizontal=True,
                        help="auto = live if HUBSPOT_TOKEN is set, otherwise dry-run")
    st.caption("Prices and suppliers are fictional demo data.")

st.title("🧾 Request-to-Quote Agent")
st.caption("Messy request in → structured items → questions or priced quote → checks → human approval → PDF + CRM")

# -- 1. request ------------------------------------------------------------------
col_in, col_sample = st.columns([3, 1])
with col_sample:
    pick = st.selectbox("Load a sample", ["-"] + [p.name for p in SAMPLES])
    if pick != "-" and st.button("Load"):
        st.session_state["request"] = (DEFAULT_DATA_DIR / "requests" / pick).read_text(encoding="utf-8")
with col_in:
    text = st.text_area("Client request (email / WhatsApp)", key="request", height=220)

try:
    llm = get_llm(provider)
except LLMError as exc:
    st.error(f"{exc}. Pick 'mock' to run offline.")
    st.stop()

if st.button("Process request", type="primary", disabled=not text.strip()):
    with st.spinner(f"Reading the request with {llm.label}..."):
        try:
            _, run_dir = start_quote(text, settings=settings, catalog=catalog, llm=llm,
                                     out_root=DEFAULT_OUTPUT_DIR, today=today)
            st.session_state["run_dir"] = str(run_dir)
        except LLMError as exc:
            st.error(str(exc))

if "run_dir" not in st.session_state:
    st.stop()

run_dir = Path(st.session_state["run_dir"])
state = load_state(run_dir)
colour = {"NEEDS_CLARIFICATION": "orange", "BLOCKED": "red", "PENDING_APPROVAL": "blue",
          "APPROVED": "green", "REJECTED": "gray"}[state.status]
st.subheader(f"{state.quote_no} · :{colour}[{state.status}]")

# -- 2. extraction -------------------------------------------------------------------
ex = state.extraction
c1, c2, c3 = st.columns(3)
c1.metric("Client", ex.client.name or "?", ex.client.company or "")
c2.metric("Deadline", str(ex.deadline or "not given"), ex.deadline_text or "")
c3.metric("Language", ex.language, state.provider)
st.dataframe(pd.DataFrame([i.model_dump() for i in ex.items]), width="stretch", hide_index=True)

# -- 3a. clarification ---------------------------------------------------------------
if state.status == "NEEDS_CLARIFICATION":
    st.warning("The agent won't guess. Send these questions first:")
    st.text_area("Clarification email draft", (run_dir / "clarification_email.txt").read_text(), height=260)
    reply = st.text_area("Paste the client's reply")
    if st.button("Continue with reply", disabled=not reply.strip()):
        continue_with_reply(run_dir, reply, settings=settings, catalog=catalog, llm=llm, today=today)
        st.rerun()
    st.stop()

# -- 3b. pricing + checks -----------------------------------------------------------------
q = state.quote
rows = [{"#": l.line_no, "Product": l.name, "Term": l.billing_term, "Qty": l.quantity, "Supplier": l.supplier,
         "Unit cost": float(l.unit_cost), "Unit price": float(l.unit_price), "Disc %": float(l.discount_rate * 100),
         "Net": float(l.net_amount), "Tax": float(l.tax_amount), "Total": float(l.line_total),
         "Margin %": round(float(l.margin_pct * 100), 2), "Lead days": l.lead_time_days} for l in q.lines]
st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
t = q.totals
m1, m2, m3, m4 = st.columns(4)
m1.metric("Net", f"{q.currency} {t.net_total:,.2f}")
m2.metric(settings.tax.name, f"{t.tax_total:,.2f}")
m3.metric("Grand total", f"{t.grand_total:,.2f}")
m4.metric("Margin", f"{t.margin_total:,.2f}", f"{t.margin_pct:.1%}")
for u in q.unpriced:
    st.info(f"Not priced: **{u.requested_text}** - {u.reason}")

st.markdown("#### Checks")
for c in state.checks:
    {"block": st.error, "warn": st.warning, "info": st.info}[c.severity](f"`{c.code}` {c.message}")
if not state.checks:
    st.success("All checks passed.")

pdf = run_dir / (f"{state.quote_no}.pdf" if state.status == "APPROVED" else "quote_DRAFT.pdf")
if pdf.exists():
    st.download_button("Download PDF", pdf.read_bytes(), file_name=pdf.name, mime="application/pdf")

# -- 4. approval ---------------------------------------------------------------------------
if state.status == "PENDING_APPROVAL":
    if st.button("✅ Approve, generate final PDF and push to HubSpot", type="primary", disabled=not approver.strip()):
        try:
            approve_quote(run_dir, approver, settings=settings, llm=llm,
                          crm_dry_run={"auto": None, "dry-run": True, "live": False}[crm_mode])
            st.rerun()
        except ApprovalError as exc:
            st.error(str(exc))
    if not approver.strip():
        st.caption("Enter your name in the sidebar to approve.")
elif state.status == "APPROVED":
    st.success(f"Approved by {state.approved_by}. CRM: {state.crm.get('mode')} - "
               f"{'ok' if state.crm.get('ok') else state.crm.get('error')}")
    st.text_area("Cover email", (run_dir / "quote_email.txt").read_text(), height=200)
    with st.expander("HubSpot requests"):
        st.code((run_dir / "hubspot_requests.json").read_text(), language="json")
