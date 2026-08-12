"""
health_score.py
================
Fuel Consumption Health Score (0-100) per equipment -- BUKAN skor efisiensi
mesin atau diagnosis kerusakan (lihat catatan proyek). Skor ini murni
mengukur seberapa "mencurigakan"/tidak biasa pola pengisian solar sebuah
unit dibanding riwayatnya sendiri dan kategori sejenisnya, sebagai alat
BANTU MEMPRIORITASKAN pemeriksaan -- bukan vonis.

Delapan komponen penalty (bobot di config.HEALTH_SCORE_WEIGHTS, total = 1.0):
1. anomaly_penalty            -- proporsi transaksi anomali (severity-weighted)
2. volatility_penalty         -- coefficient of variation, relatif thd peer kategori
3. trend_penalty              -- tren KENAIKAN konsumsi (paruh 1 vs paruh 2 masa aktif)
4. change_point_penalty       -- change point yang mengarah ke kenaikan level/volatilitas
5. missing_data_penalty       -- zero-consumption streak (Tahap 3)
6. reconciliation_penalty     -- proksi dari status rekonsiliasi KATEGORI (Tahap 3),
                                  karena workbook tidak punya subtotal per unit individual
7. forecast_error_penalty     -- proksi dari WAPE forecast level ARMADA (Tahap 4);
                                  lemah sebagai sinyal per-unit, diberi bobot kecil
8. repeated_critical_penalty  -- anomaly CRITICAL yang BERULANG (bukan insiden tunggal)

health_score = 100 - sum(weight_i * component_i),  masing2 component_i sudah
dinormalisasi ke rentang [0, 100] SEBELUM dikalikan bobot -- sehingga skor
akhir dijamin berada di [0, 100] oleh konstruksi (bobot berjumlah 1.0).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

SEVERITY_WEIGHT = {"NORMAL": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _clip_0_100(x: pd.Series) -> pd.Series:
    return x.clip(lower=0, upper=100)


def compute_base_stats(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Statistik dasar per equipment: rata-rata/median konsumsi per transaksi,
    volatilitas (CV), dan tren (%perubahan paruh-1 vs paruh-2 masa aktif).
    """
    valid = cleaned[cleaned["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])].sort_values(
        ["equipment_category", "equipment_id", "date"])

    def _trend_pct(s: pd.Series) -> float:
        if len(s) < 6:
            return np.nan
        mid = len(s) // 2
        first_half_mean, second_half_mean = s.iloc[:mid].mean(), s.iloc[mid:].mean()
        if first_half_mean == 0:
            return np.nan
        return (second_half_mean - first_half_mean) / first_half_mean * 100

    stats = valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].agg(
        average_daily_fuel="mean", median_daily_fuel="median", fuel_std="std",
        n_observations="count",
    ).reset_index()
    stats["fuel_volatility"] = (stats["fuel_std"] / stats["average_daily_fuel"]).replace(
        [np.inf, -np.inf], np.nan)

    trend = valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].apply(_trend_pct)
    stats = stats.merge(trend.rename("trend_percentage"), on=["equipment_category", "equipment_id"])

    return stats.drop(columns=["fuel_std"])


def compute_anomaly_component(anomaly_df: pd.DataFrame) -> pd.DataFrame:
    """anomaly_penalty (severity-weighted proporsi) + anomaly_count + critical_anomaly_count
    + repeated_critical_penalty.
    """
    scored = anomaly_df[anomaly_df["severity"] != "INSUFFICIENT_DATA"].copy()
    scored["severity_weight"] = scored["severity"].map(SEVERITY_WEIGHT)

    agg = scored.groupby(["equipment_category", "equipment_id"]).agg(
        anomaly_count=("severity", lambda s: (s != "NORMAL").sum()),
        critical_anomaly_count=("severity", lambda s: (s == "CRITICAL").sum()),
        n_scored=("severity", "count"),
        severity_weight_sum=("severity_weight", "sum"),
    ).reset_index()

    max_weight = max(SEVERITY_WEIGHT.values())
    agg["anomaly_penalty"] = _clip_0_100(
        agg["severity_weight_sum"] / (agg["n_scored"] * max_weight) * 100)
    agg["repeated_critical_penalty"] = _clip_0_100(
        (agg["critical_anomaly_count"] / config.HEALTH_REPEATED_CRITICAL_THRESHOLD).clip(upper=1) * 100)

    return agg[["equipment_category", "equipment_id", "anomaly_count", "critical_anomaly_count",
                "anomaly_penalty", "repeated_critical_penalty"]]


