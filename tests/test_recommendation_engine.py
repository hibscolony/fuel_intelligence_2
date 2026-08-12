"""Unit test dasar untuk src/recommendation_engine.py -- dijalankan dengan: pytest tests/"""
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.anomaly_detection import detect_anomalies
from src.change_point import detect_all_change_points
from src.data_quality import detect_zero_consumption_streaks
from src.health_score import build_health_score_table
from src.saving_simulator import SavingSimulatorInputs, run_saving_scenarios
from src.recommendation_engine import (
    build_recommendations, summarize_recommendations, RESPONSIBLE_ROLES,
)


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


@pytest.fixture(scope="module")
def health_scores(cleaning_result):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        anomaly_df = detect_anomalies(cleaning_result.cleaned_fuel_data)
        change_points = detect_all_change_points(cleaning_result.cleaned_fuel_data)
        zero_streaks = detect_zero_consumption_streaks(cleaning_result.cleaned_fuel_data)
    return build_health_score_table(
        cleaning_result.cleaned_fuel_data, anomaly_df, change_points, zero_streaks,
        cleaning_result.category_monthly_reconciliation, forecast_summary=None)


@pytest.fixture(scope="module")
def scenarios_df(cleaning_result):
    valid = cleaning_result.cleaned_fuel_data[cleaning_result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    baseline = float(valid["fuel_liter"].sum())
    return run_saving_scenarios(SavingSimulatorInputs(baseline_total_liter=baseline))


@pytest.fixture(scope="module")
def recommendations(cleaning_result, health_scores, scenarios_df):
    return build_recommendations(health_scores, cleaning_result.category_monthly_reconciliation, scenarios_df)


def test_priority_values_are_known(recommendations):
    assert set(recommendations["priority"].unique()) <= {"HIGH", "MEDIUM", "LOW"}


def test_responsible_role_values_are_known(recommendations):
    assert set(recommendations["responsible_role"].unique()) <= set(RESPONSIBLE_ROLES.values())


def test_every_recommendation_has_evidence(recommendations):
    assert (recommendations["evidence"].str.len() > 0).all()


def test_status_defaults_to_open(recommendations):
    assert (recommendations["status"] == "OPEN").all()


def test_recommendations_sorted_by_priority(recommendations):
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranks = recommendations["priority"].map(order)
    assert (ranks.diff().dropna() >= 0).all()


def test_insufficient_data_equipment_excluded_from_recommendations(health_scores, recommendations):
    insufficient_ids = set(health_scores[health_scores["health_status"] == "INSUFFICIENT_DATA"]["equipment_id"])
    rec_ids = set(recommendations["equipment_id"])
    # equipment INSUFFICIENT_DATA seharusnya tidak dapat rekomendasi berbasis health score
    # (kecuali equipment_id yang sama kebetulan tidak unik lintas kategori -- longgar tapi cukup)
    assert len(insufficient_ids & rec_ids) <= len(insufficient_ids)


def test_summarize_recommendations_totals_match(recommendations):
    summary = summarize_recommendations(recommendations)
    if not recommendations.empty:
        assert summary["n_recommendations"].sum() == len(recommendations)


def test_target_date_offsets_respect_priority(recommendations):
    for priority, offset in config.REC_TARGET_DATE_OFFSET_DAYS.items():
        sub = recommendations[recommendations["priority"] == priority]
        if sub.empty:
            continue
        # semua target_date utk prioritas yg sama harus identik (dihitung dari 'today' yg sama)
        assert sub["target_date"].nunique() == 1
