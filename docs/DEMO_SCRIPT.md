# 5-minute demo script

Before the call, run `pytest` and `quote-agent demo` once, clear `outputs/`, and open the
Streamlit app (`streamlit run app.py`) with your provider set to Gemini or Claude.

## 0:00 The problem (30 s)

"A reseller gets requests like this every day. Turning it into a correct quote by hand means reading,
asking follow-up questions, checking three distributor price lists, doing the maths, making a PDF and
updating the CRM. I did it by hand first. Here's my log of where the time and the risk were." Show
`docs/PAIN_POINT_LOG.md`.

## 0:30 Messy request, so the agent asks instead of guessing (1 min)

Load `02_messy_request.txt`, then click **Process request**.

- Point out that it understood 25 / 3 / 5 and "end of month" → 30 Nov.
- "It didn't guess *which* Microsoft 365 plan or *which* laptop. It drafted these questions."
- Paste `02_followup_reply.txt` and click **Continue with reply**. It keeps the same quote number.

## 1:30 Pricing and supplier choice (1 min)

- Show the pricing table: licences come from Puncak, laptops from Tropika. Each line gets its own best supplier.
- Open `internal_costing.csv`: "Lautan was cheapest for Microsoft 365, but its price list had expired,
  so it was skipped automatically. Puncak has a minimum order of 10, and 25 qualifies. For laptops, Lautan was cheapest but has 0 in stock."
- Point out the margin per line and the 1.5% volume discount on 25 seats.

## 2:30 Accuracy (1 min)

- "No LLM maths. `Decimal`, rounded half-up at the same points a person would round."
- "Then `checks.py` recalculates everything independently with exact fractions. A 1-sen difference blocks the quote."
- Show `test_recompute_catches_a_tampered_total` and `test_m365_line_worked_example` (a hand-checked example).
- "If the LLM puts a number in an email that it wasn't given, the draft is thrown away."

## 3:30 Approve, PDF and CRM (1 min)

- Enter your name and click **Approve**. Show the final PDF: no supplier names or costs on it.
- Show the HubSpot deal (live) or `hubspot_requests.json` (dry-run): contact, deal with amount and stage, line items, PDF on a note.
- Show `audit.jsonl`: every step, and who approved.

## 4:30 Edge cases and what's next (30 s)

- `03_rush_order.txt`: deadline risk, plus the manager-approval threshold.
- `04_unknown_product.txt`: AutoCAD isn't in the catalog, so it's quoted separately without delaying the rest.
- `05_bahasa_mix.txt`: a Bahasa Malaysia request gets a Bahasa Malaysia reply.
- Next steps: Gmail/WhatsApp intake through n8n, price lists from Google Sheets or distributor APIs, POs to suppliers after acceptance, renewal reminders from annual lines.

## Likely questions

- **Why not let the LLM do the maths?** Because it's sometimes wrong, and it's wrong confidently. Code
  is cheaper, faster and testable.
- **What if the LLM maps to the wrong SKU?** The SKU must exist in the catalog, generic wording forces a
  question, and a human reviews the extracted items on the review screen before approving.
- **How does this scale to more suppliers?** Drop another CSV into `data/suppliers/`, or replace the
  loader with a Google Sheets or API reader. Everything downstream stays the same.
- **Cost per quote?** RM0 on the Gemini free tier or Ollama. On Claude it's a few short calls on a small model, so the cost per quote is very low.