def compute_volatility_component(base_stats: pd.DataFrame) -> pd.DataFrame:
    """volatility_penalty = percentile rank CV relatif terhadap PEER dalam kategori
    yang sama (supaya adil lintas kategori dgn karakteristik alat berbeda)."""
    df = base_stats.copy()
    df["volatility_penalty"] = df.groupby("equipment_category")["fuel_volatility"].transform(
        lambda s: s.rank(pct=True) * 100)
    df["volatility_penalty"] = _clip_0_100(df["volatility_penalty"].fillna(0))
    return df[["equipment_category", "equipment_id", "volatility_penalty"]]


def compute_trend_component(base_stats: pd.DataFrame) -> pd.DataFrame:
    """trend_penalty: HANYA tren NAIK yang dipenalti (sesuai spesifikasi
    'upward trend penalty') -- tren turun tidak menambah penalty di komponen ini.
    """
    df = base_stats.copy()
    upward = df["trend_percentage"].clip(lower=0)
    df["trend_penalty"] = _clip_0_100(
        (upward / config.HEALTH_TREND_PENALTY_CAP_PCT * 100).fillna(0))
    return df[["equipment_category", "equipment_id", "trend_penalty"]]


def compute_change_point_component(change_points: pd.DataFrame,
                                    equipment_index: pd.DataFrame) -> pd.DataFrame:
    """change_point_penalty: lebih berat utk arah INCREASE/VOLATILITY_INCREASE
    dan confidence lebih tinggi; juga catat last_change_point per equipment.
    """
    direction_weight = {"INCREASE": 1.0, "VOLATILITY_INCREASE": 0.8, "DECREASE": 0.4,
                        "VOLATILITY_DECREASE": 0.3, "MINOR_SHIFT": 0.2}
    confidence_weight = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}

    base = equipment_index[["equipment_category", "equipment_id"]].drop_duplicates()
    if change_points.empty:
        base["change_point_penalty"] = 0.0
        base["last_change_point"] = pd.NaT
        return base

    cp = change_points.copy()
    cp["score"] = (cp["change_direction"].map(direction_weight).fillna(0.2)
                   * cp["confidence"].map(confidence_weight).fillna(0.3)) * 100

    agg = cp.groupby(["equipment_category", "equipment_id"]).agg(
        change_point_penalty=("score", lambda s: min(100, s.sum())),
        last_change_point=("change_date", "max"),
    ).reset_index()

    result = base.merge(agg, on=["equipment_category", "equipment_id"], how="left")
    result["change_point_penalty"] = result["change_point_penalty"].fillna(0.0)
    return result


def compute_missing_data_component(zero_streaks: pd.DataFrame,
                                    equipment_index: pd.DataFrame) -> pd.DataFrame:
    """missing_data_penalty dari panjang zero-consumption streak (Tahap 3),
    dinormalisasi relatif terhadap ambang konfigurasi."""
    base = equipment_index[["equipment_category", "equipment_id"]].drop_duplicates()
    if zero_streaks.empty:
        base["missing_data_penalty"] = 0.0
        base["data_completeness"] = 100.0
        return base

    merged = base.merge(
        zero_streaks[["equipment_category", "equipment_id", "longest_gap_days"]],
        on=["equipment_category", "equipment_id"], how="left")
    merged["missing_data_penalty"] = _clip_0_100(
        (merged["longest_gap_days"].fillna(0) / (config.ZERO_CONSUMPTION_STREAK_DAYS * 3) * 100))
    merged["data_completeness"] = (100 - merged["missing_data_penalty"]).round(1)
    return merged.drop(columns=["longest_gap_days"])


