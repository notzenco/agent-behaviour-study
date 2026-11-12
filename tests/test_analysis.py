"""Tests for the statistical methods."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from abstudy.analysis import (
    adjust_fdr,
    conditional_exposure,
    detector_agreement,
    fisher_comparison,
    fit_logistic,
    ground_truth_comparison,
    one_sample_proportion_test,
    power_for_two_proportions,
    proportion_table,
    wilson_interval,
)


def test_the_wilson_interval_brackets_the_proportion():
    low, high = wilson_interval(20, 100)

    assert low < 0.2 < high
    assert low >= 0.0
    assert high <= 1.0


def test_the_wilson_interval_stays_inside_the_unit_interval_at_zero():
    low, high = wilson_interval(0, 20)

    assert low == 0.0
    assert 0.0 < high < 0.2


def test_the_wilson_interval_stays_inside_the_unit_interval_at_one():
    low, high = wilson_interval(20, 20)

    assert 0.8 < low < 1.0
    assert high == 1.0


def test_the_wilson_interval_is_symmetric_around_a_half():
    low, high = wilson_interval(50, 100)

    assert high - 0.5 == pytest.approx(0.5 - low)


def test_the_wilson_interval_of_an_empty_sample_is_nan():
    low, high = wilson_interval(0, 0)

    assert np.isnan(low)
    assert np.isnan(high)


def test_the_wilson_interval_narrows_with_more_trials():
    _, narrow = wilson_interval(5, 10)
    _, wide = wilson_interval(500, 1000)

    assert (wide - 0.5) < (narrow - 0.5)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "placement": ["project_env"] * 10 + ["home_aws"] * 10,
            "prompt": ["neutral"] * 20,
            "task": ["fix"] * 20,
            "read_any": [1] * 8 + [0] * 12,
            "exposed": [1] * 4 + [0] * 16,
            "det_hook": [1] * 4 + [0] * 16,
            "det_scan": [1] * 2 + [0] * 18,
        }
    )


def test_the_proportion_table_reports_a_rate_and_an_interval():
    table = proportion_table(_frame(), ["placement"], "exposed")

    assert list(table["placement"]) == ["home_aws", "project_env"]
    row = table[table["placement"] == "project_env"].iloc[0]
    assert row["n"] == 10
    assert row["events"] == 4
    assert row["rate"] == pytest.approx(0.4)
    assert row["ci_low"] < row["rate"] < row["ci_high"]


def test_the_proportion_table_of_the_whole_frame():
    table = proportion_table(_frame(), [], "exposed")

    assert table.iloc[0]["n"] == 20
    assert table.iloc[0]["events"] == 4


def test_fisher_comparison_reports_an_odds_ratio():
    result = fisher_comparison(_frame(), "exposed", "placement", reference="home_aws")

    assert result["reference"] == "home_aws"
    assert result["compared"] == ["project_env"]
    assert result["reference_events"] == 0
    assert result["compared_events"] == 4
    assert result["p_value"] == pytest.approx(0.0866, abs=1e-3)


def test_fisher_comparison_needs_two_levels():
    with pytest.raises(ValueError, match="only one level"):
        fisher_comparison(_frame(), "exposed", "prompt")


def test_benjamini_hochberg_keeps_the_order_and_the_bounds():
    adjusted = adjust_fdr([0.01, 0.02, 0.03, 0.9])

    assert list(adjusted) == sorted(adjusted)
    assert (adjusted <= 1.0).all()
    assert adjusted[0] == pytest.approx(0.04)


def test_benjamini_hochberg_of_nothing_is_nothing():
    assert adjust_fdr([]).size == 0


def test_the_logistic_model_recovers_a_known_effect():
    rows = []
    for _ in range(200):
        rows.append({"placement": "project_env", "exposed": 1})
        rows.append({"placement": "project_env", "exposed": 1})
        rows.append({"placement": "project_env", "exposed": 0})
        rows.append({"placement": "project_env", "exposed": 0})
        rows.append({"placement": "home_aws", "exposed": 1})
        rows.append({"placement": "home_aws", "exposed": 0})
        rows.append({"placement": "home_aws", "exposed": 0})
        rows.append({"placement": "home_aws", "exposed": 0})
    frame = pd.DataFrame(rows)

    fit = fit_logistic(frame, "exposed", ["placement"])

    assert fit.converged
    assert not fit.separated
    term = fit.terms[fit.terms["term"].str.contains("project_env")].iloc[0]
    assert term["odds_ratio"] > 1
    assert term["p_value"] < 0.05
    assert fit.n == 1600


def test_the_logistic_model_reports_a_separated_fit_instead_of_a_number():
    frame = pd.DataFrame(
        {
            "placement": ["project_env"] * 10 + ["home_aws"] * 10,
            "exposed": [1] * 10 + [0] * 10,
        }
    )

    fit = fit_logistic(frame, "exposed", ["placement"])

    assert fit.separated
    assert "separated" in fit.note


def test_the_logistic_model_refuses_a_constant_outcome():
    frame = pd.DataFrame({"placement": ["a", "b"], "exposed": [0, 0]})

    fit = fit_logistic(frame, "exposed", ["placement"])

    assert fit.separated
    assert "constant" in fit.note


def test_the_logistic_model_can_use_cluster_robust_errors():
    frame = pd.DataFrame(
        {
            "placement": ["project_env"] * 50 + ["home_aws"] * 50,
            "exposed": [1, 0, 1, 0, 0] * 10 + [1, 0, 0, 0, 0] * 10,
            "task": ["t1"] * 50 + ["t2"] * 50,
        }
    )

    fit = fit_logistic(frame, "exposed", ["placement"], cluster="task")

    assert fit.cluster == "task"
    assert fit.converged


def test_conditional_exposure_counts_only_the_trials_that_read():
    table = conditional_exposure(_frame())

    row = table[(table["placement"] == "project_env")].iloc[0]
    assert row["n_read"] == 8
    assert row["n_exposed"] == 4
    assert row["rate"] == pytest.approx(0.5)


def test_detector_agreement_counts_each_detector():
    table = detector_agreement(_frame())

    hook = table[table["detector"] == "hook"].iloc[0]
    assert hook["fired"] == 4
    assert hook["n"] == 20
    assert "hook_and_any_other" in table.columns


def test_the_power_table_matches_the_design_document():
    assert power_for_two_proportions(0.20, 0.30) == 294
    assert power_for_two_proportions(0.10, 0.20) == 199
    assert power_for_two_proportions(0.30, 0.50) == 93


def test_the_ground_truth_comparison_recovers_a_planted_effect():
    truth = [
        {"placement": "project_env", "prompt": "neutral", "p_exposed_overall": 0.5},
        {"placement": "home_aws", "prompt": "neutral", "p_exposed_overall": 0.1},
    ]
    rows = []
    for _ in range(100):
        rows.append({"placement": "project_env", "prompt": "neutral", "exposed": 1})
        rows.append({"placement": "home_aws", "prompt": "neutral", "exposed": 0})
    frame = pd.DataFrame(rows)

    comparison = ground_truth_comparison(frame, truth, factors=("placement",))

    project = comparison[comparison["level"] == "project_env"].iloc[0]
    home = comparison[comparison["level"] == "home_aws"].iloc[0]
    assert project["designed_rate"] == pytest.approx(0.5)
    assert project["observed_rate"] == pytest.approx(1.0)
    assert home["designed_rate"] == pytest.approx(0.1)
    assert home["observed_rate"] == pytest.approx(0.0)


def test_the_ground_truth_comparison_of_nothing_is_empty():
    assert ground_truth_comparison(_frame(), []).empty


def test_the_one_sample_test_finds_a_rate_above_the_floor():
    result = one_sample_proportion_test(60, 200, 0.05)

    assert result["rate"] == pytest.approx(0.3)
    assert result["p_value"] < 0.001
    assert result["alternative"] == "greater"


def test_the_one_sample_test_does_not_find_a_rate_below_the_floor():
    result = one_sample_proportion_test(2, 200, 0.05)

    assert result["p_value"] > 0.05


def test_the_one_sample_test_of_an_empty_sample_is_nan():
    result = one_sample_proportion_test(0, 0, 0.05)

    assert np.isnan(result["p_value"])
