"""
clustering.py
=============
Segmentasi equipment berdasarkan pola konsumsi, memakai K-Means -- dijalankan
TERPISAH PER KATEGORI (equipment_category), karena skala konsumsi berbeda
jauh antar kategori (RTGC vs MODUL, misalnya) sehingga tidak adil di-cluster
bersamaan. Kategori dengan equipment terlalu sedikit
(< config.CLUSTERING_MIN_EQUIPMENT_PER_CATEGORY) tidak di-cluster dengan
K-Means -- diberi label langsung dari aturan sederhana supaya tidak
memaksakan model pada sampel yang terlalu kecil.

Jumlah cluster (k) dipilih otomatis per kategori lewat silhouette score
tertinggi di antara k=2..config.CLUSTERING_MAX_K.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

FEATURE_COLS = ["mean_daily_fuel", "median_daily_fuel", "standard_deviation",
                "coefficient_of_variation", "maximum_daily_fuel", "zero_day_ratio",
                "anomaly_ratio", "monthly_growth", "active_day_ratio"]


def build_clustering_features(cleaned: pd.DataFrame, anomaly_df: pd.DataFrame) -> pd.DataFrame:
    """Bangun fitur per equipment untuk clustering.

    CATATAN ISTILAH: nama kolom mengikuti spesifikasi ("*_daily_fuel"), tapi
    secara teknis dihitung per TRANSAKSI pengisian (bukan per hari kalender),
    karena refueling tidak terjadi tiap hari.
    """
    valid = cleaned[cleaned["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])].copy()

    base = valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].agg(
        mean_daily_fuel="mean", median_daily_fuel="median", standard_deviation="std",
        maximum_daily_fuel="max", n_observations="count",
    ).reset_index()
    base["coefficient_of_variation"] = (base["standard_deviation"] / base["mean_daily_fuel"]).replace(
        [np.inf, -np.inf], np.nan)

    zero_ratio = (valid.assign(is_zero=valid["fuel_liter"] == 0)
                  .groupby(["equipment_category", "equipment_id"])["is_zero"].mean()
                  .rename("zero_day_ratio"))

    date_range = valid.groupby(["equipment_category", "equipment_id"])["date"].agg(
        first_date="min", last_date="max")
    date_range["range_days"] = (date_range["last_date"] - date_range["first_date"]).dt.days + 1
    n_records = valid.groupby(["equipment_category", "equipment_id"]).size().rename("n_records")
    active_ratio = (n_records / date_range["range_days"].replace(0, np.nan)).clip(upper=1.0).rename(
        "active_day_ratio")

    def _monthly_growth(sub: pd.DataFrame) -> float:
        monthly = sub.groupby(sub["date"].dt.to_period("M"))["fuel_liter"].sum().sort_index()
        if len(monthly) < 3:
            return np.nan
        x = np.arange(len(monthly))
        y = monthly.values
        slope = np.polyfit(x, y, 1)[0]
        mean_y = y.mean()
        return (slope / mean_y * 100) if mean_y else np.nan

    growth = valid.groupby(["equipment_category", "equipment_id"]).apply(_monthly_growth).rename(
        "monthly_growth")

    if not anomaly_df.empty:
        scored = anomaly_df[anomaly_df["severity"] != "INSUFFICIENT_DATA"]
        anomaly_ratio = (scored.assign(is_anom=scored["severity"] != "NORMAL")
                         .groupby(["equipment_category", "equipment_id"])["is_anom"].mean()
                         .rename("anomaly_ratio"))
    else:
        anomaly_ratio = pd.Series(dtype=float, name="anomaly_ratio")

    features = (base
                .merge(zero_ratio, on=["equipment_category", "equipment_id"], how="left")
                .merge(active_ratio, on=["equipment_category", "equipment_id"], how="left")
                .merge(growth, on=["equipment_category", "equipment_id"], how="left")
                .merge(anomaly_ratio, on=["equipment_category", "equipment_id"], how="left"))
    features["anomaly_ratio"] = features["anomaly_ratio"].fillna(0.0)
    return features


def _select_best_k(X_scaled: np.ndarray, max_k: int, min_cluster_size: int = 3) -> tuple[int, dict[int, float]]:
    """Pilih k dengan silhouette score tertinggi di antara k=2..max_k.

    k yang menghasilkan cluster ber-anggota < `min_cluster_size` DIBUANG dari
    kandidat sebelum memilih -- tanpa ini, silhouette sering "tertipu"
    memilih k yang hanya memisahkan 1-2 outlier ekstrem, meninggalkan
    mayoritas equipment tetap tercampur tak terdiferensiasi dalam satu cluster.
    """
    scores = {}
    max_k = min(max_k, X_scaled.shape[0] - 1)
    for k in range(2, max_k + 1):
        try:
            labels = KMeans(n_clusters=k, random_state=config.RANDOM_STATE, n_init=10).fit_predict(X_scaled)
            if len(set(labels)) < 2:
                continue
            cluster_sizes = pd.Series(labels).value_counts()
            if cluster_sizes.min() < min_cluster_size:
                continue  # buang k ini -- ada cluster yg cuma menangkap outlier
            scores[k] = silhouette_score(X_scaled, labels)
        except Exception:
            continue
    if not scores:
        return 1, scores
    best_k = max(scores, key=scores.get)
    return best_k, scores


def _label_cluster(centroid: pd.Series, global_medians: pd.Series, single_cluster: bool = False) -> str:
    """Aturan pelabelan bisnis dari karakteristik centroid (dlm unit asli,
    sudah di-inverse-transform dari hasil scaling).

    Jika `single_cluster=True` (K-Means tidak menemukan struktur >1 cluster
    yang valid utk kategori ini), label HIGH/LOW_STABLE/VOLATILE TIDAK
    dipakai -- perbandingan "tinggi/rendah" jadi tidak bermakna saat cuma ada
    satu kelompok. INTERMITTENT/INCREASING_TREND tetap dicek karena keduanya
    memakai ambang absolut, bukan perbandingan relatif antar cluster.
    """
    if centroid["active_day_ratio"] <= config.CLUSTERING_INTERMITTENT_ACTIVE_RATIO_MAX:
        return "INTERMITTENT"
    if centroid["monthly_growth"] >= config.CLUSTERING_HIGH_GROWTH_THRESHOLD_PCT:
        return "INCREASING_TREND"
    if single_cluster:
        return "HOMOGENEOUS"

    is_high_volume = centroid["mean_daily_fuel"] >= global_medians["mean_daily_fuel"]
    is_volatile = centroid["coefficient_of_variation"] >= global_medians["coefficient_of_variation"]

    if is_high_volume and is_volatile:
        return "HIGH_VOLATILE"
    if is_high_volume and not is_volatile:
        return "HIGH_STABLE"
    if not is_high_volume and not is_volatile:
        return "LOW_STABLE"
    return "LOW_VOLATILE"  # kombinasi ke-5 di luar 5 label spesifikasi, tapi tetap informatif


def _describe_cluster(centroid: pd.Series) -> str:
    return (f"mean~{centroid['mean_daily_fuel']:.0f}L, CV~{centroid['coefficient_of_variation']:.2f}, "
            f"active_ratio~{centroid['active_day_ratio']:.2f}, growth~{centroid['monthly_growth']:.1f}%/bulan")


def cluster_category(features: pd.DataFrame, category: str) -> pd.DataFrame:
    """Jalankan clustering untuk SATU kategori. Kategori dengan equipment
    terlalu sedikit diberi label langsung dari aturan (tanpa K-Means).
    """
    sub = features[features["equipment_category"] == category].copy()
    sub = sub.dropna(subset=FEATURE_COLS)

    if len(sub) < config.CLUSTERING_MIN_EQUIPMENT_PER_CATEGORY:
        global_medians = sub[FEATURE_COLS].median() if len(sub) else pd.Series(0, index=FEATURE_COLS)
        sub["cluster_id"] = -1  # -1 = tidak di-cluster (sampel terlalu sedikit utk K-Means)
        sub["cluster_label"] = sub.apply(
            lambda r: _label_cluster(r, global_medians, single_cluster=False), axis=1) if len(sub) else None
        sub["cluster_characteristics"] = sub.apply(_describe_cluster, axis=1) if len(sub) else None
        sub["silhouette_score"] = np.nan
        return sub

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(sub[FEATURE_COLS])
    min_cluster_size = max(3, int(0.05 * len(sub)))
    best_k, scores = _select_best_k(X_scaled, config.CLUSTERING_MAX_K, min_cluster_size)

    if best_k == 1:
        sub["cluster_id"] = 0
        sub["silhouette_score"] = np.nan
    else:
        model = KMeans(n_clusters=best_k, random_state=config.RANDOM_STATE, n_init=10)
        sub["cluster_id"] = model.fit_predict(X_scaled)
        sub["silhouette_score"] = scores.get(best_k, np.nan)

    global_medians = sub[FEATURE_COLS].median()
    centroid_stats = sub.groupby("cluster_id")[FEATURE_COLS].mean()
    single_cluster = best_k == 1
    label_map = {cid: _label_cluster(row, global_medians, single_cluster) for cid, row in centroid_stats.iterrows()}
    char_map = {cid: _describe_cluster(row) for cid, row in centroid_stats.iterrows()}

    sub["cluster_label"] = sub["cluster_id"].map(label_map)
    sub["cluster_characteristics"] = sub["cluster_id"].map(char_map)
    return sub


def cluster_all_equipment(cleaned: pd.DataFrame, anomaly_df: pd.DataFrame) -> pd.DataFrame:
    """Entry point utama: bangun fitur, cluster tiap kategori, gabungkan hasil."""
    features = build_clustering_features(cleaned, anomaly_df)
    sufficient = features[features["n_observations"] >= config.CLUSTERING_MIN_OBSERVATIONS]
    skipped = features[features["n_observations"] < config.CLUSTERING_MIN_OBSERVATIONS]

    results = []
    for category in sufficient["equipment_category"].unique():
        results.append(cluster_category(sufficient, category))
    clustered = pd.concat(results, ignore_index=True) if results else pd.DataFrame()

    if not skipped.empty:
        skipped = skipped.copy()
        skipped["cluster_id"] = -1
        skipped["cluster_label"] = "INSUFFICIENT_DATA"
        skipped["cluster_characteristics"] = "Observasi terlalu sedikit untuk disegmentasi"
        skipped["silhouette_score"] = np.nan
        warnings.warn(
            f"{len(skipped)} equipment dilewati dari clustering karena observasi "
            f"< {config.CLUSTERING_MIN_OBSERVATIONS}.", stacklevel=2,
        )
        clustered = pd.concat([clustered, skipped], ignore_index=True)

    out_cols = ["equipment_id", "equipment_category", "cluster_id", "cluster_label",
                "cluster_characteristics", "silhouette_score"] + FEATURE_COLS
    return clustered[out_cols].sort_values(["equipment_category", "cluster_label"]).reset_index(drop=True)


def summarize_clusters(clustered: pd.DataFrame) -> pd.DataFrame:
    return (clustered.groupby(["equipment_category", "cluster_label"])
            .size().rename("n_equipment").reset_index()
            .sort_values(["equipment_category", "n_equipment"], ascending=[True, False]))


def save_outputs(clustered: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    clustered.to_csv(output_dir / "equipment_clusters.csv", index=False)
    summarize_clusters(clustered).to_csv(output_dir / "cluster_summary.csv", index=False)


if __name__ == "__main__":
    import warnings as _w
    _w.filterwarnings("ignore")

    from src.data_cleaning import run_cleaning_pipeline
    from src.anomaly_detection import detect_anomalies

    result = run_cleaning_pipeline()
    anomaly_df = detect_anomalies(result.cleaned_fuel_data)

    with warnings.catch_warnings():
        warnings.simplefilter("always")
        clustered = cluster_all_equipment(result.cleaned_fuel_data, anomaly_df)
    save_outputs(clustered)

    print("=== Ringkasan cluster per kategori & label ===")
    print(summarize_clusters(clustered).to_string(index=False))

    print("\n=== Contoh karakteristik cluster (RTGC) ===")
    rtgc = clustered[clustered["equipment_category"] == "RTGC"]
    print(rtgc[["cluster_label", "cluster_characteristics"]].drop_duplicates().to_string(index=False))