def compute_reconciliation_component(category_reconciliation: pd.DataFrame,
                                      equipment_index: pd.DataFrame) -> pd.DataFrame:
    """reconciliation_penalty: PROKSI dari status rekonsiliasi KATEGORI (bukan
    per unit -- workbook tidak menyediakan subtotal per ID individual). Semua
    equipment dalam kategori & rentang waktu yang sama mendapat proksi yang sama.
    """
    status_penalty = {"MATCH": 0, "MINOR DIFFERENCE": 20, "MAJOR DIFFERENCE": 60,
                       "REQUIRES REVIEW": 100, "NO_WORKBOOK_VALUE": 0}
    cat_penalty = (category_reconciliation.assign(
        penalty=category_reconciliation["validation_status"].map(status_penalty).fillna(0))
        .groupby("equipment_category")["penalty"].mean().rename("reconciliation_penalty"))

    base = equipment_index[["equipment_category", "equipment_id"]].drop_duplicates()
    result = base.merge(cat_penalty, on="equipment_category", how="left")
    result["reconciliation_penalty"] = result["reconciliation_penalty"].fillna(0.0)
    return result


def compute_forecast_error_component(forecast_summary: Optional[dict],
                                      equipment_index: pd.DataFrame) -> pd.DataFrame:
    """forecast_error_penalty: PROKSI LEMAH dari WAPE forecast level ARMADA
    (model saat ini forecasting di level total/kategori, bukan per unit).
    Diberi bobot kecil (lihat config.HEALTH_SCORE_WEIGHTS) justru karena
    keterbatasan ini.
    """
    base = equipment_index[["equipment_category", "equipment_id"]].drop_duplicates()
    if not forecast_summary:
        base["forecast_error_penalty"] = 0.0
        return base
    wape = forecast_summary.get("wape", 0.0)
    penalty = min(100.0, (wape / config.FORECAST_WAPE_MONITOR_MAX) * 100)
    base["forecast_error_penalty"] = penalty
    return base


def classify_health_status(score: float, n_observations: int) -> str:
    if n_observations < config.HEALTH_MIN_OBSERVATIONS:
        return "INSUFFICIENT_DATA"
    for status, (low, high) in config.HEALTH_SCORE_BANDS.items():
        if low <= score <= high:
            return status
    return "REVIEW"


def recommend_action(row: pd.Series) -> str:
    """Rule-based recommended_action -- urutan prioritas dari yang paling
    mendesak. Hanya SARAN tindak lanjut, bukan kesimpulan.
    """
    if row["health_status"] == "INSUFFICIENT_DATA":
        return "Kumpulkan lebih banyak data sebelum dievaluasi"
    if row["reconciliation_penalty"] >= 60:
        return "Validasi data pengisian (selisih rekonsiliasi kategori signifikan)"
    if row["critical_anomaly_count"] >= config.HEALTH_REPEATED_CRITICAL_THRESHOLD:
        return "Koordinasikan inspeksi alat (anomali kritis berulang)"
    if row["change_point_penalty"] >= 50:
        return "Bandingkan dengan hour meter dan log operasi (perubahan pola menetap terdeteksi)"
    if row["anomaly_count"] >= 5:
        return "Periksa log operasi"
    if row["missing_data_penalty"] >= 50:
        return "Cek kemungkinan duplikasi transaksi / konfirmasi status alat"
    if row["health_status"] == "MONITOR":
        return "Monitor tujuh hari berikutnya"
    return "Tidak ada tindakan"


