"""Regenerate the committed sample outputs in examples/ (offline, mock provider, fixed date).

    python scripts/build_examples.py
"""

import shutil
from datetime import date
from pathlib import Path

from quote_agent.catalog import load_catalog
from quote_agent.config import DEFAULT_DATA_DIR, PROJECT_ROOT, load_settings
from quote_agent.llm import MockLLM
from quote_agent.pipeline import approve_quote, continue_with_reply, start_quote

TODAY = date(2026, 11, 21)
OUT = PROJECT_ROOT / "examples"
REQ = DEFAULT_DATA_DIR / "requests"

settings, catalog, llm = load_settings(), load_catalog(DEFAULT_DATA_DIR), MockLLM()
shutil.rmtree(OUT, ignore_errors=True)


def go(name, quote_no, reply=None, approve=True):
    state, run_dir = start_quote((REQ / name).read_text(encoding="utf-8"), settings=settings, catalog=catalog,
                                 llm=llm, out_root=OUT, today=TODAY, quote_no=quote_no)
    if reply:
        shutil.move(run_dir / "clarification_email.txt", run_dir / "clarification_email_sent.txt")
        state, run_dir = continue_with_reply(run_dir, (REQ / reply).read_text(encoding="utf-8"),
                                             settings=settings, catalog=catalog, llm=llm, today=TODAY)
    if approve and state.status == "PENDING_APPROVAL":
        state = approve_quote(run_dir, "Najah (demo)", settings=settings, llm=llm, crm_dry_run=True)
    print(f"{name:28s} -> {state.status}")


go("01_clear_request.txt", "Q-20261121-EX01")
go("02_messy_request.txt", "Q-20261121-EX02", reply="02_followup_reply.txt")
go("03_rush_order.txt", "Q-20261121-EX03", approve=False)  # left for the manager to review
go("04_unknown_product.txt", "Q-20261121-EX04")
go("05_bahasa_mix.txt", "Q-20261121-EX05")
