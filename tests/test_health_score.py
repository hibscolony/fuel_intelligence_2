"""Unit test dasar untuk src/health_score.py -- dijalankan dengan: pytest tests/"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.anomaly_detection import detect_anomalies
from src.change_point import detect_all_change_points
from src.data_quality import detect_zero_consumption_streaks
from src.health_score import build_health_score_table, classify_health_status, recommend_action


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


def test_health_score_always_between_0_and_100(health_scores):
    assert (health_scores["health_score"] >= 0).all()
    assert (health_scores["health_score"] <= 100).all()


def test_health_status_values_are_known(health_scores):
    known = {"HEALTHY", "MONITOR", "REVIEW", "CRITICAL", "INSUFFICIENT_DATA"}
    assert set(health_scores["health_status"].unique()) <= known


def test_weights_sum_to_one():
    assert abs(sum(config.HEALTH_SCORE_WEIGHTS.values()) - 1.0) < 1e-9


def test_classify_health_status_respects_min_observations():
    assert classify_health_status(95.0, n_observations=3) == "INSUFFICIENT_DATA"
    assert classify_health_status(95.0, n_observations=50) == "HEALTHY"


def test_classify_health_status_bands():
    assert classify_health_status(90, 50) == "HEALTHY"
    assert classify_health_status(75, 50) == "MONITOR"
    assert classify_health_status(60, 50) == "REVIEW"
    assert classify_health_status(20, 50) == "CRITICAL"


def test_recommend_action_insufficient_data_overrides_everything():
    row = pd.Series({"health_status": "INSUFFICIENT_DATA", "reconciliation_penalty": 100,
                      "critical_anomaly_count": 10, "change_point_penalty": 100,
                      "anomaly_count": 100, "missing_data_penalty": 100})
    assert recommend_action(row) == "Kumpulkan lebih banyak data sebelum dievaluasi"


def test_recommend_action_no_action_when_all_clear():
    row = pd.Series({"health_status": "HEALTHY", "reconciliation_penalty": 0,
                      "critical_anomaly_count": 0, "change_point_penalty": 0,
                      "anomaly_count": 0, "missing_data_penalty": 0})
    assert recommend_action(row) == "Tidak ada tindakan"


def test_every_equipment_has_a_recommended_action(health_scores):
    assert health_scores["recommended_action"].notna().all()


def test_no_duplicate_equipment_rows(health_scores):
    dup = health_scores.duplicated(subset=["equipment_category", "equipment_id"]).sum()
    assert dup == 0
