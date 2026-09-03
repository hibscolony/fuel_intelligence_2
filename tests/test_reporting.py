import pandas as pd

from src.reporting import default_reporting_year, select_reporting_period


def test_select_reporting_period_does_not_mix_years():
    cleaned = pd.DataFrame({
        "date": pd.to_datetime(["2025-01-01", "2025-12-31", "2026-01-01"]),
        "fuel_liter": [100.0, 200.0, 900.0],
        "data_status": ["VALID", "VALID", "VALID"],
    })

    result = select_reporting_period(cleaned, 2025)

    assert result.total_liter == 300.0
    assert result.start_date == pd.Timestamp("2025-01-01")
    assert result.end_date == pd.Timestamp("2025-12-31")
    assert result.n_calendar_days == 365
    assert set(result.data["date"].dt.year) == {2025}


def test_partial_year_is_labeled_ytd():
    cleaned = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-08-24"]),
        "fuel_liter": [100.0, 200.0],
        "data_status": ["VALID", "VALID"],
    })

    result = select_reporting_period(cleaned, 2026)

    assert result.is_complete_year is False
    assert result.label == "2026 YTD (s.d. 24 Agu 2026)"
    assert result.n_calendar_days == 236


def test_default_reporting_year_prefers_latest_complete_year():
    dates = pd.date_range("2025-01-01", "2025-12-31", freq="D").append(
        pd.to_datetime(["2026-01-01", "2026-08-24"])
    )
    cleaned = pd.DataFrame({
        "date": dates,
        "fuel_liter": [1.0] * len(dates),
        "data_status": ["VALID"] * len(dates),
    })

    assert default_reporting_year(cleaned) == 2025


def test_year_with_internal_calendar_gap_is_not_complete():
    dates = pd.date_range("2025-01-01", "2025-12-31", freq="D").delete(100)
    cleaned = pd.DataFrame({
        "date": dates,
        "fuel_liter": [1.0] * len(dates),
        "data_status": ["VALID"] * len(dates),
    })

    result = select_reporting_period(cleaned, 2025)

    assert result.is_complete_year is False
    assert result.n_observed_days == 364
    assert result.missing_calendar_days == 1
