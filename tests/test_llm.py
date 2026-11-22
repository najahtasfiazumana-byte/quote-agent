"""Gemini HTTP behaviour, tested with a fake `requests.post` (no network, no key needed)."""

import pytest
import requests

from quote_agent import llm as llm_mod
from quote_agent.llm import GeminiLLM, LLMError


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


OK = {"candidates": [{"content": {"parts": [
    {"text": "thinking...", "thought": True},
    {"text": '{"a": 1}'},
]}}]}


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    return GeminiLLM()


def test_sends_low_thinking_and_skips_thought_parts(gemini, monkeypatch):
    sent = []
    monkeypatch.setattr(llm_mod.requests, "post", lambda url, **kw: sent.append(kw) or FakeResp(200, OK))
    assert gemini.complete_json("sys", "user") == {"a": 1}
    assert sent[0]["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert sent[0]["headers"]["x-goog-api-key"] == "test-key"
    assert gemini.model == "gemini-3.6-flash"


def test_retries_without_thinking_if_model_rejects_it(gemini, monkeypatch):
    sent = []

    def fake(url, **kw):
        sent.append("thinkingConfig" in kw["json"]["generationConfig"])
        return FakeResp(400, {"error": "unknown field"}) if sent[-1] else FakeResp(200, OK)

    monkeypatch.setattr(llm_mod.requests, "post", fake)
    assert gemini.complete_json("sys", "user") == {"a": 1}
    assert sent == [True, False]


def test_timeout_retries_once_then_gives_clear_error(gemini, monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(1)
        raise requests.Timeout("read timed out")

    monkeypatch.setattr(llm_mod.requests, "post", fake)
    with pytest.raises(LLMError, match="No answer from .* within"):
        gemini.complete_text("sys", "user")
    assert len(calls) == 2


def test_timeout_then_success(gemini, monkeypatch):
    replies = [requests.Timeout("slow"), FakeResp(200, OK)]

    def fake(url, **kw):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(llm_mod.requests, "post", fake)
    assert gemini.complete_json("sys", "user") == {"a": 1}
