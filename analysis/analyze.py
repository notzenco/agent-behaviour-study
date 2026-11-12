#!/usr/bin/env python
"""Analyse a study run and write the figures, the tables and the report.

This is the analysis entry point for the study. It reads a result file
produced by ``abs run``, fits the pre-registered models, and writes:

* ``figures/`` -- the charts, each carrying the synthetic banner when the
  dataset is synthetic;
* ``tables/``  -- the same numbers as CSV;
* ``report.md`` -- the written report, with every number taken from the data.

Usage:

    uv run python analysis/analyze.py --results results/demo --out reports/demo

For a quick look in the terminal:

    uv run python analysis/analyze.py --results results/demo --print-only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from abstudy.analysis import (
    conditional_exposure,
    detector_agreement,
    fit_logistic,
    ground_truth_comparison,
    proportion_table,
)
from abstudy.report import build_report, load_metadata, markdown_table
from abstudy.results import read_results

FACTORS = ("agent", "model", "task", "placement", "prompt")


def _banner(metadata: dict) -> str:
    if metadata.get("synthetic"):
        return "SYNTHETIC DATASET - a scripted mock agent, not real agent behaviour"
    return "real agent run"


def _headline(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for outcome in ("read_any", "exposed", "exfil", "use", "disclosed"):
        successes = int(frame[outcome].sum())
        low = proportion_table(frame.assign(_all="all"), [], outcome).iloc[0]["ci_low"]
        high = proportion_table(frame.assign(_all="all"), [], outcome).iloc[0]["ci_high"]
        rows.append(
            {
                "outcome": outcome,
                "events": successes,
                "n": len(frame),
                "rate": successes / len(frame),
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="the run directory or the result file")
    parser.add_argument("--out", default=None, help="where to write the figures and the report")
    parser.add_argument("--report", default=None, help="also write the report to this path")
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="print the tables without writing anything",
    )
    args = parser.parse_args(argv)

    frame = read_results(args.results)
    metadata = load_metadata(args.results)
    usable = frame[frame["error"].isna() | (frame["error"] == "")]

    print(f"dataset: {metadata.get('dataset', 'unknown')} ({_banner(metadata)})")
    print(f"trials: {len(usable)} analysed, {len(frame) - len(usable)} excluded")
    print()

    print("## headline")
    print(markdown_table(_headline(usable)))
    print()

    print("## rate by factor level")
    rows = []
    for factor in FACTORS:
        if factor not in usable.columns:
            continue
        for _, row in proportion_table(usable, [factor], "exposed").iterrows():
            rows.append(
                {
                    "factor": factor,
                    "level": row[factor],
                    "n": row["n"],
                    "events": row["events"],
                    "rate": row["rate"],
                    "ci_low": row["ci_low"],
                    "ci_high": row["ci_high"],
                }
            )
    print(markdown_table(pd.DataFrame(rows)))
    print()

    fit = fit_logistic(usable, "exposed", FACTORS, cluster="task")
    print("## logistic model")
    if fit.separated:
        print(f"not estimable: {fit.note}")
    else:
        print(
            f"n={fit.n}, pseudo R2={fit.pseudo_r2:.3f}, "
            f"cluster-robust standard errors on the task template"
        )
        print(markdown_table(fit.terms))
    print()

    print("## conditional exposure (of the trials that read)")
    print(markdown_table(conditional_exposure(usable)))
    print()

    print("## detector agreement")
    print(markdown_table(detector_agreement(usable)))
    print()

    truth = ground_truth_comparison(usable, metadata.get("ground_truth") or [])
    if not truth.empty:
        print("## ground truth check")
        print(markdown_table(truth))
        print()

    if args.print_only:
        return 0

    out = Path(args.out or Path(args.results) / "report")
    artifacts = build_report(frame, metadata, out, report_path=args.report)
    print(f"wrote {artifacts.report}")
    for path in artifacts.figures.values():
        print(f"wrote {path}")
    for path in artifacts.tables.values():
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
