"""Tests untuk pembentukan target harian forecasting."""
import pandas as pd
import pytest

from src.forecast_data import build_daily_refueling_series, build_forecast_calendar_audit


def _row(date, fuel, status="VALID", eq="EQ-1", cat="TEST", source_row=1):
    return {
        "date": pd.Timestamp(date),
        "equipment_category": cat,
        "equipment_id": eq,
        "fuel_liter": fuel,
        "data_status": status,
        "source_file": "dummy.xlsx",
        "source_row": source_row,
    }


def test_status_only_day_becomes_zero_without_breaking_calendar():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0),
        _row("2025-01-02", None, status="STATUS_ONLY"),
        _row("2025-01-03", 120.0),
    ])

    s = build_daily_refueling_series(cleaned)

    assert list(s.index) == list(pd.date_range("2025-01-01", "2025-01-03", freq="D"))
    assert s.loc["2025-01-01"] == pytest.approx(100.0)
    assert s.loc["2025-01-02"] == pytest.approx(0.0)
    assert s.loc["2025-01-03"] == pytest.approx(120.0)
    assert not s.isna().any()


def test_true_source_coverage_gap_is_not_silently_treated_as_zero():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0),
        _row("2025-01-03", 120.0),
    ])

    with pytest.raises(ValueError, match="tanpa baris sumber"):
        build_daily_refueling_series(cleaned, strict_source_coverage=True)

    s = build_daily_refueling_series(cleaned, strict_source_coverage=False)
    assert pd.isna(s.loc["2025-01-02"])

    audit = build_forecast_calendar_audit(cleaned)
    gap = audit.loc[audit["date"] == pd.Timestamp("2025-01-02")].iloc[0]
    assert gap["calendar_status"] == "SOURCE_COVERAGE_GAP"


def test_duplicate_group_counted_once_in_forecast_target():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0, status="DUPLICATE", source_row=10),
        _row("2025-01-01", 100.0, status="DUPLICATE", source_row=11),
        _row("2025-01-01", 50.0, status="VALID", eq="EQ-2", source_row=12),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2025-01-01"] == pytest.approx(150.0)


def test_negative_and_invalid_date_values_do_not_enter_target():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0),
        _row("2025-01-01", -999.0, status="NEGATIVE_VALUE", eq="EQ-2", source_row=2),
        {
            **_row("2025-01-02", 200.0, status="INVALID_DATE", eq="EQ-3", source_row=3),
            "date": pd.NaT,
        },
        _row("2025-01-02", 80.0, eq="EQ-4", source_row=4),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2025-01-01"] == pytest.approx(100.0)
    assert s.loc["2025-01-02"] == pytest.approx(80.0)
