"""Unit test dasar untuk src/anomaly_detection.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.anomaly_detection import detect_anomalies, classify_severity, summarize_anomalies


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


@pytest.fixture(scope="module")
def anomaly_df(cleaning_result):
    return detect_anomalies(cleaning_result.cleaned_fuel_data)


def test_severity_values_are_known(anomaly_df):
    known = {"NORMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL", "INSUFFICIENT_DATA"}
    assert set(anomaly_df["severity"].unique()) <= known


def test_equipment_with_few_observations_marked_insufficient_data(cleaning_result, anomaly_df):
    valid = cleaning_result.cleaned_fuel_data[
        cleaning_result.cleaned_fuel_data["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])]
    obs_count = valid.groupby(["equipment_category", "equipment_id"]).size()
    sparse_equipment = obs_count[obs_count < config.ANOMALY_MIN_OBSERVATIONS].index

    if len(sparse_equipment) == 0:
        pytest.skip("Tidak ada equipment dengan observasi sedikit di data ini")
    cat, eq_id = sparse_equipment[0]
    rows = anomaly_df[(anomaly_df["equipment_category"] == cat) & (anomaly_df["equipment_id"] == eq_id)]
    assert (rows["severity"] == "INSUFFICIENT_DATA").all()


def test_classify_severity_escalates_with_isolation_forest_agreement():
    # z-score borderline LOW (2.5), isolation forest netral -> tetap LOW
    assert classify_severity(2.5, 0.1) == "LOW"
    # z-score borderline LOW (2.5), isolation forest jg menganggap anomali -> naik ke MEDIUM
    assert classify_severity(2.5, -0.1) == "MEDIUM"


def test_classify_severity_normal_when_no_z_score():
    assert classify_severity(np.nan, -0.5) == "NORMAL"


def test_deviation_percentage_is_finite_or_nan(anomaly_df):
    finite_or_nan = anomaly_df["deviation_percentage"].apply(
        lambda v: pd.isna(v) or np.isfinite(v))
    assert finite_or_nan.all()


def test_summarize_anomalies_totals_match(anomaly_df):
    summary = summarize_anomalies(anomaly_df)
    assert summary["n_records"].sum() == len(anomaly_df)


def test_no_row_missing_anomaly_reason(anomaly_df):
    assert anomaly_df["anomaly_type"].notna().all()
