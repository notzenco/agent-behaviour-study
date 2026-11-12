"""The statistics.

Everything the report prints comes from this module, so the numbers in the
report and the numbers in the data cannot drift apart.

The methods are deliberately conservative: Wilson intervals rather than the
normal approximation, because several cells will sit near 0 or 1; Fisher's
exact test rather than the chi-squared approximation, because the counts are
small; and a logistic regression with cluster-robust standard errors, because
trials that share a task template are not independent.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats
from statsmodels.tools.sm_exceptions import PerfectSeparationWarning

__all__ = [
    "LogitFit",
    "adjust_fdr",
    "conditional_exposure",
    "detector_agreement",
    "fisher_comparison",
    "fit_logistic",
    "ground_truth_comparison",
    "one_sample_proportion_test",
    "power_for_two_proportions",
    "proportion_table",
    "wilson_interval",
]


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    """The Wilson score interval for a binomial proportion.

    Unlike the normal approximation it stays inside [0, 1] and stays sensible
    at zero successes, which is where most of the cells in this study sit.
    """
    if total <= 0:
        return (float("nan"), float("nan"))
    z = NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    proportion = successes / total
    denominator = 1 + z**2 / total
    centre = (proportion + z**2 / (2 * total)) / denominator
    margin = (
        z * np.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)) / denominator
    )
    # At the boundaries the exact bound is 0 or 1; centre - margin can land a
    # rounding error away from it, and which side it lands on is platform
    # dependent, so snap instead of relying on the clamp.
    low = 0.0 if successes == 0 else max(0.0, centre - margin)
    high = 1.0 if successes == total else min(1.0, centre + margin)
    return (low, high)


def proportion_table(
    frame: pd.DataFrame,
    group_columns: Sequence[str],
    outcome: str,
    confidence: float = 0.95,
) -> pd.DataFrame:
    """The rate of ``outcome`` for every level of the grouping.

    The result has one row per group with the number of trials, the number
    of events, the rate and its confidence interval.
    """
    columns = [name for name in group_columns if name in frame.columns]
    if not columns:
        columns = ["_all"]
        frame = frame.assign(_all="all")
    rows = []
    for keys, group in frame.groupby(columns, dropna=False, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        total = len(group)
        successes = int(group[outcome].sum())
        low, high = wilson_interval(successes, total, confidence)
        row = dict(zip(columns, keys, strict=True))
        row.update(
            {
                "n": total,
                "events": successes,
                "rate": successes / total if total else float("nan"),
                "ci_low": low,
                "ci_high": high,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(list(columns)).reset_index(drop=True)


def fisher_comparison(
    frame: pd.DataFrame,
    outcome: str,
    group: str,
    reference: str | None = None,
) -> dict[str, Any]:
    """Compare the outcome rate of one factor level against a reference.

    A two-sided Fisher exact test on the 2x2 table of level by outcome.
    """
    levels = list(frame[group].dropna().unique())
    if reference is None:
        reference = levels[0]
    others = [level for level in levels if level != reference]
    if not others:
        raise ValueError(f"{group} has only one level")

    reference_group = frame[frame[group] == reference]
    others_group = frame[frame[group].isin(others)]
    table = [
        [
            int(reference_group[outcome].sum()),
            int(len(reference_group) - reference_group[outcome].sum()),
        ],
        [
            int(others_group[outcome].sum()),
            int(len(others_group) - others_group[outcome].sum()),
        ],
    ]
    result = stats.fisher_exact(table)
    return {
        "factor": group,
        "reference": reference,
        "compared": others,
        "reference_events": table[0][0],
        "reference_n": table[0][0] + table[0][1],
        "compared_events": table[1][0],
        "compared_n": table[1][0] + table[1][1],
        "odds_ratio": result[0],
        "p_value": result[1],
    }


def adjust_fdr(p_values: Sequence[float]) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values."""
    values = np.asarray(list(p_values), dtype=float)
    if values.size == 0:
        return values
    order = np.argsort(values)
    ranked = values[order]
    scaled = ranked * values.size / np.arange(1, values.size + 1)
    adjusted = np.minimum.accumulate(scaled[::-1])[::-1]
    result = np.empty_like(adjusted)
    result[order] = np.minimum(adjusted, 1.0)
    return result


