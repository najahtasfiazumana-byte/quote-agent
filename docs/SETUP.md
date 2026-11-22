# Setup (all free)

## 1. Python

Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev,ui]"
pytest                             # should print: 42 passed
cp .env.example .env               # Windows: copy .env.example .env
```

## 2. Pick an LLM

### Gemini (free tier), recommended to start

1. Go to <https://aistudio.google.com/apikey> and create an API key.
2. In `.env`, set `GEMINI_API_KEY=...` and `QUOTE_AGENT_PROVIDER=gemini`.
3. Test it: `quote-agent run data/requests/02_messy_request.txt --no-prompt`

Notes:
- Free-tier limits vary by model and can be checked in AI Studio. Daily quotas reset at midnight
  Pacific time (4pm Malaysia time).
- The default model is `gemini-3.6-flash` with `GEMINI_THINKING_LEVEL=low` (fast). To change the model, set `GEMINI_MODEL`. Check
  <https://ai.google.dev/pricing> for models that have a free tier.
- Free-tier prompts may be used to improve Google's products. Only use fictional or sample data.

### Claude

1. Create a key at <https://platform.claude.com> (new accounts get a small amount of free credit).
2. In `.env`, set `ANTHROPIC_API_KEY=...` and `QUOTE_AGENT_PROVIDER=claude`.
3. The default model is `claude-haiku-5-5` (cheap and fast). To change it, set `CLAUDE_MODEL`.

A full quote is only a few short calls on a small model, so the cost per quote is very low. Check current pricing on the Claude website.

### Ollama (local, free, private)

1. Install from <https://ollama.com>.
2. Run `ollama pull llama3.1` (or any model you like, then set `OLLAMA_MODEL`).
3. In `.env`, set `QUOTE_AGENT_PROVIDER=ollama`.

Smaller local models make more extraction mistakes. The catalog normalisation and clarification
rules catch most of them, which makes this a good way to demo the safeguards.

### No model

Use `--provider mock`, or leave every key empty. This is the offline keyword parser, used for tests and demos.

## 3. HubSpot (free CRM), optional

Without a token, every approval writes `hubspot_requests.json` (dry-run) so you can see exactly what
would be sent.

To push for real:

1. Create a free account at <https://www.hubspot.com/products/crm>.
2. Create a **private app**. In current portals this is under *Development → Legacy apps → Create →
   Private*. In older portals it's *Settings → Integrations → Private Apps*.
3. Give it these scopes:
   - `crm.objects.contacts.read`, `crm.objects.contacts.write`
   - `crm.objects.deals.read`, `crm.objects.deals.write`
   - `crm.objects.line_items.read`, `crm.objects.line_items.write`
   - `files`
   If a call fails with a missing-scope error, HubSpot's message names the scope to add.
4. Copy the access token into `.env` as `HUBSPOT_TOKEN=...`.
5. Approve a quote: `quote-agent approve outputs/Q-... --approver "Your Name"`. Then open the deal in
   HubSpot and you'll see the line items and a note with the PDF attached.

The deal stage comes from `hubspot.deal_stage_quote_sent` in `config.yaml`. The default pipeline's
stage IDs are listed there. Custom pipelines use their own IDs (find them under *Settings → Objects →
Deals → Pipelines*).

## 4. Streamlit review screen

```bash
streamlit run app.py
```

Opens at <http://localhost:8501>. Generated quotes are saved to `outputs/` either way.

## Troubleshooting

| Problem | Fix |
|---|---|
| `GEMINI_API_KEY is not set` | Create `.env` from `.env.example`, or use `--provider mock` |
| `429` / quota errors on Gemini | Free-tier limit reached. Wait for the reset, or switch provider |
| `No answer ... within 180s` | Slow network or a busy model. Try again, or raise `QUOTE_AGENT_LLM_TIMEOUT` |
| `Extraction failed after retry` | The model returned invalid JSON twice. Try a larger model |
| Everything is `BLOCKED` with "price list expired" | Update `valid_until` in `data/suppliers/*.csv` |
| HubSpot `403` | Missing scope on the private app (see the list above) |
