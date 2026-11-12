"""Study configuration and the trial plan.

A study is described by a TOML file: which agents and models take part, which
tasks are in the suite, where credentials are planted, how the prompt is
worded, and how many times each cell is repeated. The configuration is the
whole experimental design, so it is a plain data file that can be committed
and diffed next to the results it produced.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from abstudy.mock_agent import MockProfile, MockSettings
from abstudy.templates import TEMPLATES
from abstudy.tripwire import CREDENTIAL_KINDS

__all__ = [
    "AgentSpec",
    "PlacementSpec",
    "PromptSpec",
    "StudyConfig",
    "TaskSpec",
    "TrialPlan",
    "load_config",
]

#: Factors that make up a cell of the design.
FACTORS = ("agent", "model", "task", "placement", "prompt")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class AgentSpec:
    """An agent taking part in the study."""

    key: str
    label: str
    kind: str
    command: tuple[str, ...] = ()
    timeout_s: float = 900.0
    env: tuple[tuple[str, str], ...] = ()
    #: ``claude`` or ``codex`` when the agent supports the tripwire hook.
    hook: str = ""
    #: Prompt piped to the agent on stdin, when it reads stdin.
    stdin: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    """A task in the suite."""

    key: str
    label: str
    category: str
    template: str
    prompt: str


@dataclass(frozen=True)
class PlacementSpec:
    """Where a credential is planted."""

    key: str
    label: str
    kind: str
    location: str


@dataclass(frozen=True)
class PromptSpec:
    """A prompt wording variant."""

    key: str
    label: str
    text: str


@dataclass(frozen=True)
class TrialPlan:
    """One cell of the design, repeated ``replicate`` times."""

    agent: str
    model: str
    task: str
    placement: str
    prompt: str
    replicate: int

    @property
    def cell(self) -> tuple[str, str, str, str, str]:
        return (self.agent, self.model, self.task, self.placement, self.prompt)

    @property
    def trial_id(self) -> str:
        """A filesystem-safe identifier for the trial."""
        raw = "-".join(
            [self.agent, self.model, self.task, self.placement, self.prompt, f"r{self.replicate}"]
        )
        return _UNSAFE.sub("_", raw)

    @property
    def factors(self) -> dict[str, str]:
        return dict(zip(FACTORS, self.cell, strict=True))


@dataclass(frozen=True)
class StudyConfig:
    """A complete experimental design."""

    name: str
    dataset: str
    synthetic: bool
    seed: int
    replicates: int
    models: tuple[str, ...]
    agents: tuple[AgentSpec, ...]
    tasks: tuple[TaskSpec, ...]
    placements: tuple[PlacementSpec, ...]
    prompts: tuple[PromptSpec, ...]
    mock: MockSettings | None = None
    watch_interval_ms: int = 100
    settle_ms: int = 200
    agent_timeout_s: float = 300.0
    listen: bool = True
    source: str = ""

    def agent(self, key: str) -> AgentSpec:
        return _lookup(self.agents, key, "agent")

    def task(self, key: str) -> TaskSpec:
        return _lookup(self.tasks, key, "task")

    def placement(self, key: str) -> PlacementSpec:
        return _lookup(self.placements, key, "placement")

    def prompt(self, key: str) -> PromptSpec:
        return _lookup(self.prompts, key, "prompt")

    def plan(self) -> list[TrialPlan]:
        """Every trial in the design, in a stable order."""
        plans: list[TrialPlan] = []
        for agent in self.agents:
            for model in self.models:
                for task in self.tasks:
                    for placement in self.placements:
                        for prompt in self.prompts:
                            for replicate in range(self.replicates):
                                plans.append(
                                    TrialPlan(
                                        agent=agent.key,
                                        model=model,
                                        task=task.key,
                                        placement=placement.key,
                                        prompt=prompt.key,
                                        replicate=replicate,
                                    )
                                )
        return plans

    def prompt_text(self, plan: TrialPlan) -> str:
        """The exact prompt given to the agent for a trial."""
        base = self.task(plan.task).prompt.strip()
        wording = self.prompt(plan.prompt).text.strip()
        if not wording:
            return base
        return f"{base}\n\n{wording}"

    def trial_seed(self, plan: TrialPlan) -> int:
        """A deterministic seed for a trial, derived from the run seed."""
        material = f"{self.seed}|{plan.trial_id}".encode()
        digest = hashlib.blake2b(material, digest_size=8).digest()
        return int.from_bytes(digest, "big")

    def describe(self) -> str:
        """A short human summary of the design."""
        lines = [
            f"study: {self.name}",
            f"dataset: {self.dataset} ({'SYNTHETIC' if self.synthetic else 'real'})",
            f"trials: {len(self.plan())}",
            f"  agents: {', '.join(a.key for a in self.agents)}",
            f"  models: {', '.join(self.models)}",
            f"  tasks: {', '.join(t.key for t in self.tasks)}",
            f"  placements: {', '.join(p.key for p in self.placements)}",
            f"  prompts: {', '.join(p.key for p in self.prompts)}",
            f"  replicates: {self.replicates}",
        ]
        return "\n".join(lines)


def _lookup(items: tuple[Any, ...], key: str, kind: str) -> Any:
    for item in items:
        if item.key == key:
            return item
    raise KeyError(f"unknown {kind} {key!r}")


def _parse_mock(raw: Mapping[str, Any]) -> MockSettings:
    profiles: dict[str, MockProfile] = {}
    for key, values in (raw.get("profiles") or {}).items():
        profiles[key] = MockProfile(
            key=key,
            logit_read=float(values["logit_read"]),
            logit_exposed=float(values["logit_exposed"]),
            logit_use=float(values.get("logit_use", -4.0)),
            logit_disclose=float(values.get("logit_disclose", -4.0)),
        )
    return MockSettings(
        profiles=profiles,
        read_modifiers=_modifiers(raw.get("read_modifiers")),
        exposed_modifiers=_modifiers(raw.get("exposed_modifiers")),
        use_modifiers=_modifiers(raw.get("use_modifiers")),
        disclose_modifiers=_modifiers(raw.get("disclose_modifiers")),
    )


def _modifiers(raw: Any) -> dict[str, dict[str, float]]:
    if not raw:
        return {}
    return {
        str(factor): {str(level): float(delta) for level, delta in levels.items()}
        for factor, levels in raw.items()
    }


def load_config(path: str | Path) -> StudyConfig:
    """Read and validate a study configuration."""
    config_path = Path(path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    agents = tuple(
        AgentSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            kind=item.get("kind", "external"),
            command=tuple(item.get("command", ())),
            timeout_s=float(item.get("timeout_s", 900.0)),
            env=tuple((pair[0], pair[1]) for pair in item.get("env", ())),
            hook=item.get("hook", ""),
            stdin=item.get("stdin"),
        )
        for item in raw.get("agents", ())
    )
    tasks = tuple(
        TaskSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            category=item.get("category", "code"),
            template=item.get("template", item["key"]),
            prompt=item["prompt"],
        )
        for item in raw.get("tasks", ())
    )
    placements = tuple(
        PlacementSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            kind=item["kind"],
            location=item.get("location", "project"),
        )
        for item in raw.get("placements", ())
    )
    prompts = tuple(
        PromptSpec(
            key=item["key"],
            label=item.get("label", item["key"]),
            text=item.get("text", ""),
        )
        for item in raw.get("prompts", ())
    )

    config = StudyConfig(
        name=raw.get("name", config_path.stem),
        dataset=raw.get("dataset", config_path.stem),
        synthetic=bool(raw.get("synthetic", False)),
        seed=int(raw.get("seed", 0)),
        replicates=int(raw.get("replicates", 1)),
        models=tuple(raw.get("models", ())),
        agents=agents,
        tasks=tasks,
        placements=placements,
        prompts=prompts,
        mock=_parse_mock(raw["mock"]) if raw.get("mock") else None,
        watch_interval_ms=int(raw.get("watch_interval_ms", 100)),
        settle_ms=int(raw.get("settle_ms", 200)),
        agent_timeout_s=float(raw.get("agent_timeout_s", 300.0)),
        listen=bool(raw.get("listen", True)),
        source=str(config_path),
    )
    validate(config)
    return config


def validate(config: StudyConfig) -> None:
    """Check that a design is coherent before any trial runs."""
    if not config.agents:
        raise ValueError("the study has no agents")
    if not config.models:
        raise ValueError("the study has no models")
    if not config.tasks:
        raise ValueError("the study has no tasks")
    if not config.placements:
        raise ValueError("the study has no placements")
    if not config.prompts:
        raise ValueError("the study has no prompt variants")
    if config.replicates < 1:
        raise ValueError("replicates must be at least one")

    for group, name in (
        (config.agents, "agent"),
        (config.tasks, "task"),
        (config.placements, "placement"),
        (config.prompts, "prompt"),
    ):
        keys = [item.key for item in group]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            raise ValueError(f"duplicate {name} keys: {sorted(duplicates)}")

    for task in config.tasks:
        if task.template not in TEMPLATES:
            raise ValueError(f"task {task.key!r} names unknown template {task.template!r}")

    for placement in config.placements:
        if placement.kind not in CREDENTIAL_KINDS:
            raise ValueError(
                f"placement {placement.key!r} names unknown credential kind {placement.kind!r}"
            )
        if placement.location not in ("project", "home"):
            raise ValueError(
                f"placement {placement.key!r} has unknown location {placement.location!r}"
            )

    for agent in config.agents:
        if agent.kind == "mock" and config.mock is None:
            raise ValueError(f"agent {agent.key!r} is a mock but the study has no mock settings")
        if agent.kind == "external" and not agent.command:
            raise ValueError(f"agent {agent.key!r} has no command")
        if agent.kind not in ("mock", "external"):
            raise ValueError(f"agent {agent.key!r} has unknown kind {agent.kind!r}")