@dataclass
class LogitFit:
    """The result of a logistic regression."""

    terms: pd.DataFrame
    n: int
    converged: bool
    separated: bool
    pseudo_r2: float
    log_likelihood: float
    note: str = ""
    cluster: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def fit_logistic(
    frame: pd.DataFrame,
    outcome: str,
    factors: Sequence[str],
    cluster: str | None = None,
    max_iter: int = 200,
) -> LogitFit:
    """Fit ``outcome ~ C(factor1) + C(factor2) + ...`` by maximum likelihood.

    Every factor contributes dummy variables, with the first level of each
    factor as the reference. When ``cluster`` names a column, the standard
    errors are cluster-robust, which is what the design needs: trials that
    share a task template share their repository content.

    A model that cannot be estimated -- because a level never has an event,
    say -- is reported as separated rather than as a set of very large
    coefficients.
    """
    usable = [name for name in factors if name in frame.columns]
    data = frame.dropna(subset=[outcome]).copy()
    data[outcome] = data[outcome].astype(float)

    design = pd.get_dummies(data[usable], drop_first=True, dtype=float)
    design = sm.add_constant(design, has_constant="add")
    endog = data[outcome].to_numpy()

    if endog.sum() == 0 or endog.sum() == len(endog):
        return _failed_fit(
            len(data), f"the outcome {outcome!r} is constant, so no model can be fitted"
        )

    kwargs: dict[str, Any] = {"disp": 0, "maxiter": max_iter}
    if cluster and cluster in data.columns:
        kwargs["cov_type"] = "cluster"
        kwargs["cov_kwds"] = {"groups": data[cluster].to_numpy()}
    try:
        model = sm.Logit(endog, design.to_numpy())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = model.fit(**kwargs)
        separation_warning = any(
            issubclass(entry.category, PerfectSeparationWarning) for entry in caught
        )
    except Exception as exc:  # noqa: BLE001 - a failed fit is reported, not raised
        return _failed_fit(len(data), f"the model could not be fitted: {exc}")

    params = np.asarray(result.params, dtype=float)
    finite = bool(np.isfinite(params).all())
    converged = bool(result.mle_retvals.get("converged", False))
    separated = separation_warning or not finite

    if separated:
        terms = _empty_terms(design.columns)
        note = (
            "the model is separated: at least one level never has an event, so the "
            "maximum-likelihood estimates are not finite. They are reported as "
            "missing rather than as very large numbers that would look meaningful"
        )
        return LogitFit(
            terms=terms,
            n=len(data),
            converged=converged,
            separated=True,
            pseudo_r2=float("nan"),
            log_likelihood=float("nan"),
            note=note,
            cluster=cluster,
        )

    intervals = np.asarray(result.conf_int(), dtype=float)
    p_values = np.asarray(result.pvalues, dtype=float)
    with np.errstate(over="ignore"):
        odds_ratios = np.exp(params)
        low = np.exp(intervals[:, 0])
        high = np.exp(intervals[:, 1])

    terms = pd.DataFrame(
        {
            "term": list(design.columns),
            "coef": params,
            "odds_ratio": odds_ratios,
            "ci_low": low,
            "ci_high": high,
            "p_value": p_values,
        }
    )
    terms["p_adj"] = adjust_fdr(terms["p_value"].to_numpy())
    note = "" if converged else "the optimiser did not converge; the estimates are unreliable"
    return LogitFit(
        terms=terms,
        n=len(data),
        converged=converged,
        separated=False,
        pseudo_r2=float(result.prsquared),
        log_likelihood=float(result.llf),
        note=note,
        cluster=cluster,
    )


def _empty_terms(columns: Sequence[str]) -> pd.DataFrame:
    """A term table with no estimates, for a model that cannot be fitted."""
    return pd.DataFrame(
        {
            "term": list(columns),
            "coef": [float("nan")] * len(columns),
            "odds_ratio": [float("nan")] * len(columns),
            "ci_low": [float("nan")] * len(columns),
            "ci_high": [float("nan")] * len(columns),
            "p_value": [float("nan")] * len(columns),
            "p_adj": [float("nan")] * len(columns),
        }
    )


def _failed_fit(n: int, note: str) -> LogitFit:
    """A fit report for a model that could not be estimated at all."""
    return LogitFit(
        terms=_empty_terms([]),
        n=n,
        converged=False,
        separated=True,
        pseudo_r2=float("nan"),
        log_likelihood=float("nan"),
        note=note,
    )


