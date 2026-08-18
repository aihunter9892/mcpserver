"""Tests for server wiring: model fallback, tool schemas, config validation.

The LLM client is stubbed, so these run offline with no API key and no spend.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def srv(monkeypatch):
    """Import server.py with a fake key so it boots without real credentials."""
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_not_a_real_key")
    for mod in ("server", "guards"):
        sys.modules.pop(mod, None)
    import server

    return server


# ---------------------------------------------------------------------------
# Model fallback - the failure that actually bit us in development
# ---------------------------------------------------------------------------

def test_model_chain_starts_with_primary(srv):
    assert srv.MODEL_CHAIN[0] == srv.MODEL


def test_model_chain_has_no_duplicates(srv):
    assert len(srv.MODEL_CHAIN) == len(set(srv.MODEL_CHAIN))


def test_missing_model_is_detected(srv):
    assert srv._is_missing_model(Exception("Error code: 404 - model gone"))
    assert srv._is_missing_model(Exception("The model does not exist"))
    assert not srv._is_missing_model(Exception("401 invalid api key"))


def test_falls_back_when_primary_model_is_retired(srv, monkeypatch):
    """A 404 on the primary model must roll to the next one, not fail the tool."""
    tried = []

    def fake_call(model, prompt, system, max_tokens):
        tried.append(model)
        if model == srv.MODEL_CHAIN[0]:
            raise Exception("Error code: 404 - model does not exist")
        return "fallback answer"

    monkeypatch.setattr(srv, "_one_call", fake_call)
    assert srv.call_llm("hi") == "fallback answer"
    assert tried == srv.MODEL_CHAIN[:2]


def test_non_model_errors_do_not_trigger_fallback(srv, monkeypatch):
    """A bad key must fail fast, not burn every model in the chain."""
    tried = []

    def fake_call(model, prompt, system, max_tokens):
        tried.append(model)
        raise Exception("401 authentication_error")

    monkeypatch.setattr(srv, "_one_call", fake_call)
    result = srv.call_llm("hi")
    assert len(tried) == 1
    assert "API error" in result


def test_api_errors_return_text_not_exceptions(srv, monkeypatch):
    """Tools must degrade to a readable message, never crash the session."""
    monkeypatch.setattr(srv, "_one_call",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("boom")))
    assert "API error" in srv.call_llm("hi")


# ---------------------------------------------------------------------------
# Tool contract
# ---------------------------------------------------------------------------

def test_guard_preserves_signature(srv):
    """The @_guarded wrapper must not erase the schema MCP builds from it."""
    import inspect

    params = inspect.signature(srv.ask_llm).parameters
    assert "question" in params
    assert "style" in params


def test_oversized_input_returns_message_not_exception(srv):
    huge = "x" * (srv.MAX_INPUT_CHARS + 10)
    assert srv.summarize(huge).startswith("Input rejected:")


def test_bullets_are_clamped(srv, monkeypatch):
    """Out-of-range bullet counts must be clamped, not passed through."""
    seen = {}

    def capture(prompt, system="", max_tokens=1024):
        seen["system"] = system
        return "ok"

    monkeypatch.setattr(srv, "call_llm", capture)
    srv.summarize("text", bullets=999)
    assert "exactly 10" in seen["system"]

    srv.summarize("text", bullets=-5)
    assert "exactly 1" in seen["system"]


def test_server_info_reports_live_config(srv):
    info = srv.server_info()
    assert srv.PROVIDER in info
    assert srv.MODEL in info


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_unknown_provider_exits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-vendor")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    sys.modules.pop("server", None)
    with pytest.raises(SystemExit):
        import server  # noqa: F401


def test_missing_key_exits(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    sys.modules.pop("server", None)
    with pytest.raises(SystemExit):
        import server  # noqa: F401
