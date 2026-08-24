"""Tests untuk pembentukan target harian forecasting."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.forecast_data import (
    build_daily_refueling_series,
    build_forecast_calendar_audit,
    split_complete_daily_segments,
    select_complete_daily_segment,
    build_forecast_coverage_segments,
)


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


def _ujb_row(date, fuel, event_key, event_time, eq="101", source_row=1):
    return {
        "date": pd.Timestamp(date),
        "equipment_category": "HEAD_TRUCK",
        "equipment_id": eq,
        "fuel_liter": fuel,
        "data_status": "VALID",
        "source_file": "ujb_dashboard_scrape",
        "source_row": source_row,
        "source_system": "UJB",
        "source_event_key": event_key,
        "event_time": event_time,
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


def test_coverage_gap_splits_series_without_compressing_calendar():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0, source_row=1),
        _row("2025-01-02", 110.0, source_row=2),
        _ujb_row("2025-01-05", 50.0, "evt-1", "08:00:00", source_row=3),
        _ujb_row("2025-01-06", 60.0, "evt-2", "08:00:00", source_row=4),
    ])

    s = build_daily_refueling_series(cleaned, strict_source_coverage=False)
    assert pd.isna(s.loc["2025-01-03"])
    assert pd.isna(s.loc["2025-01-04"])

    segments = split_complete_daily_segments(s)
    assert len(segments) == 2
    assert list(segments[0].index) == list(pd.date_range("2025-01-01", "2025-01-02"))
    assert list(segments[1].index) == list(pd.date_range("2025-01-05", "2025-01-06"))

    summary = build_forecast_coverage_segments(cleaned, model_ready_min_days=2)
    assert summary["n_days"].tolist() == [2, 2]
    assert summary["is_latest"].tolist() == [False, True]
    assert summary["model_ready"].tolist() == [True, True]


def test_training_cutoff_selects_historical_segment_without_crossing_gap():
    idx = pd.date_range("2025-12-28", "2026-01-07", freq="D")
    s = pd.Series(
        [10.0, 11.0, 12.0, 13.0, float("nan"), float("nan"), float("nan"),
         20.0, 21.0, 22.0, 23.0],
        index=idx,
        name="fuel_liter",
    )

    training = select_complete_daily_segment(s, end_at="2025-12-31", min_days=4)
    assert training.index.min() == pd.Timestamp("2025-12-28")
    assert training.index.max() == pd.Timestamp("2025-12-31")
    assert training.tolist() == [10.0, 11.0, 12.0, 13.0]

    latest = select_complete_daily_segment(s, latest=True, min_days=4)
    assert latest.index.min() == pd.Timestamp("2026-01-04")
    assert latest.index.max() == pd.Timestamp("2026-01-07")


def test_duplicate_group_counted_once_in_forecast_target():
    cleaned = pd.DataFrame([
        _row("2025-01-01", 100.0, status="DUPLICATE", source_row=10),
        _row("2025-01-01", 100.0, status="DUPLICATE", source_row=11),
        _row("2025-01-01", 50.0, status="VALID", eq="EQ-2", source_row=12),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2025-01-01"] == pytest.approx(150.0)


def test_multiple_ujb_events_same_unit_day_are_all_counted():
    cleaned = pd.DataFrame([
        _ujb_row("2026-08-18", 50.0, "evt-1", "08:00:00", source_row=1),
        _ujb_row("2026-08-18", 40.0, "evt-2", "13:00:00", source_row=2),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2026-08-18"] == pytest.approx(90.0)


def test_repeated_ujb_event_key_is_counted_once():
    cleaned = pd.DataFrame([
        _ujb_row("2026-08-18", 50.0, "evt-1", "08:00:00", source_row=1),
        _ujb_row("2026-08-18", 50.0, "evt-1", "08:00:00", source_row=2),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2026-08-18"] == pytest.approx(50.0)


def test_unkeyed_ujb_exact_event_uses_time_fallback_dedup():
    cleaned = pd.DataFrame([
        _ujb_row("2026-08-18", 50.0, None, "08:00:00", source_row=1),
        _ujb_row("2026-08-18", 50.0, None, "08:00:00", source_row=2),
        _ujb_row("2026-08-18", 25.0, None, "13:00:00", source_row=3),
    ])

    s = build_daily_refueling_series(cleaned)
    assert s.loc["2026-08-18"] == pytest.approx(75.0)


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
