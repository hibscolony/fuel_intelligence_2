"""Tests untuk evaluasi forecasting multi-horizon."""
import numpy as np
import pandas as pd
import pytest

from src.forecast_evaluation import (
    build_multi_horizon_backtest,
    summarize_multi_horizon_backtest,
    residual_quantiles_by_horizon,
    choose_calibration_horizon,
    apply_horizon_prediction_interval,
    rank_model_horizon_summaries,
    validate_daily_history,
)


def _daily_series(n=220):
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    values = 1000 + np.arange(n) * 0.5 + 50 * np.sin(2 * np.pi * np.arange(n) / 7)
    return pd.Series(values, index=idx, name="fuel_liter")


def test_irregular_calendar_is_rejected_instead_of_silently_shifted():
    s = _daily_series(20).drop(pd.Timestamp("2025-01-10"))
    with pytest.raises(ValueError, match="kalender harian"):
        validate_daily_history(s)


def test_nan_coverage_gap_is_rejected_instead_of_dropna():
    s = _daily_series(20)
    s.iloc[5] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        validate_daily_history(s)


def test_multi_horizon_backtest_targets_true_calendar_horizon():
    s = _daily_series(220)
    horizons = (1, 7, 14, 30)
    bt = build_multi_horizon_backtest(
        s, "seasonal_naive_7", horizons=horizons,
        evaluation_days=70, origin_step_days=14, min_train_days=60,
    )
    ok = bt[bt["status"] == "OK"]
    assert not ok.empty
    assert set(ok["horizon_days"].unique()) == set(horizons)
    delta_days = (ok["target_date"] - ok["origin_date"]).dt.days
    assert (delta_days == ok["horizon_days"]).all()


def test_summary_returns_one_row_per_horizon():
    s = _daily_series(220)
    horizons = (1, 7, 30)
    bt = build_multi_horizon_backtest(
        s, "moving_average_7", horizons=horizons,
        evaluation_days=60, origin_step_days=15, min_train_days=60,
    )
    summary = summarize_multi_horizon_backtest(bt)
    assert summary["horizon_days"].tolist() == list(horizons)
    assert (summary["n_forecasts"] > 0).all()
    assert (summary["mae"] >= 0).all()
    assert (summary["rmse"] >= 0).all()
    assert (summary["wape"] >= 0).all()


def test_residual_quantiles_are_horizon_specific():
    s = _daily_series(220)
    bt = build_multi_horizon_backtest(
        s, "naive", horizons=(1, 7, 30),
        evaluation_days=60, origin_step_days=15, min_train_days=60,
    )
    q = residual_quantiles_by_horizon(bt)
    assert q["horizon_days"].tolist() == [1, 7, 30]
    assert (q["upper_residual"] >= q["lower_residual"]).all()
    assert (q["n_residuals"] > 0).all()


def test_choose_calibration_horizon_is_conservative():
    available = (1, 7, 14, 30, 60, 90)
    assert choose_calibration_horizon(1, available) == 1
    assert choose_calibration_horizon(5, available) == 7
    assert choose_calibration_horizon(20, available) == 30
    assert choose_calibration_horizon(120, available) == 90


def test_prediction_interval_uses_next_conservative_horizon_and_nonnegative_floor():
    q = pd.DataFrame([
        {"horizon_days": 1, "lower_residual": -5.0, "upper_residual": 5.0, "n_residuals": 20},
        {"horizon_days": 7, "lower_residual": -30.0, "upper_residual": 20.0, "n_residuals": 18},
        {"horizon_days": 30, "lower_residual": -50.0, "upper_residual": 40.0, "n_residuals": 15},
    ])
    interval = apply_horizon_prediction_interval(10.0, 5, q)
    assert interval["interval_calibration_horizon"] == 7
    assert interval["lower"] == 0.0
    assert interval["upper"] == pytest.approx(30.0)
    assert interval["interval_extrapolated"] is False
    assert interval["interval_calibration_independent"] is False


def test_prediction_interval_flags_horizon_beyond_calibration_range():
    q = pd.DataFrame([
        {"horizon_days": 30, "lower_residual": -10.0, "upper_residual": 10.0, "n_residuals": 12},
        {"horizon_days": 90, "lower_residual": -25.0, "upper_residual": 30.0, "n_residuals": 10},
    ])
    interval = apply_horizon_prediction_interval(100.0, 120, q)
    assert interval["interval_calibration_horizon"] == 90
    assert interval["interval_extrapolated"] is True


def test_model_ranking_is_separate_for_each_horizon_and_prefers_complete_evaluation():
    summary = pd.DataFrame([
        {"model_name": "A", "horizon_days": 1, "n_forecasts": 10, "mae": 10.0, "rmse": 12.0, "wape": 5.0, "bias": 2.0},
        {"model_name": "B", "horizon_days": 1, "n_forecasts": 10, "mae": 8.0, "rmse": 10.0, "wape": 6.0, "bias": 1.0},
        {"model_name": "A", "horizon_days": 30, "n_forecasts": 10, "mae": 30.0, "rmse": 35.0, "wape": 12.0, "bias": 5.0},
        {"model_name": "B", "horizon_days": 30, "n_forecasts": 10, "mae": 20.0, "rmse": 25.0, "wape": 9.0, "bias": -2.0},
        {"model_name": "C", "horizon_days": 30, "n_forecasts": 8, "mae": 10.0, "rmse": 12.0, "wape": 4.0, "bias": 0.0},
    ])
    ranked = rank_model_horizon_summaries(summary)
    winners = ranked[ranked["best_for_horizon"]].set_index("horizon_days")
    assert winners.loc[1, "model_name"] == "A"
    assert winners.loc[30, "model_name"] == "B"
    incomplete_c = ranked[(ranked["horizon_days"] == 30) & (ranked["model_name"] == "C")].iloc[0]
    assert bool(incomplete_c["evaluation_complete"]) is False
