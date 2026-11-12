"""The tidy result file: one row per trial.

A result file is a small table with one row per trial and one column per
variable. It is written as CSV (always) and as Parquet (when polars is
available), and it never contains a planted credential value.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl

__all__ = [
    "RESULT_COLUMNS",
    "build_row",
    "read_results",
    "write_results",
]

#: Every column of a result row, in order.
RESULT_COLUMNS: tuple[str, ...] = (
    # identity
    "trial_id",
    "run_id",
    "dataset",
    "synthetic",
    # factors
    "agent",
    "agent_label",
    "agent_kind",
    "model",
    "task",
    "task_label",
    "task_category",
    "placement",
    "placement_label",
    "credential_kind",
    "placement_location",
    "prompt",
    "prompt_label",
    "replicate",
    "seed",
    # outcomes
    "read_attempt",
    "read",
    "exfil",
    "use",
    "disclosed",
    "mention",
    "modified",
    "deleted",
    "read_any",
    "exposed",
    # which detectors fired
    "det_hook",
    "det_watch",
    "det_listen",
    "det_scan",
    # counts and process metadata
    "n_alerts",
    "n_findings",
    "n_distinct_tokens",
    "duration_s",
    "exit_code",
    "error",
)

_TEXT_COLUMNS = {
    "trial_id",
    "run_id",
    "dataset",
    "agent",
    "agent_label",
    "agent_kind",
    "model",
    "task",
    "task_label",
    "task_category",
    "placement",
    "placement_label",
    "credential_kind",
    "placement_location",
    "prompt",
    "prompt_label",
    "error",
}

_BOOL_COLUMNS = {
    "synthetic",
    "read_attempt",
    "read",
    "exfil",
    "use",
    "disclosed",
    "mention",
    "modified",
    "deleted",
    "read_any",
    "exposed",
    "det_hook",
    "det_watch",
    "det_listen",
    "det_scan",
}

_INT_COLUMNS = {
    "replicate",
    "seed",
    "n_alerts",
    "n_findings",
    "n_distinct_tokens",
    "exit_code",
}

_SCHEMA = {
    **dict.fromkeys(_TEXT_COLUMNS, pl.String),
    **dict.fromkeys(_BOOL_COLUMNS, pl.Boolean),
    **dict.fromkeys(_INT_COLUMNS, pl.Int64),
    "duration_s": pl.Float64,
}


def build_row(
    *,
    trial_id: str,
    run_id: str,
    dataset: str,
    synthetic: bool,
    agent: str,
    agent_label: str,
    agent_kind: str,
    model: str,
    task: str,
    task_label: str,
    task_category: str,
    placement: str,
    placement_label: str,
    credential_kind: str,
    placement_location: str,
    prompt: str,
    prompt_label: str,
    replicate: int,
    seed: int,
    outcomes: Mapping[str, int],
    detectors: Mapping[str, int],
    n_alerts: int,
    n_findings: int,
    n_distinct_tokens: int,
    duration_s: float,
    exit_code: int,
    error: str | None,
) -> dict[str, Any]:
    """Assemble one result row.

    ``outcomes`` holds the detector counts for the alert kinds that matter.
    """
    row: dict[str, Any] = {
        "trial_id": trial_id,
        "run_id": run_id,
        "dataset": dataset,
        "synthetic": synthetic,
        "agent": agent,
        "agent_label": agent_label,
        "agent_kind": agent_kind,
        "model": model,
        "task": task,
        "task_label": task_label,
        "task_category": task_category,
        "placement": placement,
        "placement_label": placement_label,
        "credential_kind": credential_kind,
        "placement_location": placement_location,
        "prompt": prompt,
        "prompt_label": prompt_label,
        "replicate": replicate,
        "seed": seed,
        "n_alerts": n_alerts,
        "n_findings": n_findings,
        "n_distinct_tokens": n_distinct_tokens,
        "duration_s": duration_s,
        "exit_code": exit_code,
        "error": error,
    }
    for kind in (
        "read_attempt",
        "read",
        "exfil",
        "use",
        "disclosed",
        "mention",
        "modified",
        "deleted",
    ):
        row[kind] = int(outcomes.get(kind, 0))
    row["read_any"] = int(bool(row["read_attempt"] or row["read"]))
    row["exposed"] = int(bool(row["exfil"] or row["use"]))
    for name in ("det_hook", "det_watch", "det_listen", "det_scan"):
        row[name] = int(detectors.get(name, 0))
    return {name: row.get(name) for name in RESULT_COLUMNS}


def write_results(
    rows: Sequence[Mapping[str, Any]],
    run_dir: Path,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Write the result file, its Parquet twin and the run metadata."""
    run_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    csv_path = run_dir / "trials.csv"
    frame = pd.DataFrame(list(rows), columns=list(RESULT_COLUMNS))
    frame.to_csv(csv_path, index=False)
    written["csv"] = csv_path

    parquet_path = run_dir / "trials.parquet"
    columns = {name: [row.get(name) for row in rows] for name in RESULT_COLUMNS}
    pl.DataFrame(columns, schema=_SCHEMA).write_parquet(parquet_path)
    written["parquet"] = parquet_path

    if metadata is not None:
        metadata_path = run_dir / "run.json"
        metadata_path.write_text(json.dumps(dict(metadata), indent=2, default=str) + "\n")
        written["metadata"] = metadata_path
    return written


def read_results(path: str | Path) -> pd.DataFrame:
    """Read a result file into a tidy dataframe.

    Both the CSV and the Parquet file are accepted; the CSV is the canonical
    one, because it can be read without any optional dependency.
    """
    source = Path(path)
    if source.is_dir():
        source = source / "trials.csv"
    if source.suffix == ".parquet":
        frame = pd.DataFrame(pl.read_parquet(source).to_dicts())
    else:
        frame = pd.read_csv(source)
    for name in _TEXT_COLUMNS:
        if name in frame.columns:
            frame[name] = frame[name].astype("object").where(frame[name].notna(), None)
    for name in _BOOL_COLUMNS:
        if name in frame.columns:
            frame[name] = frame[name].fillna(False).astype(bool)
    for name in _INT_COLUMNS:
        if name in frame.columns:
            frame[name] = frame[name].fillna(0).astype("int64")
    return frame


def summarise_columns(columns: Iterable[str]) -> list[str]:
    """Keep only the columns that exist in a frame."""
    known = set(RESULT_COLUMNS)
    return [name for name in columns if name in known]


def now_iso() -> str:
    """An RFC 3339 timestamp in UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
