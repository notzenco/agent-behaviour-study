"""A thin, typed wrapper around the ``tripwire`` command line tool.

Only the commands the study needs are wrapped: planting, listing, removing,
scanning, reading the alert log, handling a hook event, and starting the
``watch`` and ``listen`` detectors.

The wrapper never prints a planted value. ``tripwire`` redacts the values in
its own output, and this module keeps it that way: the plant record carries
token *ids*, not token values.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "PlantInfo",
    "Tripwire",
    "TripwireBackend",
    "TripwireError",
    "default_tripwire_binary",
]

_PLANTED_RE = re.compile(r"^Planted (p-[a-z0-9]+) in (?P<root>.+)$", re.MULTILINE)

#: The kinds of credential ``tripwire plant --kind`` understands.
CREDENTIAL_KINDS = ("env", "aws", "npm", "ssh")


def _credential_value(line: str) -> str:
    """The credential part of a planted line.

    Planted files are ``KEY=value`` files, except for the SSH key, where the
    value sits inside a base64 block. Falling back to the whole line keeps
    the token inside the returned text either way, which is all the callers
    need.
    """
    _, separator, value = line.partition("=")
    if separator and value.strip():
        return value.strip()
    return line.strip()


class TripwireError(RuntimeError):
    """Raised when the ``tripwire`` CLI fails or cannot be found."""


@dataclass(frozen=True)
class PlantInfo:
    """A registered plant: where it lives and which token ids it holds."""

    id: str
    root: Path
    files: tuple[Path, ...]
    token_ids: tuple[str, ...]


class TripwireBackend(Protocol):
    """The subset of the tripwire interface the harness relies on."""

    def plant(self, root: Path, kinds: tuple[str, ...], listen: int | None = None) -> PlantInfo: ...

    def remove(self, plant_id: str) -> None: ...

    def scan(self, paths: tuple[Path, ...]) -> list[dict[str, Any]]: ...

    def planted_values(self, file: Path) -> list[str]: ...

    def alerts(self) -> list[dict[str, Any]]: ...

    def hook(self, event: dict[str, Any]) -> str: ...

    def hook_config(self, agent: str) -> dict[str, Any]: ...

    def spawn_watch(self, transcripts: tuple[Path, ...], interval_ms: int) -> Any: ...

    def spawn_listen(self, port: int) -> Any: ...


def default_tripwire_binary() -> str:
    """The tripwire binary to use when the caller does not name one.

    Prefers an explicit ``TRIPWIRE_BIN`` environment variable, then a
    ``tripwire`` on ``PATH``, then a release build of the sibling checkout of
    ``agent-tripwire``.
    """
    override = os.environ.get("TRIPWIRE_BIN")
    if override:
        return override
    from shutil import which

    found = which("tripwire")
    if found:
        return found
    sibling = Path.home() / "Developer/personal/agent-tripwire/target/release/tripwire"
    if sibling.is_file():
        return str(sibling)
    return "tripwire"


@dataclass
class Tripwire:
    """Runs the ``tripwire`` CLI against one state directory."""

    binary: str = "tripwire"
    state_dir: Path | None = None
    timeout: float = 120.0

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.state_dir is not None:
            env["TRIPWIRE_STATE_DIR"] = str(self.state_dir)
        return env

    def _run(
        self, args: tuple[str, ...], stdin: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [self.binary, *args],
                input=stdin,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise TripwireError(
                f"tripwire binary {self.binary!r} not found; install agent-tripwire "
                "or set TRIPWIRE_BIN"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TripwireError(f"tripwire {args[0]} timed out after {self.timeout}s") from exc
        if check and proc.returncode != 0:
            raise TripwireError(
                f"tripwire {args[0]} failed with status {proc.returncode}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc

    def plant(self, root: Path, kinds: tuple[str, ...], listen: int | None = None) -> PlantInfo:
        """Register honeytokens of ``kinds`` under ``root``."""
        for kind in kinds:
            if kind not in CREDENTIAL_KINDS:
                raise TripwireError(f"unknown credential kind {kind!r}")
        args = ["plant", str(root), *sum((["--kind", kind] for kind in kinds), [])]
        if listen is not None:
            args += ["--listen", str(listen)]
        proc = self._run(tuple(args))
        match = _PLANTED_RE.search(proc.stdout)
        if match is None:
            raise TripwireError(f"could not parse plant output: {proc.stdout!r}")
        plant_id = match.group(1)
        for record in self.plants():
            if record["id"] == plant_id:
                files = tuple(Path(item["path"]) for item in record["files"])
                token_ids = tuple(
                    token["id"] for item in record["files"] for token in item["tokens"]
                )
                return PlantInfo(
                    id=plant_id,
                    root=Path(record["root"]),
                    files=files,
                    token_ids=token_ids,
                )
        raise TripwireError(f"plant {plant_id} is missing from the registry")

    def plants(self) -> list[dict[str, Any]]:
        """Every plant in the registry."""
        proc = self._run(("list", "--json"))
        return json.loads(proc.stdout or "[]")

    def remove(self, plant_id: str) -> None:
        self._run(("remove", plant_id))

    def scan(self, paths: tuple[Path, ...]) -> list[dict[str, Any]]:
        """Scan transcripts or text files. Exit status 1 means findings."""
        if not paths:
            return []
        args = ["scan", *[str(path) for path in paths], "--json"]
        proc = self._run(tuple(args), check=False)
        if proc.returncode not in (0, 1):
            raise TripwireError(f"tripwire scan failed: {proc.stderr.strip()}")
        findings = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                findings.append(json.loads(line))
        return findings

    def alerts(self) -> list[dict[str, Any]]:
        """Every alert in the log, oldest first."""
        proc = self._run(("alerts", "--json"))
        alerts = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                alerts.append(json.loads(line))
        return alerts

    def planted_values(self, file: Path) -> list[str]:
        """Return the raw credential values held in a planted file.

        The planted values are not in the registry, by design: the registry
        holds ids and hashes so that reading it cannot leak a credential.
        The values are therefore read back from the file itself, exactly as a
        real agent would obtain them.

        To find which lines hold a credential, this asks ``tripwire scan``
        which lines matched a registered token. That keeps the token format
        out of this codebase: the marker is never hard-coded.
        """
        findings = self.scan((file,))
        if not findings:
            return []
        lines = file.read_text().splitlines()
        values: list[str] = []
        for line_number in sorted({finding["line"] for finding in findings}):
            if not 1 <= line_number <= len(lines):
                continue
            values.append(_credential_value(lines[line_number - 1]))
        return values

    def hook(self, event: dict[str, Any]) -> str:
        """Feed one hook event to ``tripwire hook`` and return its stderr."""
        proc = self._run(("hook",), stdin=json.dumps(event), check=False)
        return proc.stderr

    def hook_config(self, agent: str) -> dict[str, Any]:
        """The hook settings snippet for an agent.

        ``agent`` is ``claude`` or ``codex``. The snippet is the ``hooks``
        section that has to be merged into the agent's own settings file.
        """
        proc = self._run(("hook", "config", agent))
        return json.loads(proc.stdout)

    def spawn_watch(self, transcripts: tuple[Path, ...], interval_ms: int) -> subprocess.Popen[str]:
        args = ["watch", "--interval", str(interval_ms)]
        for path in transcripts:
            args += ["--transcripts", str(path)]
        return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [self.binary, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._env(),
        )

    def spawn_listen(self, port: int) -> subprocess.Popen[str]:
        return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [self.binary, "listen", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._env(),
        )
