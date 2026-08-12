"""Unit test dasar untuk src/forecast_integration.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.forecast_integration import (
    load_forecast_results, compute_forecast_errors, compute_overall_metrics,
    compute_rolling_performance, classify_model_health, ForecastFormatError,
)


@pytest.fixture(scope="module")
def daily_actual():
    result = run_cleaning_pipeline()
    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    return valid.groupby("date")["fuel_liter"].sum(min_count=1).asfreq("D")


def test_load_forecast_results_raises_without_file_or_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_forecast_results(path=tmp_path / "does_not_exist.csv")


def test_load_forecast_results_placeholder_has_required_columns(daily_actual):
    with pytest.warns(UserWarning):
        df = load_forecast_results(daily_actual_fallback=daily_actual)
    assert {"date", "actual_fuel", "forecast_fuel", "lower_interval",
            "upper_interval", "model_name"} <= set(df.columns)


def test_placeholder_model_name_is_clearly_labeled(daily_actual):
    with pytest.warns(UserWarning):
        df = load_forecast_results(daily_actual_fallback=daily_actual)
    assert "PLACEHOLDER" in df["model_name"].iloc[0]


def test_load_forecast_results_rejects_bad_schema(tmp_path):
    bad = pd.DataFrame({"date": ["2025-01-01"], "wrong_col": [1]})
    p = tmp_path / "forecast_results.csv"
    bad.to_csv(p, index=False)
    with pytest.raises(ForecastFormatError):
        load_forecast_results(path=p)


def test_classify_model_health_thresholds():
    assert classify_model_health(5.0) == "HEALTHY"
    assert classify_model_health(12.0) == "MONITOR"
    assert classify_model_health(20.0) == "RETRAIN"
    assert classify_model_health(float("nan")) == "INSUFFICIENT_DATA"


def test_compute_overall_metrics_are_non_negative(daily_actual):
    with pytest.warns(UserWarning):
        df = load_forecast_results(daily_actual_fallback=daily_actual)
    df_err = compute_forecast_errors(df)
    summary = compute_overall_metrics(df_err)
    assert summary.mae >= 0
    assert summary.rmse >= 0
    assert summary.wape >= 0
    assert 0 <= summary.interval_coverage_pct <= 100


def test_rolling_performance_has_all_configured_windows(daily_actual):
    with pytest.warns(UserWarning):
        df = load_forecast_results(daily_actual_fallback=daily_actual)
    df_err = compute_forecast_errors(df)
    rolling = compute_rolling_performance(df_err)
    assert set(rolling["window_days"].unique()) == set(config.FORECAST_ROLLING_WINDOWS)
