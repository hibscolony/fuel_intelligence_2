"""Unit test dasar untuk src/data_quality.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.data_cleaning import run_cleaning_pipeline
from src.data_quality import compute_dq_kpis, detect_missing_dates, detect_zero_consumption_streaks


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


@pytest.fixture(scope="module")
def kpis(cleaning_result):
    return compute_dq_kpis(cleaning_result.cleaned_fuel_data, cleaning_result.monthly_reconciliation)


def test_valid_transaction_percentage_is_a_percentage(kpis):
    assert 0.0 <= kpis.valid_transaction_percentage <= 100.0


def test_data_completeness_percentage_is_a_percentage(kpis):
    assert 0.0 <= kpis.data_completeness_percentage <= 100.0


def test_overall_status_is_one_of_known_values(kpis):
    assert kpis.overall_status in {"PASS", "REVIEW", "FAILED"}


def test_reconciliation_status_is_one_of_known_values(kpis):
    assert kpis.reconciliation_status in {"PASS", "REVIEW", "FAILED"}


def test_no_negative_counts(kpis):
    for field in ["duplicate_count", "missing_equipment_count", "invalid_value_count",
                  "unusually_high_count", "invalid_date_count"]:
        assert getattr(kpis, field) >= 0


def test_missing_dates_report_has_expected_columns(cleaning_result):
    missing = detect_missing_dates(cleaning_result.cleaned_fuel_data)
    assert list(missing.columns) == ["date", "issue"]


def test_zero_consumption_streaks_only_above_threshold(cleaning_result):
    import config
    streaks = detect_zero_consumption_streaks(cleaning_result.cleaned_fuel_data)
    if not streaks.empty:
        assert (streaks["longest_gap_days"] >= config.ZERO_CONSUMPTION_STREAK_DAYS).all()


def test_global_source_gap_does_not_penalize_equipment_streak():
    cleaned = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-20"]),
        "equipment_category": ["BUS", "BUS"],
        "equipment_id": ["01", "01"],
        "fuel_liter": [20.0, 20.0],
        "data_status": ["VALID", "VALID"],
    })
    source_calendar = pd.DataFrame({
        "date": pd.date_range("2026-01-01", "2026-01-20", freq="D"),
        "known_source_coverage": [True] + [False] * 18 + [True],
    })

    streaks = detect_zero_consumption_streaks(
        cleaned,
        min_streak_days=5,
        source_coverage_calendar=source_calendar,
    )

    assert streaks.empty


def test_category_reconciliation_covers_all_categories(cleaning_result):
    # COMPRESSOR baru muncul di layout tahun 2026 -- pastikan kategori 2025
    # tidak hilang, tapi izinkan kategori baru bertambah
    core_categories = {"RTGC", "HEAD_TRUCK", "SUPPORT", "KEND_OPS", "BUS", "ELF", "MODUL"}
    found = set(cleaning_result.category_monthly_reconciliation["equipment_category"].unique())
    assert core_categories <= found
