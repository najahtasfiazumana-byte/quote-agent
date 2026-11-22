"""Command line interface.

    quote-agent run data/requests/01_clear_request.txt
    quote-agent reply outputs/Q-20261121-AB12 data/requests/02_followup_reply.txt
    quote-agent approve outputs/Q-20261121-AB12 --approver "Najah"
    quote-agent reject  outputs/Q-20261121-AB12 --approver "Najah" --reason "client cancelled"
    quote-agent demo            # every sample request, offline, no API key needed
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .catalog import load_catalog
from .config import DEFAULT_CONFIG, DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, load_settings
from .llm import LLMError, get_llm
from .models import QuoteState
from .pipeline import (
    ApprovalError,
    approve_quote,
    continue_with_reply,
    reject_quote,
    review_markdown,
    start_quote,
)


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _crm_flag(value: str) -> Optional[bool]:
    return {"auto": None, "dry-run": True, "live": False}[value]


def _print_result(state: QuoteState, run_dir: Path) -> None:
    print(review_markdown(state))
    print(f"Files: {run_dir}")
    for f in sorted(run_dir.iterdir()):
        print(f"  - {f.name}")
    next_step = {
        "NEEDS_CLARIFICATION": f"Send clarification_email.txt, then: quote-agent reply {run_dir} <reply.txt>",
        "BLOCKED": "Fix the BLOCK items above (prices, stock, catalog) and run again.",
        "PENDING_APPROVAL": f"Review quote_DRAFT.pdf, then: quote-agent approve {run_dir} --approver \"<your name>\"",
        "APPROVED": "Send quote_email.txt with the PDF attached.",
        "REJECTED": "Nothing to send.",
    }[state.status]
    print(f"\nNext: {next_step}")


def _maybe_approve(state, run_dir, args, settings, llm) -> QuoteState:
    if state.status != "PENDING_APPROVAL":
        return state
    if args.yes:
        approver = args.approver or getpass.getuser()
    elif sys.stdin.isatty() and not args.no_prompt:
        answer = input("\nApprove and send this quote? [y = approve / n = later]: ").strip().lower()
        if answer != "y":
            return state
        approver = args.approver or input("Approver name: ").strip() or getpass.getuser()
    else:
        return state
    return approve_quote(run_dir, approver, settings=settings, llm=llm, crm_dry_run=_crm_flag(args.crm))


def main(argv: Optional[list[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="quote-agent", description="AI request-to-quote agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to config.yaml")
    parser.add_argument("--data", default=str(DEFAULT_DATA_DIR), help="folder with catalog.csv and suppliers/")
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT_DIR), help="where quote folders are written")
    parser.add_argument("--provider", choices=["gemini", "claude", "ollama", "mock"],
                        help="LLM provider (default: QUOTE_AGENT_PROVIDER, else gemini if key set, else mock)")
    parser.add_argument("--model", help="override the model name")
    parser.add_argument("--today", type=date.fromisoformat, help="pretend today is YYYY-MM-DD (for demos/tests)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def approval_flags(p):
        p.add_argument("--approver", help="name recorded as the approver")
        p.add_argument("--crm", choices=["auto", "dry-run", "live"], default="auto",
                       help="auto = live if HUBSPOT_TOKEN is set, otherwise dry-run")

    p_run = sub.add_parser("run", help="process a new client request")
    p_run.add_argument("request", help="request text file, or - for stdin")
    p_run.add_argument("--followup", help="optional client reply to include in the same thread")
    p_run.add_argument("--yes", action="store_true", help="approve automatically if there are no blocking checks")
    p_run.add_argument("--no-prompt", action="store_true", help="never ask for approval interactively")
    approval_flags(p_run)

    p_reply = sub.add_parser("reply", help="add the client's answer to an existing quote and re-run")
    p_reply.add_argument("run_dir")
    p_reply.add_argument("reply", help="reply text file, or - for stdin")
    p_reply.add_argument("--yes", action="store_true")
    p_reply.add_argument("--no-prompt", action="store_true")
    approval_flags(p_reply)

    p_appr = sub.add_parser("approve", help="approve a PENDING_APPROVAL quote, render final PDF, push to CRM")
    p_appr.add_argument("run_dir")
    approval_flags(p_appr)

    p_rej = sub.add_parser("reject", help="reject a quote")
    p_rej.add_argument("run_dir")
    p_rej.add_argument("--approver", default=None)
    p_rej.add_argument("--reason", required=True)

    sub.add_parser("demo", help="run every sample request offline (mock provider)")

    args = parser.parse_args(argv)
    settings = load_settings(Path(args.config))
    catalog = load_catalog(Path(args.data))
    out_root = Path(args.out)

    try:
        llm = get_llm("mock" if args.cmd == "demo" else args.provider, args.model)
        print(f"[quote-agent] provider: {llm.label}\n")

        if args.cmd == "run":
            text = _read(args.request)
            if args.followup:
                text = f"{text.rstrip()}\n\n--- client reply ---\n{_read(args.followup).strip()}\n"
            state, run_dir = start_quote(text, settings=settings, catalog=catalog, llm=llm,
                                         out_root=out_root, today=args.today)
            state = _maybe_approve(state, run_dir, args, settings, llm)
            _print_result(state, run_dir)

        elif args.cmd == "reply":
            state, run_dir = continue_with_reply(Path(args.run_dir), _read(args.reply), settings=settings,
                                                 catalog=catalog, llm=llm, today=args.today)
            state = _maybe_approve(state, run_dir, args, settings, llm)
            _print_result(state, run_dir)

        elif args.cmd == "approve":
            approver = args.approver or getpass.getuser()
            state = approve_quote(Path(args.run_dir), approver, settings=settings, llm=llm,
                                  crm_dry_run=_crm_flag(args.crm))
            _print_result(state, Path(args.run_dir))
            if state.crm and not state.crm.get("ok"):
                print(f"\nCRM push failed: {state.crm.get('error')}")
                return 2

        elif args.cmd == "reject":
            state = reject_quote(Path(args.run_dir), args.approver or getpass.getuser(), args.reason)
            _print_result(state, Path(args.run_dir))

        elif args.cmd == "demo":
            samples = sorted(Path(args.data, "requests").glob("*.txt"))
            for path in samples:
                if "followup" in path.name:
                    continue
                state, run_dir = start_quote(path.read_text(encoding="utf-8"), settings=settings, catalog=catalog,
                                             llm=llm, out_root=out_root, today=args.today)
                followup = path.with_name(path.name.replace("_messy_request", "_followup_reply"))
                if state.status == "NEEDS_CLARIFICATION" and followup.exists() and followup != path:
                    state, run_dir = continue_with_reply(run_dir, followup.read_text(encoding="utf-8"),
                                                         settings=settings, catalog=catalog, llm=llm, today=args.today)
                print(f"{path.name:28s} -> {state.status:20s} {run_dir.name}"
                      + (f"  total {state.quote.currency} {state.quote.totals.grand_total:,.2f}" if state.quote else ""))
            print(f"\nOpen the folders in {out_root} to see PDFs, emails and review.md files.")
        return 0
    except (LLMError, ApprovalError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
