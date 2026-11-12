"""Tests for the figures, the tables and the generated report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from abstudy.report import (
    SYNTHETIC_BANNER,
    build_report,
    load_metadata,
    markdown_table,
    write_metadata,
)


def _frame(n: int = 60) -> pd.DataFrame:
    rows = []
    for index in range(n):
        rows.append(
            {
                "trial_id": f"t{index}",
                "run_id": "run",
                "dataset": "synthetic-demo",
                "synthetic": True,
                "agent": "mock-a" if index % 2 else "mock-b",
                "agent_label": "Mock",
                "agent_kind": "mock",
                "model": "model-small",
                "task": "fix" if index % 3 else "deploy",
                "task_label": "Fix",
                "task_category": "code",
                "placement": "project_env" if index % 4 else "home_aws",
                "placement_label": "Project",
                "credential_kind": "env",
                "placement_location": "project",
                "prompt": "neutral" if index % 2 else "log_everything",
                "prompt_label": "Neutral",
                "replicate": index,
                "seed": index,
                "read_attempt": 1,
                "read": 1,
                "exfil": 1 if index % 3 else 0,
                "use": 0,
                "disclosed": 0,
                "mention": 0,
                "modified": 0,
                "deleted": 0,
                "read_any": 1,
                "exposed": 1 if index % 3 else 0,
                "det_hook": 1,
                "det_watch": 1,
                "det_listen": 0,
                "det_scan": 1,
                "n_alerts": 4,
                "n_findings": 2,
                "n_distinct_tokens": 1,
                "duration_s": 0.5,
                "exit_code": 0,
                "error": None,
            }
        )
    return pd.DataFrame(rows)


def _metadata(synthetic: bool = True) -> dict:
    return {
        "study": "test",
        "dataset": "synthetic-demo",
        "synthetic": synthetic,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:10:00Z",
        "n_trials": 60,
        "config_path": "configs/demo.toml",
    }


def test_the_report_is_written_with_its_figures_and_tables(tmp_path: Path):
    artifacts = build_report(_frame(), _metadata(), tmp_path)

    assert artifacts.report.exists()
    assert set(artifacts.figures) == {
        "rates_by_factor",
        "placement_prompt",
        "outcomes",
        "odds_ratios",
    }
    for path in artifacts.figures.values():
        assert path.exists()
        assert path.stat().st_size > 1000
    for path in artifacts.tables.values():
        assert path.exists()


def test_a_synthetic_report_is_labelled_everywhere(tmp_path: Path):
    artifacts = build_report(_frame(), _metadata(synthetic=True), tmp_path)

    text = artifacts.report.read_text()
    assert "SYNTHETIC" in text
    assert SYNTHETIC_BANNER in text


def test_a_real_report_is_not_labelled_synthetic(tmp_path: Path):
    artifacts = build_report(_frame(), _metadata(synthetic=False), tmp_path)

    text = artifacts.report.read_text()
    assert "SYNTHETIC DATASET" not in text
    assert "Real agent run" in text


def test_the_report_reports_the_headline_numbers(tmp_path: Path):
    artifacts = build_report(_frame(), _metadata(), tmp_path)

    text = artifacts.report.read_text()
    assert "read_any" in text
    assert "exposed" in text
    assert "Wilson" in text


def test_the_report_links_the_figures_relative_to_itself(tmp_path: Path):
    artifacts = build_report(_frame(), _metadata(), tmp_path)

    text = artifacts.report.read_text()
    assert "figures/rates_by_factor.png" in text


def test_failed_trials_are_excluded_and_counted(tmp_path: Path):
    frame = _frame()
    frame.loc[0, "error"] = "boom"

    artifacts = build_report(frame, _metadata(), tmp_path)

    text = artifacts.report.read_text()
    assert "trials analysed | 59" in text
    assert "trials excluded (errors) | 1" in text


def test_the_factor_table_has_one_row_per_level(tmp_path: Path):
    frame = _frame()
    artifacts = build_report(frame, _metadata(), tmp_path)

    rows = artifacts.tables["factor_rates"].read_text().strip().splitlines()
    levels = sum(
        frame[factor].nunique() for factor in ("agent", "model", "task", "placement", "prompt")
    )
    assert len(rows) == 1 + levels


def test_markdown_table_renders_a_header_and_a_row():
    frame = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})

    text = markdown_table(frame)

    assert text.splitlines()[0] == "| a | b |"
    assert text.splitlines()[2] == "| 1 | x |"


def test_markdown_table_of_nothing_says_so():
    assert markdown_table(pd.DataFrame()) == "_no rows_"


def test_the_metadata_round_trips(tmp_path: Path):
    write_metadata(tmp_path, _metadata())

    loaded = load_metadata(tmp_path)

    assert loaded["dataset"] == "synthetic-demo"
    assert loaded["synthetic"] is True


def test_missing_metadata_reads_as_empty(tmp_path: Path):
    assert load_metadata(tmp_path) == {}
