"""LLM access through the Claude Code CLI in headless mode (`claude -p`).

Uses the Claude subscription the CLI is logged in with; no API key is needed.
Each call runs with no tools, no MCP servers, no user/project settings and no saved
session, so the model only sees the system prompt and the input we pass.

Note: subscription login is meant for personal use. A product offered to other
people must use an API key instead.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

# Removed from the child environment so the CLI always uses the subscription login.
_AUTH_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


class LLMError(ExternalServiceError):
    code = "llm_error"


class ClaudeCliClient:
    def __init__(
        self,
        cli_path: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.cli_path = cli_path or settings.claude_cli_path
        self.timeout_seconds = timeout_seconds or settings.claude_cli_timeout_seconds

    def _resolve_executable(self) -> str:
        resolved = shutil.which(self.cli_path)
        if resolved is None:
            raise LLMError(
                f"Claude CLI '{self.cli_path}' was not found. Install Claude Code and log in, "
                "or set CLAUDE_CLI_PATH."
            )
        return resolved

    def build_command(
        self, *, system: str, schema: dict[str, Any], model: str, effort: str | None
    ) -> list[str]:
        command = [
            self._resolve_executable(),
            "-p",
            "--output-format", "json",
            "--model", model,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", system,
            "--json-schema", json.dumps(schema, separators=(",", ":")),
        ]
        if effort:
            command += ["--effort", effort]
        return command

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """Send `prompt` (via stdin) and return the JSON object validated against `schema`."""
        settings = get_settings()
        model = model or settings.claude_model
        command = self.build_command(system=system, schema=schema, model=model, effort=effort)
        env = {k: v for k, v in os.environ.items() if k not in _AUTH_ENV_VARS}

        logger.info("Claude CLI request: model=%s, prompt=%d chars", model, len(prompt))
        # Run in an empty temp dir so the CLI never picks up project files or CLAUDE.md.
        with tempfile.TemporaryDirectory(prefix="clipgen-llm-") as workdir:
            try:
                proc = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    cwd=workdir,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(
                    f"Claude CLI timed out after {self.timeout_seconds} seconds."
                ) from exc
            except OSError as exc:
                raise LLMError(f"Could not start the Claude CLI: {exc}") from exc

        return self._parse_output(proc)

    @staticmethod
    def _parse_output(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
        stdout = (proc.stdout or "").strip()
        try:
            payload = json.loads(stdout) if stdout else None
        except json.JSONDecodeError:
            payload = None

        if not isinstance(payload, dict):
            detail = (proc.stderr or stdout or "no output").strip()[-500:]
            raise LLMError(f"Claude CLI failed (exit code {proc.returncode}): {detail}")

        if payload.get("is_error") or payload.get("subtype") != "success":
            message = str(payload.get("result") or payload.get("subtype") or "unknown error")
            raise LLMError(f"Claude CLI returned an error: {message[:500]}")

        structured = payload.get("structured_output")
        if structured is None:
            # Fall back to parsing the text result as JSON.
            try:
                structured = json.loads(payload.get("result") or "")
            except json.JSONDecodeError as exc:
                raise LLMError("Claude CLI response did not contain valid JSON.") from exc

        if not isinstance(structured, dict):
            raise LLMError("Claude CLI response JSON was not an object.")

        usage = payload.get("usage") or {}
        logger.info(
            "Claude CLI done in %s ms (output tokens: %s)",
            payload.get("duration_ms"), usage.get("output_tokens"),
        )
        return structured


def check_cli_version(cli_path: str, timeout: int = 20) -> str:
    """Return `claude --version` output; raises if the CLI is missing or broken."""
    resolved = shutil.which(cli_path)
    if resolved is None:
        raise RuntimeError(f"'{cli_path}' not found. Install Claude Code or set CLAUDE_CLI_PATH.")
    proc = subprocess.run(
        [resolved, "--version"], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "claude --version failed").strip()[:300])
    return proc.stdout.strip()
