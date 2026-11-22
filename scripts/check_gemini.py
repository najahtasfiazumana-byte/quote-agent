"""Quick Gemini connection check: which models answer, and how fast?

    python scripts/check_gemini.py            # test a few likely models
    python scripts/check_gemini.py --list     # list models your key can use
    python scripts/check_gemini.py gemini-3.6-flash

Reads GEMINI_API_KEY from .env. The key is never printed.
"""

import os
import sys
import time

import requests
from dotenv import load_dotenv

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
TIMEOUT = 45

load_dotenv()
key = os.getenv("GEMINI_API_KEY", "").strip()
if not key:
    sys.exit("GEMINI_API_KEY is empty. Put it in .env first.")
print(f"Key loaded: starts with {key[:3]}..., {len(key)} characters\n")
headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

if "--list" in sys.argv:
    try:
        r = requests.get(f"{BASE}/models", headers=headers, params={"pageSize": 200}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        sys.exit(f"Could not reach Google: {type(exc).__name__}")
    if r.status_code != 200:
        sys.exit(f"HTTP {r.status_code}: {r.text[:300]}")
    for m in r.json().get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []):
            print(m["name"].replace("models/", ""))
    sys.exit(0)

models = [a for a in sys.argv[1:] if not a.startswith("-")] or DEFAULT_MODELS
for model in models:
    for label, config in (("thinking=low", {"thinkingConfig": {"thinkingLevel": "low"}}), ("default", {})):
        body = {"contents": [{"role": "user", "parts": [{"text": "Reply with just the word OK."}]}],
                "generationConfig": config}
        start = time.time()
        try:
            r = requests.post(f"{BASE}/models/{model}:generateContent", headers=headers, json=body, timeout=TIMEOUT)
            took = time.time() - start
            if r.status_code == 200:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                print(f"OK    {model:24s} {label:13s} {took:5.1f}s  -> {text[:40]!r}")
            else:
                msg = r.json().get("error", {}).get("message", r.text) if r.content else ""
                print(f"FAIL  {model:24s} {label:13s} {took:5.1f}s  HTTP {r.status_code}: {msg[:120]}")
        except requests.RequestException as exc:
            print(f"FAIL  {model:24s} {label:13s} {time.time() - start:5.1f}s  {type(exc).__name__} (no answer)")

print("\nPick a model marked OK and set it in .env, e.g.  GEMINI_MODEL=gemini-3.6-flash")
print("If only 'default' works, also add:              GEMINI_THINKING_LEVEL=")
