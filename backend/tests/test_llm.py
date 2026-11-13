import json
import os
import shutil
import subprocess

import pytest

from app.services import llm
from app.services.llm import ClaudeCliClient, LLMError

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


@pytest.fixture
def fake_cli(monkeypatch):
    """Pretend the CLI exists and capture the subprocess call."""
    monkeypatch.setattr(llm.shutil, "which", lambda path: f"/fake/{path}")
    calls = {}

    def install(stdout="", stderr="", returncode=0, exc=None):
        def fake_run(command, **kwargs):
            calls["command"] = command
            calls["kwargs"] = kwargs
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        monkeypatch.setattr(llm.subprocess, "run", fake_run)
        return calls

    return install


def _result(**overrides):
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"answer": 4}',
        "structured_output": {"answer": 4},
        "duration_ms": 1000,
        "usage": {"output_tokens": 10},
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_returns_structured_output_and_builds_safe_command(fake_cli):
    calls = fake_cli(stdout=_result())
    out = ClaudeCliClient().generate_json(
        system="sys", prompt="2+2?", schema=SCHEMA, model="claude-haiku-5-5", effort="low"
    )
    assert out == {"answer": 4}

    cmd = calls["command"]
    assert cmd[0] == "/fake/claude" and "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert "--no-session-persistence" in cmd
    # The prompt goes through stdin, not the command line (Windows length limit).
    assert calls["kwargs"]["input"] == "2+2?"


def test_api_key_is_removed_from_child_env(fake_cli, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    calls = fake_cli(stdout=_result())
    ClaudeCliClient().generate_json(system="s", prompt="p", schema=SCHEMA)
    assert "ANTHROPIC_API_KEY" not in calls["kwargs"]["env"]


def test_falls_back_to_result_text_when_no_structured_output(fake_cli):
    fake_cli(stdout=_result(structured_output=None, result='{"answer": 7}'))
    assert ClaudeCliClient().generate_json(system="s", prompt="p", schema=SCHEMA) == {"answer": 7}


def test_cli_error_result_raises(fake_cli):
    fake_cli(stdout=_result(is_error=True, subtype="error_during_execution", result="rate limited"))
    with pytest.raises(LLMError, match="rate limited"):
        ClaudeCliClient().generate_json(system="s", prompt="p", schema=SCHEMA)


def test_non_json_output_raises_with_stderr(fake_cli):
    fake_cli(stdout="", stderr="Not logged in", returncode=1)
    with pytest.raises(LLMError, match="Not logged in"):
        ClaudeCliClient().generate_json(system="s", prompt="p", schema=SCHEMA)


def test_timeout_raises(fake_cli):
    fake_cli(exc=subprocess.TimeoutExpired(cmd="claude", timeout=5))
    with pytest.raises(LLMError, match="timed out"):
        ClaudeCliClient(timeout_seconds=5).generate_json(system="s", prompt="p", schema=SCHEMA)


def test_missing_cli_raises(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda path: None)
    with pytest.raises(LLMError, match="not found"):
        ClaudeCliClient(cli_path="nope").generate_json(system="s", prompt="p", schema=SCHEMA)


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_CLAUDE_CLI_TESTS") != "1" or shutil.which("claude") is None,
    reason="set RUN_CLAUDE_CLI_TESTS=1 to call the real Claude CLI (uses your subscription)",
)
def test_real_cli_call():
    out = ClaudeCliClient().generate_json(
        system="Answer the arithmetic question.",
        prompt="What is 2 + 2?",
        schema=SCHEMA,
        model="claude-haiku-5-5",
    )
    assert out == {"answer": 4}
