"""LLM layer (docs/15 §3.2). Unit tests never reach a network: the providers are faked."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

from boomerang import llm
from boomerang.config import get_settings
from boomerang.events import Emitter
from boomerang.models import Event
from boomerang.redact import find_secrets

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


class Toy(BaseModel):
    analysis: str
    answer: int


@pytest.fixture
def events() -> tuple[Emitter, list[Event]]:
    seen: list[Event] = []
    return Emitter("t", [seen.append]), seen


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch):
    """Fake Ollama call: records what the provider saw, answers Toy(answer=5)."""
    calls: list[dict[str, Any]] = []

    def fake(settings, system, user, schema, label):
        calls.append({"system": system, "user": user, "label": label})
        return schema(analysis="2 + 3", answer=5), {"in": 12, "out": 7}

    monkeypatch.setattr(llm, "_call_ollama", fake)
    return calls


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _ask(label: str = "toy", user: str = "What is 2 + 3?", **kw) -> Toy:
    return llm.structured(system="Answer in JSON.", user=user, schema=Toy, label=label, **kw)


def _cache_files() -> list[Path]:
    return sorted(get_settings().cache_dir.glob("*.json"))


# ---------- cache ----------

def test_record_then_served_from_cache_and_label_is_not_in_the_key(provider, events):
    emitter, seen = events
    assert _ask("first", emit=emitter).answer == 5
    assert _ask("second label", emit=emitter).answer == 5
    assert len(provider) == 1 and len(_cache_files()) == 1
    assert [e.data["cached"] for e in seen] == [False, True]
    assert seen[0].message == "first: 0.0 s · 12→7 tokens · cached=False"
    assert seen[1].message.startswith("second label: ") and seen[1].message.endswith("12→7 tokens · cached=True")
    assert seen[0].stage == "delock" and seen[0].data["model"] == "qwen2.5-coder:7b"


def test_cache_file_content(provider):
    _ask("toy")
    entry = json.loads(_cache_files()[0].read_text(encoding="utf-8"))
    assert {k: entry[k] for k in ("label", "provider", "model", "schema")} == {
        "label": "toy", "provider": "ollama", "model": "qwen2.5-coder:7b", "schema": "Toy"}
    assert entry["response"] == {"analysis": "2 + 3", "answer": 5}
    assert entry["usage"] == {"in": 12, "out": 7} and entry["user"] == "What is 2 + 3?"
    assert _cache_files()[0].stem == entry["key"] == llm.cache_key("ollama", "qwen2.5-coder:7b",
                                                                   "Answer in JSON.", "What is 2 + 3?", Toy)


def test_key_changes_with_prompt_model_and_schema():
    class Other(BaseModel):
        analysis: str
        answer: str

    base = llm.cache_key("ollama", "m", "s", "u", Toy)
    assert base != llm.cache_key("ollama", "m", "s", "u2", Toy)
    assert base != llm.cache_key("ollama", "m2", "s", "u", Toy)
    assert base != llm.cache_key("anthropic", "m", "s", "u", Toy)
    assert base != llm.cache_key("ollama", "m", "s", "u", Other)


def test_replay_misses_on_an_empty_cache(provider, monkeypatch):
    _set(monkeypatch, BOOMERANG_LLM_MODE="replay")
    with pytest.raises(llm.CacheMiss, match="toy"):
        _ask("toy")
    assert provider == []


def test_replay_serves_what_record_stored(provider, monkeypatch):
    _ask()
    _set(monkeypatch, BOOMERANG_LLM_MODE="replay")
    assert _ask().answer == 5 and len(provider) == 1
    with pytest.raises(llm.CacheMiss):
        _ask(user="A different question")


def test_live_always_calls_and_never_stores(provider, monkeypatch):
    _set(monkeypatch, BOOMERANG_LLM_MODE="live")
    _ask()
    _ask()
    assert len(provider) == 2 and _cache_files() == []


def test_unreadable_cache_entry_is_a_miss_with_a_warning(provider, events):
    emitter, seen = events
    _ask()
    _cache_files()[0].write_text("{not json", encoding="utf-8")
    assert _ask(emit=emitter).answer == 5
    assert len(provider) == 2 and seen[0].level == "warning" and "unreadable cache entry" in seen[0].message


# ---------- redaction ----------

def test_provider_and_cache_only_see_redacted_text(provider):
    user = f"DB_PASSWORD=hunter2hunter2\naws key {AWS_KEY}\ndef get_order(order_id):\n    return 1\r\n"
    _ask(user=user)
    seen = provider[0]["user"]
    assert "hunter2hunter2" not in seen and AWS_KEY not in seen and "\r" not in seen
    assert "DB_PASSWORD=<REDACTED:DB_PASSWORD>" in seen and "def get_order(order_id):" in seen
    raw = _cache_files()[0].read_text(encoding="utf-8")
    assert "hunter2hunter2" not in raw and AWS_KEY not in raw
    assert find_secrets(raw) == [] and find_secrets(json.loads(raw)["user"]) == []


def test_size_guard_warns_but_does_not_truncate(provider, events):
    emitter, seen = events
    user = "x = 1\n" * 4000                                        # ~24k chars ≈ 6.9k tokens
    _ask(user=user, emit=emitter)
    assert provider[0]["user"] == user
    assert seen[0].level == "warning" and "tokens" in seen[0].message


# ---------- Ollama ----------

class FakeOllamaClient:
    """Stands in for ``ollama.Client``; ``script`` is the list of answers (str) or exceptions to raise."""

    script: ClassVar[list[Any]] = []
    calls: ClassVar[list[dict[str, Any]]] = []
    init: ClassVar[dict[str, Any]] = {}

    def __init__(self, host=None, **kwargs):
        FakeOllamaClient.init = {"host": host, **kwargs}

    def chat(self, **kwargs):
        from ollama import ChatResponse, Message

        FakeOllamaClient.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        item = FakeOllamaClient.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResponse(message=Message(role="assistant", content=item), prompt_eval_count=10, eval_count=4)


@pytest.fixture
def ollama_client(monkeypatch: pytest.MonkeyPatch):
    import ollama

    FakeOllamaClient.script, FakeOllamaClient.calls, FakeOllamaClient.init = [], [], {}
    monkeypatch.setattr(ollama, "Client", FakeOllamaClient)
    return FakeOllamaClient


def test_ollama_call_shape(ollama_client):
    ollama_client.script = ['{"analysis": "sum", "answer": 5}']
    assert _ask().answer == 5
    assert ollama_client.init == {"host": "http://127.0.0.1:11434", "timeout": 180}
    call = ollama_client.calls[0]
    assert call["model"] == "qwen2.5-coder:7b" and call["format"] == Toy.model_json_schema()
    assert call["options"] == {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 2048}
    assert call["keep_alive"] == "60m"
    assert [m["role"] for m in call["messages"]] == ["system", "user"]


def test_ollama_validation_retry_once(ollama_client, events):
    emitter, seen = events
    ollama_client.script = ['{"analysis": "sum"}', '{"analysis": "sum", "answer": 5}']
    assert _ask(emit=emitter).answer == 5
    retry = ollama_client.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "user"]
    assert retry[2]["content"] == '{"analysis": "sum"}'
    assert retry[3]["content"].startswith("That JSON failed validation:") and "answer" in retry[3]["content"]
    assert retry[3]["content"].endswith("Return only JSON matching the schema.")
    assert seen[-1].data["tokens_in"] == 20 and seen[-1].data["tokens_out"] == 8      # both attempts count


def test_ollama_validation_fails_twice(ollama_client):
    ollama_client.script = ["not json", '{"answer": "five"}']
    with pytest.raises(llm.LLMError, match="failed validation twice") as info:
        _ask()
    assert not isinstance(info.value, llm.LLMUnavailable) and _cache_files() == []


def test_ollama_not_running(ollama_client):
    ollama_client.script = [ConnectionError("Failed to connect to Ollama")]
    with pytest.raises(llm.LLMUnavailable, match="ollama serve.*BOOMERANG_LLM_PROVIDER=anthropic"):
        _ask()


def test_ollama_model_not_pulled(ollama_client):
    import ollama

    ollama_client.script = [ollama.ResponseError("model not found", 404)]
    with pytest.raises(llm.LLMUnavailable, match="ollama pull qwen2.5-coder:7b"):
        _ask()


# ---------- Anthropic (fallback) ----------

def _anthropic_settings(monkeypatch):
    _set(monkeypatch, BOOMERANG_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-0000000000")
    return get_settings()


def _mock_http(body: dict[str, Any], captured: list[Any]):
    import anthropic
    import httpx2

    def handler(request):
        captured.append(request)
        return httpx2.Response(200, json=body)

    return anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))


def _message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 30, "output_tokens": 9}}


def test_anthropic_request_shape_and_parsed_output(monkeypatch):
    settings = _anthropic_settings(monkeypatch)
    captured: list[Any] = []
    http = _mock_http(_message('{"analysis": "sum", "answer": 5}'), captured)
    result, usage = llm._call_anthropic(settings, "sys", "user text", Toy, "toy", http_client=http)
    assert result == Toy(analysis="sum", answer=5) and usage == {"in": 30, "out": 9}
    request = captured[0]
    body = json.loads(request.content)
    assert "server-side-fallback-2026-07-01" in request.headers["anthropic-beta"]
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 16000 and body["fallbacks"] == "default"
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "user text"}]
    assert "temperature" not in body and "thinking" not in body
    assert "answer" in json.dumps(body.get("output_config") or body.get("output_format"))


@pytest.mark.parametrize("stop_reason, match", [("refusal", "refused"), ("max_tokens", "max_tokens")])
def test_anthropic_bad_stop_reasons(monkeypatch, stop_reason, match):
    settings = _anthropic_settings(monkeypatch)
    http = _mock_http(_message('{"analysis": "x", "answer": 1}', stop_reason), [])
    with pytest.raises(llm.LLMError, match=match):
        llm._call_anthropic(settings, "s", "u", Toy, "toy", http_client=http)


def test_anthropic_without_a_key_is_unavailable(monkeypatch):
    _set(monkeypatch, BOOMERANG_LLM_PROVIDER="anthropic")
    with pytest.raises(llm.LLMUnavailable, match="ANTHROPIC_API_KEY"):
        _ask()


def test_anthropic_provider_is_routed_and_cached_under_its_own_key(monkeypatch):
    calls: list[str] = []

    def fake(settings, system, user, schema, label):
        calls.append(settings.model)
        return schema(analysis="a", answer=5), {"in": 1, "out": 1}

    monkeypatch.setattr(llm, "_call_anthropic", fake)
    _anthropic_settings(monkeypatch)
    _ask()
    entry = json.loads(_cache_files()[0].read_text(encoding="utf-8"))
    assert calls == ["claude-opus-5-5"] and entry["provider"] == "anthropic"


# ---------- stable_text ----------

def test_stable_text_removes_run_dependent_parts(tmp_path):
    app = tmp_path / "runs" / "20261120-101500-ab12" / "leaseweb" / "app"
    (app / "app").mkdir(parents=True)
    win_app = str(app.resolve())
    text = (
        f'File "{win_app}\\app\\db.py", line 42, in get_order\r\n'
        f"{win_app.replace(chr(92), '/')}/app/db.py:42: in get_order\n"
        r'File "C:\Python312\Lib\site-packages\botocore\client.py", line 9, in _make_api_call' "\n"
        "/home/runner/work/x/conftest.py:10\n"
        "customer cust-3f2b8c1e-9a4d-4f6e-8b7a-1c2d3e4f5a6b not found; id 3f2b8c1e9a4d4f6e8b7a1c2d3e4f5a6b\n"
        "<app.db.Repo object at 0x000001D126BFF4A0>\n"
        "7 passed in 2.31s; took 3 seconds\n"
        "at 2026-11-20T10:15:00.123Z, ts 1763633700.25, run 20261120-101500-ab12\n"
        "GET http://127.0.0.1:8080/orders/42 → 404; see app/db.py:42\n"
    )
    out = llm.stable_text(text, app_dir=app)
    assert out == (
        'File "app/db.py", line 42, in get_order\n'
        "app/db.py:42: in get_order\n"
        'File "<path>/client.py", line 9, in _make_api_call\n'
        "<path>/conftest.py:10\n"
        "customer cust-<uuid> not found; id <uuid>\n"
        "<app.db.Repo object at <addr>>\n"
        "7 passed in <n>s; took <n>s\n"
        "at <ts>, ts <ts>, run <run>\n"
        "GET http://127.0.0.1:8080/orders/42 → 404; see app/db.py:42\n"
    )
    assert llm.stable_text(out, app_dir=app) == out


def test_stable_text_without_app_dir_still_hides_absolute_paths():
    assert llm.stable_text(r"C:\Users\someone\proj\app\db.py:3") == "<path>/db.py:3"


# ---------- real model ----------

@pytest.mark.llm
def test_real_toy_call_under_60s(monkeypatch):
    """Needs a running model: `pytest -m llm`. Done-when of C2 (docs/15 §3.2)."""
    _set(monkeypatch, BOOMERANG_LLM_MODE="live")
    start = time.perf_counter()
    result = _ask("llm-test", user="What is 2 + 3? Explain in one short sentence, then give the number.")
    assert result.answer == 5
    assert time.perf_counter() - start < 60