def build_health_score_table(cleaned: pd.DataFrame, anomaly_df: pd.DataFrame,
                              change_points: pd.DataFrame, zero_streaks: pd.DataFrame,
                              category_reconciliation: pd.DataFrame,
                              forecast_summary: Optional[dict] = None) -> pd.DataFrame:
    """Entry point utama: gabungkan semua komponen -> tabel health score final."""
    base_stats = compute_base_stats(cleaned)
    equipment_index = base_stats[["equipment_category", "equipment_id"]]

    anomaly_comp = compute_anomaly_component(anomaly_df)
    volatility_comp = compute_volatility_component(base_stats)
    trend_comp = compute_trend_component(base_stats)
    cp_comp = compute_change_point_component(change_points, equipment_index)
    missing_comp = compute_missing_data_component(zero_streaks, equipment_index)
    recon_comp = compute_reconciliation_component(category_reconciliation, equipment_index)
    forecast_comp = compute_forecast_error_component(forecast_summary, equipment_index)

    keys = ["equipment_category", "equipment_id"]
    result = base_stats
    for comp in [anomaly_comp, volatility_comp, trend_comp, cp_comp, missing_comp,
                 recon_comp, forecast_comp]:
        result = result.merge(comp, on=keys, how="left")

    penalty_cols = list(config.HEALTH_SCORE_WEIGHTS.keys())
    for col in penalty_cols:
        result[col] = result[col].fillna(0.0)

    weighted_sum = sum(result[col] * w for col, w in config.HEALTH_SCORE_WEIGHTS.items())
    result["health_score"] = (100 - weighted_sum).clip(lower=0, upper=100).round(1)
    result["health_status"] = result.apply(
        lambda r: classify_health_status(r["health_score"], r["n_observations"]), axis=1)
    result["anomaly_count"] = result["anomaly_count"].fillna(0).astype(int)
    result["critical_anomaly_count"] = result["critical_anomaly_count"].fillna(0).astype(int)
    result["recommended_action"] = result.apply(recommend_action, axis=1)

    out_cols = ["equipment_id", "equipment_category", "average_daily_fuel", "median_daily_fuel",
                "fuel_volatility", "trend_percentage", "anomaly_count", "critical_anomaly_count",
                "last_change_point", "data_completeness", "n_observations"] + penalty_cols + \
               ["health_score", "health_status", "recommended_action"]
    for c in out_cols:
        if c not in result.columns:
            result[c] = np.nan
    return result[out_cols].sort_values("health_score").reset_index(drop=True)


def save_outputs(health_scores: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    health_scores.to_csv(output_dir / "equipment_health_score.csv", index=False)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")

    from src.data_cleaning import run_cleaning_pipeline
    from src.anomaly_detection import detect_anomalies
    from src.change_point import detect_all_change_points
    from src.data_quality import detect_zero_consumption_streaks
    from src.forecast_integration import (load_forecast_results, compute_forecast_errors,
                                           compute_overall_metrics)

    result = run_cleaning_pipeline()
    anomaly_df = detect_anomalies(result.cleaned_fuel_data)
    change_points = detect_all_change_points(result.cleaned_fuel_data)
    zero_streaks = detect_zero_consumption_streaks(result.cleaned_fuel_data)

    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    daily_actual = valid.groupby("date")["fuel_liter"].sum(min_count=1).asfreq("D")
    forecast_df = load_forecast_results(daily_actual_fallback=daily_actual)
    forecast_summary = compute_overall_metrics(compute_forecast_errors(forecast_df)).__dict__

    health_scores = build_health_score_table(
        result.cleaned_fuel_data, anomaly_df, change_points, zero_streaks,
        result.category_monthly_reconciliation, forecast_summary)
    save_outputs(health_scores)

    print("=== Distribusi health_status ===")
    print(health_scores["health_status"].value_counts())

    print("\n=== 10 equipment dengan skor terendah ===")
    print(health_scores.head(10)[["equipment_category", "equipment_id", "health_score",
                                   "health_status", "recommended_action"]])

    print("\n=== Statistik health_score ===")
    print(health_scores["health_score"].describe())
