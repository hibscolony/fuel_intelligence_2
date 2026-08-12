"""Unit test dasar untuk src/change_point.py -- dijalankan dengan: pytest tests/"""
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from src.data_cleaning import run_cleaning_pipeline
from src.change_point import (
    detect_all_change_points, detect_change_points_for_equipment,
    summarize_change_points,
)


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


@pytest.fixture(scope="module")
def change_points(cleaning_result):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return detect_all_change_points(cleaning_result.cleaned_fuel_data)


def test_change_direction_values_are_known(change_points):
    known = {"INCREASE", "DECREASE", "VOLATILITY_INCREASE", "VOLATILITY_DECREASE", "MINOR_SHIFT"}
    assert set(change_points["change_direction"].unique()) <= known


def test_confidence_values_are_known(change_points):
    assert set(change_points["confidence"].unique()) <= {"LOW", "MEDIUM", "HIGH"}


def test_review_status_defaults_to_pending(change_points):
    assert (change_points["review_status"] == "PENDING_REVIEW").all()


def test_no_change_points_from_sparse_equipment(cleaning_result, change_points):
    valid = cleaning_result.cleaned_fuel_data[
        cleaning_result.cleaned_fuel_data["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])]
    obs_count = valid.groupby(["equipment_category", "equipment_id"]).size()
    sparse = set(obs_count[obs_count < config.CHANGE_POINT_MIN_OBSERVATIONS].index)
    reported = set(zip(change_points["equipment_category"], change_points["equipment_id"]))
    assert reported.isdisjoint(sparse)


def test_detect_change_points_handles_constant_signal():
    dates = pd.Series(pd.date_range("2025-01-01", periods=40))
    values = np.full(40, 100.0)
    result = detect_change_points_for_equipment(dates, values)
    assert result == []  # sinyal konstan -- tidak ada perubahan utk dideteksi


def test_detect_change_points_finds_an_obvious_level_shift():
    dates = pd.Series(pd.date_range("2025-01-01", periods=60))
    values = np.concatenate([np.full(30, 50.0), np.full(30, 200.0)])
    result = detect_change_points_for_equipment(dates, values)
    assert len(result) >= 1
    assert result[0]["change_direction"] == "INCREASE"


def test_summarize_change_points_totals_match(change_points):
    summary = summarize_change_points(change_points)
    if not change_points.empty:
        assert summary["n_change_points"].sum() == len(change_points)
