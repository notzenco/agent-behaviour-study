"""Agent runners.

Two kinds of agent run in this study:

* :class:`ExternalAgent` starts a real coding agent as a subprocess. It is
  what a real study uses, and it costs money.
* :class:`abstudy.mock_agent.MockAgent` is a scripted stand-in that reads,
  leaks or ignores credentials with configurable probabilities. It is what
  the offline demo uses, and it costs nothing and proves nothing about real
  agents.

Both take the same :class:`TrialContext` and return the same
:class:`AgentOutcome`, so the harness does not know which one it is running.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from abstudy.tripwire import PlantInfo, TripwireBackend

__all__ = ["Agent", "AgentOutcome", "ExternalAgent", "TrialContext"]


@dataclass(frozen=True)
class TrialContext:
    """Everything an agent is allowed to see."""

    trial_id: str
    repo: Path
    home: Path
    transcripts: Path
    prompt: str
    plant: PlantInfo
    listen_port: int | None
    tripwire: TripwireBackend
    seed: int
    factors: dict[str, str] = field(default_factory=dict)


@dataclass
class AgentOutcome:
    """What happened when an agent ran."""

    exit_code: int
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


class Agent(Protocol):
    """The interface the harness drives."""

    key: str

    def run(self, ctx: TrialContext) -> AgentOutcome: ...


@dataclass
class ExternalAgent:
    """Runs a real coding agent as a subprocess.

    The command may contain these placeholders, which are substituted before
    the agent starts:

    ``{prompt_file}``
        Path to a file holding the exact prompt.
    ``{prompt}``
        The prompt itself, on one line.
    ``{repo}``
        The task repository, which is also the working directory.
    ``{home}``
        The sandbox home directory.
    ``{transcripts}``
        The directory the harness collects transcripts from.
    ``{settings_file}``
        Path to an agent settings file wired up to the tripwire hook.
    """

    key: str
    command: tuple[str, ...]
    timeout: float = 900.0
    env: tuple[tuple[str, str], ...] = ()
    stdin: str | None = None

    def run(self, ctx: TrialContext) -> AgentOutcome:
        substitutions = {
            "prompt_file": str(ctx.repo.parent / "prompt.txt"),
            "prompt": ctx.prompt.replace("\n", " "),
            "repo": str(ctx.repo),
            "home": str(ctx.home),
            "transcripts": str(ctx.transcripts),
            "settings_file": str(ctx.home / ".claude" / "settings.json"),
            "model": ctx.factors.get("model", ""),
        }
        command = [part.format(**substitutions) for part in self.command]
        stdin = self.stdin.format(**substitutions) if self.stdin else None
        env = dict(os.environ)
        env["HOME"] = str(ctx.home)
        env["XDG_CONFIG_HOME"] = str(ctx.home / ".config")
        env["XDG_DATA_HOME"] = str(ctx.home / ".local" / "share")
        env["XDG_STATE_HOME"] = str(ctx.home / ".local" / "state")
        env["TRIPWIRE_STATE_DIR"] = str(ctx.repo.parent / "state")
        for key, value in self.env:
            env[key] = value.format(**substitutions)

        started = time.monotonic()
        try:
            proc = subprocess.run(  # noqa: S603 - argv comes from the study config
                command,
                input=stdin,
                capture_output=True,
                text=True,
                cwd=ctx.repo,
                env=env,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            return AgentOutcome(
                exit_code=127,
                duration_s=time.monotonic() - started,
                error=f"agent command not found: {command[0]}",
            )
        except subprocess.TimeoutExpired:
            return AgentOutcome(
                exit_code=124,
                duration_s=time.monotonic() - started,
                error=f"agent timed out after {self.timeout}s",
            )
        return AgentOutcome(
            exit_code=proc.returncode,
            duration_s=time.monotonic() - started,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


def write_prompt(path: Path, prompt: str) -> None:
    """Write the exact prompt given to an agent."""
    path.write_text(prompt if prompt.endswith("\n") else prompt + "\n")


def merge_agent_settings(path: Path, settings: dict[str, Any]) -> None:
    """Merge a settings document into a file, creating it if needed."""
    import json

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}
    merged = {**existing, **settings}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n")
