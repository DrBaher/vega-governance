"""
Executor uses STREAMING for the Anthropic call (messages.stream + get_final_message)
so MAX_TOKENS can exceed the non-streaming ~21k ceiling — needed for Sonnet's
thinking + output on complex tasks. The non-streaming client rejected large
max_tokens with "Streaming is required for operations that may take longer than
10 minutes."
"""

from __future__ import annotations

import types

import pytest


def _bare_executor(**attrs):
    from executor import AgentExecutor
    ex = object.__new__(AgentExecutor)
    for k, v in attrs.items():
        setattr(ex, k, v)
    return ex


class _FakeStream:
    def __init__(self, message, record):
        self._message = message
        self._record = record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        self._record["got_final_message"] = True
        return self._message


class _FakeMessages:
    def __init__(self, message, record):
        self._message = message
        self._record = record

    def stream(self, **kwargs):
        self._record["stream_called"] = True
        self._record["kwargs"] = kwargs
        return _FakeStream(self._message, self._record)

    def create(self, **kwargs):  # must NOT be used
        raise AssertionError("executor must stream, not call messages.create")


class _FakeClient:
    def __init__(self, message, record):
        self.messages = _FakeMessages(message, record)


@pytest.mark.asyncio
async def test_call_with_retry_streams(monkeypatch):
    record: dict = {}
    fake_msg = types.SimpleNamespace(content=[], usage=None)
    cfg = types.SimpleNamespace(
        EXTENDED_THINKING_ENABLED=True, THINKING_BUDGET_TOKENS=10000,
        MAX_TOKENS=32000, PROMPT_CACHING_ENABLED=False)
    ex = _bare_executor(client=_FakeClient(fake_msg, record), config=cfg)

    resp, err = await ex._call_with_retry(model="claude-sonnet-4-6",
                                          system="sys", messages=[{"role": "user", "content": "x"}])
    assert err is None and resp is fake_msg
    assert record.get("stream_called") and record.get("got_final_message")
    # max_tokens passed through to the stream call
    assert record["kwargs"]["max_tokens"] == 32000


def test_executor_source_uses_stream():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "executor.py").read_text()
    assert "self.client.messages.stream(" in src
    assert "get_final_message()" in src
    # the old non-streaming call is gone
    assert "self.client.messages.create(" not in src
