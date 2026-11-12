"""Tests for the command line interface."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from abstudy.cli import main
from abstudy.results import RESULT_COLUMNS, build_row, write_results


def _row(index: int = 0) -> dict:
    return build_row(
        trial_id=f"t{index}",
        run_id="run",
        dataset="synthetic-demo",
        synthetic=True,
        agent="mock-a",
        agent_label="Mock A",
        agent_kind="mock",
        model="model-small",
        task="fix",
        task_label="Fix",
        task_category="code",
        placement="project_env",
        placement_label="Project",
        credential_kind="env",
        placement_location="project",
        prompt="neutral",
        prompt_label="Neutral",
        replicate=index,
        seed=index,
        outcomes={"read": 1, "exfil": 1},
        detectors={"hook": 1, "scan": 1},
        n_alerts=4,
        n_findings=2,
        n_distinct_tokens=1,
        duration_s=0.2,
        exit_code=0,
        error=None,
    )


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    rows = [_row(index) for index in range(20)]
    metadata = {
        "study": "synthetic-demo",
        "dataset": "synthetic-demo",
        "synthetic": True,
        "n_trials": len(rows),
        "config_path": "configs/demo.toml",
    }
    write_results(rows, run_dir, metadata)
    return run_dir


def test_tasks_lists_the_suite(capsys):
    assert main(["tasks"]) == 0

    output = capsys.readouterr().out
    assert "fix_failing_test" in output
    assert "deploy_staging" in output


def test_doctor_reports_the_tripwire_binary(capsys, tripwire_binary: str):
    assert main(["doctor"]) == 0

    output = capsys.readouterr().out
    assert "tripwire binary" in output
    assert "ok" in output


def test_analyze_writes_a_report_and_figures(tmp_path: Path, capsys):
    run_dir = _run_dir(tmp_path)

    assert main(["analyze", "--results", str(run_dir), "--out", str(tmp_path / "report")]) == 0

    report = tmp_path / "report" / "report.md"
    assert report.exists()
    assert "SYNTHETIC" in report.read_text()
    assert (tmp_path / "report" / "figures" / "rates_by_factor.png").exists()
    output = capsys.readouterr().out
    assert "wrote" in output


def test_analyze_can_write_the_report_elsewhere(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    elsewhere = tmp_path / "REPORT.md"

    assert (
        main(
            [
                "analyze",
                "--results",
                str(run_dir),
                "--out",
                str(tmp_path / "report"),
                "--report",
                str(elsewhere),
            ]
        )
        == 0
    )

    assert elsewhere.exists()
    assert "SYNTHETIC" in elsewhere.read_text()


def test_run_refuses_to_start_without_the_tripwire_binary(tmp_path: Path, capsys):
    code = main(
        [
            "run",
            "--config",
            str(Path(__file__).resolve().parent.parent / "configs" / "demo.toml"),
            "--out",
            str(tmp_path / "run"),
            "--tripwire",
            str(tmp_path / "not-a-binary"),
            "--limit",
            "1",
        ]
    )

    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_the_result_file_has_one_row_per_trial(tmp_path: Path):
    run_dir = _run_dir(tmp_path)

    frame = pd.read_csv(run_dir / "trials.csv")

    assert len(frame) == 20
    assert list(frame.columns) == list(RESULT_COLUMNS)


def test_the_run_metadata_records_the_ground_truth(tmp_path: Path):
    from abstudy.config import load_config
    from abstudy.harness import Harness

    config = load_config(Path(__file__).resolve().parent.parent / "configs" / "demo.toml")
    harness = Harness(config=config, run_dir=tmp_path / "run")

    metadata = harness.metadata([], "start", "finish")

    assert metadata["synthetic"] is True
    assert metadata["ground_truth"]
    assert metadata["ground_truth"][0]["p_read"] > 0
