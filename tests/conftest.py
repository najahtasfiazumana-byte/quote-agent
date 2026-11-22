from datetime import date
from pathlib import Path

import pytest

from quote_agent.catalog import load_catalog
from quote_agent.config import load_settings
from quote_agent.llm import LLM, MockLLM

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 21)


@pytest.fixture
def settings():
    return load_settings(ROOT / "config.yaml")


@pytest.fixture
def catalog():
    return load_catalog(ROOT / "data")


@pytest.fixture
def mock_llm():
    return MockLLM()


@pytest.fixture
def sample():
    def _read(name: str) -> str:
        return (ROOT / "data" / "requests" / name).read_text(encoding="utf-8")
    return _read


class ScriptedLLM(LLM):
    """Returns canned responses in order - lets tests exercise the real LLM code paths."""

    name = "scripted"

    def __init__(self, responses):
        super().__init__("test")
        self.responses = list(responses)
        self.calls = []

    def complete_text(self, system, user, json_mode=False):
        self.calls.append(user)
        return self.responses.pop(0)
