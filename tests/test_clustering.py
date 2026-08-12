"""Unit test dasar untuk src/clustering.py -- dijalankan dengan: pytest tests/"""
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
from src.clustering import cluster_all_equipment, summarize_clusters, _select_best_k


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


@pytest.fixture(scope="module")
def clustered(cleaning_result):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        anomaly_df = detect_anomalies(cleaning_result.cleaned_fuel_data)
        return cluster_all_equipment(cleaning_result.cleaned_fuel_data, anomaly_df)


def test_cluster_label_values_are_known(clustered):
    known = {"HIGH_STABLE", "HIGH_VOLATILE", "LOW_STABLE", "LOW_VOLATILE",
             "INTERMITTENT", "INCREASING_TREND", "HOMOGENEOUS", "INSUFFICIENT_DATA"}
    assert set(clustered["cluster_label"].dropna().unique()) <= known


def test_no_duplicate_equipment(clustered):
    dup = clustered.duplicated(subset=["equipment_category", "equipment_id"]).sum()
    assert dup == 0


def test_select_best_k_avoids_trivial_outlier_clusters():
    # sinyal: 20 titik rapat + 1 outlier ekstrem jauh -- tanpa batas ukuran cluster
    # minimum, silhouette akan tergoda memisahkan si outlier sbg cluster sendiri (k=2, size=1)
    rng = np.random.RandomState(0)
    cluster_a = rng.normal(0, 0.1, size=(20, 2))
    outlier = np.array([[50, 50]])
    X = np.vstack([cluster_a, outlier])

    _, scores_no_floor = _select_best_k(X, max_k=4, min_cluster_size=1)
    assert 2 in scores_no_floor  # tanpa floor, k=2 (isolasi outlier) valid & biasanya menang

    best_k, scores_with_floor = _select_best_k(X, max_k=4, min_cluster_size=3)
    assert 2 not in scores_with_floor  # dengan floor, k=2 (cluster size=1) harus dibuang


def test_summarize_clusters_totals_match(clustered):
    summary = summarize_clusters(clustered)
    assert summary["n_equipment"].sum() == len(clustered)


def test_sparse_equipment_marked_insufficient_data(cleaning_result, clustered):
    valid = cleaning_result.cleaned_fuel_data[
        cleaning_result.cleaned_fuel_data["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])]
    obs_count = valid.groupby(["equipment_category", "equipment_id"]).size()
    sparse = obs_count[obs_count < config.CLUSTERING_MIN_OBSERVATIONS].index
    if len(sparse) == 0:
        pytest.skip("Tidak ada equipment sparse di data ini")
    cat, eq_id = sparse[0]
    row = clustered[(clustered["equipment_category"] == cat) & (clustered["equipment_id"] == eq_id)]
    assert (row["cluster_label"] == "INSUFFICIENT_DATA").all()


def test_intermittent_clusters_have_low_average_active_ratio(clustered):
    # Label INTERMITTENT ditentukan dari rata-rata (centroid) cluster, bukan
    # tiap baris individual -- jadi cek rata-rata per (kategori, cluster_id).
    intermittent = clustered[clustered["cluster_label"] == "INTERMITTENT"]
    if intermittent.empty:
        pytest.skip("Tidak ada cluster INTERMITTENT di data ini")
    cluster_means = intermittent.groupby(["equipment_category", "cluster_id"])["active_day_ratio"].mean()
    assert (cluster_means <= config.CLUSTERING_INTERMITTENT_ACTIVE_RATIO_MAX + 1e-6).all()