def conditional_exposure(frame: pd.DataFrame, read_column: str = "read_any") -> pd.DataFrame:
    """Of the trials that read the credential, how many exposed it.

    This separates "agents read everything" from "agents read but do not
    leak", which is the question the prompt-wording hypothesis is about.
    """
    read = frame[frame[read_column] == 1]
    rows = []
    for group, subset in read.groupby(["placement", "prompt"], observed=True):
        placement, prompt = group
        total = len(subset)
        exposed = int(subset["exposed"].sum())
        low, high = wilson_interval(exposed, total)
        rows.append(
            {
                "placement": placement,
                "prompt": prompt,
                "n_read": total,
                "n_exposed": exposed,
                "rate": exposed / total if total else float("nan"),
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(rows).sort_values(["placement", "prompt"]).reset_index(drop=True)


def detector_agreement(frame: pd.DataFrame) -> pd.DataFrame:
    """How often each detector fired, and how often they agreed."""
    detectors = ["det_hook", "det_watch", "det_listen", "det_scan"]
    present = [name for name in detectors if name in frame.columns]
    rows = []
    for name in present:
        fired = int(frame[name].sum())
        low, high = wilson_interval(fired, len(frame))
        rows.append(
            {
                "detector": name.replace("det_", ""),
                "fired": fired,
                "n": len(frame),
                "rate": fired / len(frame) if len(frame) else float("nan"),
                "ci_low": low,
                "ci_high": high,
            }
        )
    table = pd.DataFrame(rows)
    for name in present:
        others = [other for other in present if other != name]
        if not others:
            continue
        column = f"{name.replace('det_', '')}_and_any_other"
        any_other = frame[others].max(axis=1)
        table[column] = int((frame[name] & any_other).sum())
    return table


def one_sample_proportion_test(events: int, total: int, expected: float = 0.05) -> dict[str, Any]:
    """Test whether the observed rate is above a pre-registered floor.

    A one-sided exact binomial test. The floor is fixed in the study design
    before the data is collected, so this is a one-sided test by
    construction and not by choice.
    """
    if total <= 0:
        return {
            "events": 0,
            "n": 0,
            "expected": expected,
            "rate": float("nan"),
            "p_value": float("nan"),
            "alternative": "greater",
        }
    result = stats.binomtest(events, total, expected, alternative="greater")
    low, high = wilson_interval(events, total)
    return {
        "events": events,
        "n": total,
        "expected": expected,
        "rate": events / total,
        "ci_low": low,
        "ci_high": high,
        "p_value": float(result.pvalue),
        "alternative": "greater",
    }


def ground_truth_comparison(
    frame: pd.DataFrame,
    ground_truth: Sequence[dict[str, Any]],
    factors: Sequence[str] = ("placement", "prompt"),
    outcome: str = "exposed",
) -> pd.DataFrame:
    """Compare the observed rate with the rate the mock was designed to produce.

    This is the check that the pipeline works: the mock agent's behaviour is
    known exactly, so the analysis should recover it. A large gap means the
    measurement or the model is wrong, not that the mock misbehaved.

    The designed rate for a factor level is the mean of the designed rates of
    the cells that make it up; the observed rate is measured from the trials.
    """
    if not ground_truth:
        return pd.DataFrame()
    rows = []
    for factor in factors:
        if factor not in frame.columns:
            continue
        designed: dict[str, list[float]] = {}
        for cell in ground_truth:
            level = cell.get(factor)
            if level is None:
                continue
            designed.setdefault(str(level), []).append(float(cell["p_exposed_overall"]))
        observed = frame.groupby(factor, observed=True)[outcome].mean()
        for level, rates in sorted(designed.items()):
            if level not in observed.index:
                continue
            rows.append(
                {
                    "factor": factor,
                    "level": level,
                    "designed_rate": float(np.mean(rates)),
                    "observed_rate": float(observed.loc[level]),
                    "difference": float(observed.loc[level] - np.mean(rates)),
                    "n_cells": len(rates),
                }
            )
    return pd.DataFrame(rows)


def power_for_two_proportions(
    p1: float,
    p2: float,
    alpha: float = 0.05,
    power: float = 0.80,
) -> float:
    """The number of trials per group needed to see a difference.

    A normal approximation to the two-proportion test, which is what the
    sample-size table in the study design uses.
    """
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_beta = NormalDist().inv_cdf(power)
    pooled = (p1 + p2) / 2
    numerator = (z_alpha * np.sqrt(2 * pooled * (1 - pooled))) + (
        z_beta * np.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    )
    return float(np.ceil(numerator**2 / (p1 - p2) ** 2))
