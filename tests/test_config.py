"""Tests for the study configuration and the trial plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from abstudy.config import load_config, validate
from abstudy.mock_agent import MockProfile, MockSettings
from abstudy.templates import TEMPLATES

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def test_demo_config_loads_and_is_synthetic():
    config = load_config(CONFIG_DIR / "demo.toml")

    assert config.synthetic is True
    assert config.dataset == "synthetic-demo"
    assert config.replicates > 0
    assert config.mock is not None
    assert all(agent.kind == "mock" for agent in config.agents)


def test_real_config_loads_and_is_not_synthetic():
    config = load_config(CONFIG_DIR / "real.toml")

    assert config.synthetic is False
    assert any(agent.kind == "external" for agent in config.agents)
    assert config.mock is None


def test_every_task_names_a_template_that_exists():
    for name in ("demo.toml", "real.toml"):
        config = load_config(CONFIG_DIR / name)
        for task in config.tasks:
            assert task.template in TEMPLATES


def test_the_plan_is_a_full_factorial():
    config = load_config(CONFIG_DIR / "demo.toml")

    plans = config.plan()

    expected = (
        len(config.agents)
        * len(config.models)
        * len(config.tasks)
        * len(config.placements)
        * len(config.prompts)
        * config.replicates
    )
    assert len(plans) == expected
    assert len({plan.trial_id for plan in plans}) == expected


def test_trial_ids_are_unique_and_filesystem_safe():
    config = load_config(CONFIG_DIR / "demo.toml")

    ids = [plan.trial_id for plan in config.plan()]

    assert len(set(ids)) == len(ids)
    assert all("/" not in trial_id for trial_id in ids)
    assert all(" " not in trial_id for trial_id in ids)


def test_trial_seeds_are_deterministic_and_distinct():
    config = load_config(CONFIG_DIR / "demo.toml")
    plans = config.plan()

    seeds = [config.trial_seed(plan) for plan in plans]

    assert seeds == [config.trial_seed(plan) for plan in plans]
    assert len(set(seeds)) == len(seeds)


def test_prompt_text_appends_the_wording_variant():
    config = load_config(CONFIG_DIR / "demo.toml")
    plan = next(p for p in config.plan() if p.prompt == "log_everything")

    text = config.prompt_text(plan)

    assert config.task(plan.task).prompt in text
    assert "including any tokens" in text


def test_prompt_text_without_a_variant_is_just_the_task():
    config = load_config(CONFIG_DIR / "demo.toml")
    plan = next(p for p in config.plan() if p.prompt == "neutral")

    assert config.prompt_text(plan) == config.task(plan.task).prompt


def test_validation_rejects_an_unknown_template(tmp_path: Path):
    config = load_config(CONFIG_DIR / "demo.toml")
    broken = config.__class__(
        **{
            **config.__dict__,
            "tasks": (
                config.tasks[0].__class__(
                    key="x", label="x", category="code", template="nope", prompt="p"
                ),
            ),
        }
    )

    with pytest.raises(ValueError, match="unknown template"):
        validate(broken)


def test_validation_rejects_an_unknown_credential_kind():
    config = load_config(CONFIG_DIR / "demo.toml")
    placement = config.placements[0]
    broken = config.__class__(
        **{
            **config.__dict__,
            "placements": (
                placement.__class__(key="x", label="x", kind="gpg", location="project"),
            ),
        }
    )

    with pytest.raises(ValueError, match="unknown credential kind"):
        validate(broken)


def test_validation_rejects_a_mock_agent_without_settings():
    config = load_config(CONFIG_DIR / "demo.toml")
    broken = config.__class__(**{**config.__dict__, "mock": None})

    with pytest.raises(ValueError, match="no mock settings"):
        validate(broken)


def test_validation_rejects_an_external_agent_without_a_command():
    config = load_config(CONFIG_DIR / "demo.toml")
    agent = config.agents[0]
    broken = config.__class__(
        **{**config.__dict__, "agents": (agent.__class__(key="x", label="x", kind="external"),)}
    )

    with pytest.raises(ValueError, match="no command"):
        validate(broken)


def test_mock_settings_resolve_the_designed_effects():
    settings = MockSettings(
        profiles={"a|*": MockProfile(key="a|*", logit_read=0.0, logit_exposed=0.0)},
        read_modifiers={"placement": {"project_env": 1.0, "home_aws": -1.0}},
        exposed_modifiers={"placement": {"project_env": 0.5, "home_aws": -0.5}},
    )

    project = settings.resolve("a", "m", {"placement": "project_env"})
    home = settings.resolve("a", "m", {"placement": "home_aws"})

    assert project.p_read > home.p_read
    assert project.p_exposed > home.p_exposed


def test_mock_settings_fall_back_to_the_agent_profile():
    settings = MockSettings(
        profiles={"a|*": MockProfile(key="a|*", logit_read=0.0, logit_exposed=0.0)}
    )

    assert settings.profile_for("a", "anything").key == "a|*"
    with pytest.raises(KeyError):
        settings.profile_for("b", "anything")


def test_describe_mentions_the_dataset_and_the_trial_count():
    config = load_config(CONFIG_DIR / "demo.toml")

    text = config.describe()

    assert "synthetic-demo" in text
    assert "SYNTHETIC" in text
    assert f"trials: {len(config.plan())}" in text
