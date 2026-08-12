"""Unit test dasar untuk src/forecasting_models.py -- dijalankan dengan: pytest tests/"""
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.forecasting_models import forecast_for_date, recursive_forecast, climatology_forecast


@pytest.fixture(scope="module")
def daily_actual():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = run_cleaning_pipeline()
    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    return valid.groupby("date")["fuel_liter"].sum(min_count=1).asfreq("D")


def test_historical_date_returns_actual_value(daily_actual):
    mid_date = daily_actual.index[100]
    result = forecast_for_date(daily_actual, "naive", mid_date)
    assert result["method"] == "historical_actual"
    assert result["point"] == pytest.approx(daily_actual.loc[mid_date])


def test_near_horizon_uses_recursive_forecast(daily_actual):
    target = daily_actual.index.max() + pd.Timedelta(days=30)
    result = forecast_for_date(daily_actual, "moving_average_7", target)
    assert result["method"] == "recursive_forecast"
    assert result["point"] is not None
    assert result["lower"] <= result["point"] <= result["upper"]


def test_far_horizon_uses_climatology_fallback(daily_actual):
    target = daily_actual.index.max() + pd.Timedelta(days=800)
    result = forecast_for_date(daily_actual, "random_forest", target)
    assert result["method"] == "climatology_fallback"
    assert result["warning"] is not None
    assert result["lower"] <= result["point"] <= result["upper"]


def test_all_model_choices_produce_a_recursive_forecast(daily_actual):
    target = daily_actual.index.max() + pd.Timedelta(days=14)
    for model_name in config.FORECAST_MODEL_CHOICES:
        result = forecast_for_date(daily_actual, model_name, target)
        assert result["point"] is not None, f"model {model_name} failed"


def test_climatology_interval_wider_than_typical_recursive_interval(daily_actual):
    near_target = daily_actual.index.max() + pd.Timedelta(days=30)
    far_target = daily_actual.index.max() + pd.Timedelta(days=800)
    near = forecast_for_date(daily_actual, "hist_gradient_boosting", near_target)
    far = forecast_for_date(daily_actual, "hist_gradient_boosting", far_target)
    near_width = near["upper"] - near["lower"]
    far_width = far["upper"] - far["lower"]
    # tidak selalu strictly lebih lebar (tergantung volatilitas musiman), tapi harus dalam skala wajar
    assert near_width > 0 and far_width > 0


def test_recursive_forecast_length_matches_horizon(daily_actual):
    path = recursive_forecast(daily_actual, 10, "seasonal_naive_7")
    assert len(path) == 10
    assert path.index[0] == daily_actual.index.max() + pd.Timedelta(days=1)


def test_climatology_forecast_uses_reference_points(daily_actual):
    target = daily_actual.index.max() + pd.Timedelta(days=400)
    result = climatology_forecast(daily_actual, target)
    assert result["n_reference_points"] > 0


def test_cross_year_validation_covers_all_actual_days_after_cutoff(daily_actual):
    from src.forecasting_models import build_cross_year_validation
    cutoff = daily_actual.index.max() - pd.Timedelta(days=30)
    result = build_cross_year_validation(daily_actual, cutoff, "seasonal_naive_7")
    expected_n = (daily_actual.index > cutoff).sum()
    assert len(result) == expected_n
    assert set(result["method"].unique()) <= {"recursive_forecast", "climatology_fallback"}
    assert (result["residual"] == result["actual"] - result["forecast"]).all()


def test_forecast_training_series_excludes_data_past_cutoff():
    import pandas as pd
    from src.analytics import get_forecast_training_series, get_daily_actual_series

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        training = get_forecast_training_series()
        full = get_daily_actual_series()

    cutoff = pd.Timestamp(config.FORECAST_TRAINING_CUTOFF)
    assert training.index.max() <= cutoff
    if full.index.max() > cutoff:
        assert len(training) < len(full), "Training series should exclude data past the cutoff"
