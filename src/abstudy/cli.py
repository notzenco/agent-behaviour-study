"""The command line interface.

abs run      run a study and write the result file
abs analyze  turn a result file into figures, tables and a report
abs tasks    show the task suite
abs doctor   check that the tools this study needs are present
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from abstudy.config import load_config
from abstudy.harness import Harness
from abstudy.report import build_report, load_metadata
from abstudy.results import now_iso, read_results, write_results
from abstudy.templates import TEMPLATES
from abstudy.tripwire import Tripwire, TripwireError, default_tripwire_binary

__all__ = ["main"]


def _add_run(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("run", help="run a study and write the result file")
    parser.add_argument("--config", required=True, help="the study configuration (TOML)")
    parser.add_argument("--out", required=True, help="where to write the result file")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N trials")
    parser.add_argument(
        "--keep-sandboxes",
        action="store_true",
        help="keep the trial sandboxes, including the planted credentials",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="do not remove the planted credentials after each trial",
    )
    parser.add_argument("--tripwire", default=None, help="path to the tripwire binary")
    parser.set_defaults(func=_run)


def _add_analyze(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "analyze", help="turn a result file into figures, tables and a report"
    )
    parser.add_argument("--results", required=True, help="the run directory or the result file")
    parser.add_argument("--out", required=True, help="where to write the report")
    parser.add_argument("--report", default=None, help="also write the report to this path")
    parser.set_defaults(func=_analyze)


def _add_tasks(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("tasks", help="show the task suite")
    parser.set_defaults(func=_tasks)


def _add_doctor(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("doctor", help="check that the tools are present")
    parser.add_argument("--config", default=None, help="also check a study configuration")
    parser.set_defaults(func=_doctor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abs",
        description="Measure how often AI coding agents read, leak or use credentials.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_run(subparsers)
    _add_analyze(subparsers)
    _add_tasks(subparsers)
    _add_doctor(subparsers)
    return parser


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    binary = args.tripwire or default_tripwire_binary()
    if not (Path(binary).is_file() or shutil.which(binary)):
        print(
            f"error: the tripwire binary {binary!r} was not found.\n"
            "Install agent-tripwire (https://github.com/notzenco/agent-tripwire) or pass\n"
            "--tripwire /path/to/tripwire.",
            file=sys.stderr,
        )
        return 2

    print(config.describe())
    if config.synthetic:
        print("\nThis study is SYNTHETIC: it runs a scripted mock agent and says nothing")
        print("about real agents.")

    out = Path(args.out)
    harness = Harness(
        config=config,
        run_dir=out,
        tripwire_binary=binary,
        keep_sandboxes=args.keep_sandboxes,
        cleanup_plants=not args.no_cleanup,
    )
    plans = config.plan()
    if args.limit:
        plans = plans[: args.limit]
    print(f"\nrunning {len(plans)} trials into {out}")

    started_at = now_iso()
    clock = time.monotonic()

    def progress(index: int, total: int, plan) -> None:
        if index % 25 == 0 or index == total:
            elapsed = time.monotonic() - clock
            rate = index / elapsed if elapsed else 0.0
            remaining = (total - index) / rate if rate else 0.0
            print(
                f"  [{index:>5}/{total}] {plan.trial_id} "
                f"({rate:.1f}/s, about {remaining / 60:.1f} min left)",
                flush=True,
            )

    rows = harness.run(plans, progress=progress)
    finished_at = now_iso()
    metadata = harness.metadata(rows, started_at, finished_at)
    written = write_results(rows, out, metadata)

    failed = sum(1 for row in rows if row.get("error"))
    exposed = sum(int(row["exposed"]) for row in rows)
    read = sum(int(row["read_any"]) for row in rows)
    print(f"\nfinished {len(rows)} trials in {time.monotonic() - clock:.1f}s")
    print(f"  read    {read}/{len(rows)}")
    print(f"  exposed {exposed}/{len(rows)}")
    if failed:
        print(f"  failed  {failed}")
    print(f"\nwrote {written['csv']}")
    print(f"wrote {written['parquet']}")
    print(f"wrote {written['metadata']}")
    return 0


def _analyze(args: argparse.Namespace) -> int:
    results = Path(args.results)
    frame = read_results(results)
    metadata = load_metadata(results)
    if not metadata:
        print(
            "warning: no run metadata was found, so the report cannot say whether the"
            " data is synthetic",
            file=sys.stderr,
        )
    artifacts = build_report(frame, metadata, args.out, report_path=args.report)
    print(f"wrote {artifacts.report}")
    for name, path in artifacts.figures.items():
        print(f"wrote {path} ({name})")
    for name, path in artifacts.tables.items():
        print(f"wrote {path} ({name})")
    return 0


def _tasks(_args: argparse.Namespace) -> int:
    print("task suite:")
    for key, repo in sorted(TEMPLATES.items()):
        print(f"  {key:<20} {repo.label}")
        for relative in sorted(repo.files):
            print(f"      {relative}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    problems = 0
    binary = default_tripwire_binary()
    found = Path(binary).is_file() or shutil.which(binary) is not None
    print(f"tripwire binary: {binary} {'ok' if found else 'MISSING'}")
    if not found:
        problems += 1
    else:
        try:
            Tripwire(binary=binary).plants()
            print("tripwire registry: ok")
        except TripwireError as exc:
            print(f"tripwire registry: {exc}")
            problems += 1

    print(f"python: {sys.version.split()[0]}")

    if args.config:
        config = load_config(args.config)
        print()
        print(config.describe())
        for agent in config.agents:
            if agent.kind == "external" and not shutil.which(agent.command[0]):
                print(f"  warning: the agent command {agent.command[0]!r} is not on PATH")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
