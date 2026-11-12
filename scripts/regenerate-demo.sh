#!/bin/sh
# Regenerate the committed synthetic demo artefacts from scratch.
#
# This reruns the full mock-agent study (1536 trials, about eight minutes,
# entirely offline) and rebuilds REPORT.md, the figures and the tables from
# the fresh result file. The dataset is deterministic for the seed in
# configs/demo.toml, so a regeneration should reproduce the same numbers.
#
#   scripts/regenerate-demo.sh
#
# For a quick smoke test instead (two dozen trials, a few seconds):
#
#   uv run abs run --config configs/demo.toml --out /tmp/demo-smoke --limit 24
#   uv run abs analyze --results /tmp/demo-smoke --out /tmp/demo-smoke-report
set -eu

cd "$(dirname "$0")/.."

uv run abs run --config configs/demo.toml --out data/synthetic-demo
uv run abs analyze --results data/synthetic-demo --out data/synthetic-demo --report REPORT.md

echo "regenerated data/synthetic-demo/ and REPORT.md (all SYNTHETIC)"
