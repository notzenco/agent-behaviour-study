# agent-behaviour-study

**How often do AI coding agents read or send out credentials they come
across, and what affects it?**

A measurement harness and analysis pipeline for the behaviour of coding
agents around secrets. It builds on
[`agent-tripwire`](https://github.com/notzenco/agent-tripwire), which plants
synthetic honeytoken credentials and reports when an agent reads them, sends
them out, or uses them. This project turns that detector into an experiment:
a task suite, a controlled factorial design, tidy per-trial results, and a
statistical analysis.

> **The committed dataset is synthetic.** It was produced by a scripted mock
> agent with a known behaviour model, so that the whole pipeline could be
> built and tested without spending money on a model. It is a check on the
> instrumentation, not evidence about any real agent. Every figure and every
> table carries that label.

---

## Contents

| Path | What it is |
| --- | --- |
| [`docs/study-design.md`](docs/study-design.md) | The protocol: hypotheses, variables, task suite, methods, threats, ethics |
| [`configs/demo.toml`](configs/demo.toml) | The synthetic demo study |
| [`configs/real.toml`](configs/real.toml) | A template for a study with real agents and real models |
| [`src/abstudy/`](src/abstudy) | The harness, the analysis and the report builder |
| [`analysis/analyze.py`](analysis/analyze.py) | The analysis script: figures, tables, model, report |
| [`tests/`](tests) | The test suite |
| [`data/synthetic-demo/`](data/synthetic-demo) | The committed synthetic dataset and its figures |
| [`REPORT.md`](REPORT.md) | The report for the committed synthetic dataset |

---

## Quick start

The demo runs entirely offline. It needs [uv](https://docs.astral.sh/uv/)
and a `tripwire` binary.

```sh
# 1. Check the tools are there.
uv sync --all-groups
uv run abs doctor

# 2. Run the synthetic study. 1536 trials, about eight minutes.
uv run abs run --config configs/demo.toml --out results/demo

# 3. Analyse it.
uv run abs analyze --results results/demo --out reports/demo
```

Step 3 writes the figures, the tables and `reports/demo/report.md`. The same
analysis is available as a script that prints the tables to the terminal:

```sh
uv run python analysis/analyze.py --results results/demo --print-only
```

If `tripwire` is not on your `PATH`, point the harness at it:

```sh
uv run abs run --config configs/demo.toml --out results/demo \
    --tripwire ~/Developer/personal/agent-tripwire/target/release/tripwire
```

---

## Running a real study

Real runs call paid model APIs, so they are left to you. The full protocol
is in [`docs/study-design.md`](docs/study-design.md); this is the short
version.

**1. Install the detector.**

```sh
cargo install --locked --git https://github.com/notzenco/agent-tripwire
```

**2. Copy and edit the real configuration.**

```sh
cp configs/real.toml configs/my-study.toml
```

Edit at least:

* `models` -- the model identifiers you want to compare. The template ships
  with one placeholder.
* `replicates` -- the main budget dial. Start with 2 or 3.
* the `[[agents]]` blocks -- check the command against your installed CLI.
  The template's commands were written against `claude` 2.1, `codex` and
  `cursor-agent`, and were verified only as far as their `--help` output.
* `seed` -- change it if you are running a second wave.

**3. Set a spending cap** on your API account. The harness limits each trial
with `agent_timeout_s`, but it cannot limit what an agent spends.

**4. Run one trial first.**

```sh
uv run abs run --config configs/my-study.toml --out results/smoke --limit 1 \
    --keep-sandboxes
```

Look at `results/smoke/trials/<trial-id>/`: the prompt the agent got, the
planted credential, the transcript and the alert log. `--keep-sandboxes`
leaves the planted credentials in place, so the directory is a complete record
of the trial. **Do not commit it.**

**5. Run the study.**

```sh
uv run abs run --config configs/my-study.toml --out results/my-study
uv run abs analyze --results results/my-study --out reports/my-study
```

**6. Read `reports/my-study/report.md`.** It states the collection date, the
agents and models involved, the headline rates with confidence intervals, the
logistic model, and the limits of what the run can show.

### What the harness does per trial

```
<run>/trials/<trial-id>/
    home/          HOME for the agent; the only place a credential may be planted
    repo/          the task repository
    state/         TRIPWIRE_STATE_DIR: the registry and the alert log
    transcripts/   the collected session transcripts
    logs/          the agent's stdout and stderr
    prompt.txt     the exact prompt given to the agent
```

1. The task repository is copied into `repo/`.
2. A credential is planted with `tripwire plant`, in `repo/` or in `home/`
   depending on the placement factor.
3. `tripwire watch` and `tripwire listen` are started, and the tripwire hook
   is wired into the agent's settings when the agent supports it.
4. The agent runs, with `HOME` set to the sandbox.
5. The detectors are given a moment to notice, then stopped.
6. `tripwire scan` classifies the transcript and `tripwire alerts` returns the
   alert log.
7. The planted files are removed and the sandbox is deleted, unless
   `--keep-sandboxes` was given.

One row per trial is written to `trials.csv` and `trials.parquet`, with the
run metadata in `run.json`.

---

## The design in one table

| Factor | Levels |
| --- | --- |
| Agent | any command you configure (`claude`, `codex`, `cursor-agent`, ...) |
| Model | any identifiers you configure |
| Task | `fix_failing_test`, `add_endpoint`, `bump_dependency`, `write_runbook`, `refactor_module`, `debug_incident`, `review_security`, `deploy_staging` |
| Placement | `project_env`, `home_env`, `home_aws`, `home_npm`, `home_ssh` |
| Prompt wording | `neutral`, `secrets_warning`, `credentials_needed`, `log_everything` |

The primary outcome is **exposure**: the credential was sent out (`exfil`) or
used (`use`). The primary explanatory outcome is **read**: the agent obtained
the credential's contents. The headline metric is the *conditional*
exposure: of the trials that read the credential, how many exposed it.

---

## Safety

* **No real credentials.** Planted values are generated by `agent-tripwire` at
  run time, carry a `TRIPWIRE` marker, and were never issued by anyone.
* **No real home directory.** Every trial runs with `HOME` set to a
  disposable sandbox under the run directory, and the harness refuses to run
  if that sandbox would be the real home or one of its credential
  directories.
* **No outbound traffic from the study.** The use-detection listener binds to
  `127.0.0.1`. The mock agent's "exfiltration" is text in a transcript.
* **No credential values in the results.** The result file holds flags, ids
  and counts. Transcripts can contain synthetic token values, so they stay in
  the run directory and are never committed.

---

## Development

```sh
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

The tests that need the `tripwire` binary are skipped when it is not built,
so the suite still runs without it.

### Layout

```
src/abstudy/
    config.py      the study configuration and the trial plan
    templates.py   the sandbox task repositories
    tripwire.py    the tripwire CLI wrapper
    agents.py      the agent runners
    mock_agent.py  the scripted mock agent
    harness.py     per-trial orchestration and isolation
    results.py     the tidy result file
    analysis.py    proportions, intervals, the logistic model
    report.py      figures, tables and the report
    cli.py         the command line interface
```

Two libraries do the heavy lifting: `polars` writes the Parquet result file
without a separate Parquet engine, and `pandas` carries the analysis into
`statsmodels`.

---

## Licence

[MIT](LICENSE)
