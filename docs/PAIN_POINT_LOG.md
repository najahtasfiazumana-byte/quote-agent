# Pain-point log: by hand first, then automate

> **How to use this file:** before trusting the agent, quote the five sample requests in
> `data/requests/` **by hand**, using only the CSV price lists, a spreadsheet and an email client.
> Time each step and write down what went wrong or felt risky. Fill in the `My time` and
> `What happened` columns with your own numbers. Those are the honest numbers to quote in an
> interview. The other columns describe how the agent handles each step.

## Manual run

| # | Step | My time (min) | What happened / what felt risky | Automated? | How the agent handles it |
|---|---|---|---|---|---|
| 1 | Read the request, list products and quantities | | | ✅ | LLM extraction to JSON, checked against the catalog |
| 2 | Work out what's missing (plan? billing term? model?) | | | ✅ | `clarify.py` rules, so it never guesses |
| 3 | Write the clarifying email | | | ✅ (draft) | Drafted in the client's language. A human sends it |
| 4 | Re-read the thread when the client replies | | | ✅ | `quote-agent reply` re-reads the whole thread under the same quote number |
| 5 | Look up each SKU in every distributor's price list | | | ✅ | All suppliers compared automatically |
| 6 | Check price validity, stock, minimum order, lead time vs deadline | | | ✅ | Supplier-selection rules, with reasons logged |
| 7 | Calculate sell price, discount, tax, totals | | | ✅ | `Decimal` maths, then an independent recompute |
| 8 | Double-check the maths | | | ✅ | `RECOMPUTE_MISMATCH` blocks on a 1-sen difference |
| 9 | Make the quote document | | | ✅ | PDF from a template, with no supplier data on it |
| 10 | Get approval | | | 👤 | Still human, on purpose. One click or one command |
| 11 | Write the cover email | | | ✅ (draft) | LLM wording, number guard, template fallback |
| 12 | Update the CRM (contact, deal, amount, stage, attach PDF) | | | ✅ | HubSpot API |
| 13 | Find the quote again later / explain how a price was chosen | | | ✅ | `outputs/<quote_no>/`, `internal_costing.csv`, `audit.jsonl` |

**Totals:** by hand ____ min per quote → with the agent ____ min (review and approve only).

## Pain points I expect you'll notice (check them against your own run)

- **Ambiguity is the slowest part, not the maths.** "Microsoft 365 for 25 staff" could mean three
  plans and two billing terms. Guessing wrong means re-quoting.
- **The cheapest supplier isn't always the right one.** An expired price list, a minimum order of 10,
  stock of 0, or a 21-day lead time against a "this week" deadline.
- **Copying numbers between a spreadsheet and a document** is where typos happen.
- **Rounding.** Should the discount be rounded per line or on the total? Different choices give
  totals that differ by a few sen, and clients notice.
- **The CRM is updated last, or never,** because it's the most boring step.
- **Weeks later, nobody remembers why supplier X was chosen.**

## Still manual (on purpose)

- Final approval and pressing *send*: a person is accountable for every quote.
- Sourcing products that aren't in the catalog (they're flagged as `MANUAL_SOURCING`).
- Negotiating special pricing with a distributor for large deals.
