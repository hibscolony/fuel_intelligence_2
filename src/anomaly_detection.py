"""
anomaly_detection.py
=====================
Deteksi anomali konsumsi solar per equipment DAN per kategori, memakai
kombinasi:

1. Rolling median/MAD (positional, per transaksi -- bukan kalender, karena
   data ini pengisian/refueling yang tidak terjadi tiap hari).
2. Robust z-score dari rolling median/MAD.
3. Isolation Forest per kategori (butuh cukup sampel, dipool lintas equipment
   sejenis).
4. Perbandingan terhadap median kategori (peer comparison).
5. (Opsional, terbatas) residual terhadap forecast level-kategori, jika hasil
   forecasting tersedia -- lihat forecast_integration.py. Karena model yang
   ada saat ini hanya forecasting di level TOTAL/kategori (bukan per unit),
   komponen ini hanya relevan untuk agregat kategori, bukan tiap equipment_id.

PENTING (lihat catatan proyek): output modul ini adalah "fuel consumption
anomaly" -- indikasi untuk DIPERIKSA, BUKAN bukti kebocoran, pemborosan,
atau kerusakan alat. Equipment dengan observasi terlalu sedikit diberi
status `INSUFFICIENT_DATA`, bukan dipaksakan dianalisis.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

MAD_SCALE = 0.6744897501960817  # konstanta supaya MAD comparable dgn std normal


def _rolling_positional(series: pd.Series, window: int, func,
                         min_periods: Optional[int] = None) -> pd.Series:
    """Rolling statistik berbasis URUTAN TRANSAKSI (bukan kalender), hanya
    memakai transaksi SEBELUM titik saat ini (shift(1)) supaya tidak bocor.
    """
    mp = min_periods if min_periods is not None else max(2, window // 2)
    return series.shift(1).rolling(window, min_periods=mp).apply(func, raw=True)


def _mad(x: np.ndarray) -> float:
    med = np.median(x)
    return np.median(np.abs(x - med))


def build_equipment_features(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Bangun fitur per transaksi (level equipment) dari baris VALID saja
    (fuel_liter numerik tercatat -- baris STATUS_ONLY/INVALID_DATE dikecualikan
    karena tidak ada nilai liter untuk dianalisis numerik).
    """
    df = cleaned[cleaned["data_status"].isin(["VALID", "UNUSUALLY_HIGH"])].copy()
    df = df.sort_values(["equipment_category", "equipment_id", "date"])

    df["day_of_week"] = df["date"].dt.dayofweek
    df["month_num"] = df["date"].dt.month

    grouped = df.groupby(["equipment_category", "equipment_id"])
    df["lag_1"] = grouped["fuel_liter"].shift(1)
    df["lag_7"] = grouped["fuel_liter"].shift(7)
    df["difference_1"] = df["fuel_liter"] - df["lag_1"]
    with np.errstate(divide="ignore", invalid="ignore"):
        df["percentage_change"] = np.where(
            (df["lag_1"].notna()) & (df["lag_1"] != 0),
            df["difference_1"] / df["lag_1"] * 100, np.nan)

    df["rolling_mean_7"] = grouped["fuel_liter"].transform(lambda s: _rolling_positional(s, 7, np.mean))
    df["rolling_median_7"] = grouped["fuel_liter"].transform(
        lambda s: _rolling_positional(s, 7, np.median, config.ANOMALY_ROLLING_MIN_PERIODS))
    df["rolling_std_7"] = grouped["fuel_liter"].transform(lambda s: _rolling_positional(s, 7, np.std))
    df["rolling_mad_7"] = grouped["fuel_liter"].transform(
        lambda s: _rolling_positional(s, 7, _mad, config.ANOMALY_ROLLING_MIN_PERIODS))
    df["rolling_mean_14"] = grouped["fuel_liter"].transform(lambda s: _rolling_positional(s, 14, np.mean))
    df["rolling_mean_30"] = grouped["fuel_liter"].transform(lambda s: _rolling_positional(s, 30, np.mean))

    # equipment_mean: expanding mean SEBELUM transaksi saat ini (deskriptif, bukan leak ke titik ybs)
    df["equipment_mean"] = grouped["fuel_liter"].transform(lambda s: s.shift(1).expanding(min_periods=2).mean())

    category_median = df.groupby("equipment_category")["fuel_liter"].transform("median")
    df["category_median"] = category_median

    df["deviation_from_equipment_mean"] = df["fuel_liter"] - df["equipment_mean"]
    df["deviation_from_category_median"] = df["fuel_liter"] - df["category_median"]

    return df


def compute_robust_z_score(df: pd.DataFrame) -> pd.Series:
    """Robust z-score = 0.6745 * (x - rolling_median) / rolling_MAD.

    rolling_MAD diberi LANTAI (floor) sebesar `config.ANOMALY_MAD_FLOOR_PCT`
    dari rolling_mean -- tanpa ini, histori yang kebetulan sangat seragam
    (MAD mendekati 0 murni karena kebetulan, bukan karena unit itu benar-benar
    stabil) akan membuat deviasi kecil sekalipun tampak sebagai z-score raksasa.
    Jika rolling_mean juga tidak tersedia/nol, fallback ke NaN.
    """
    floor = (config.ANOMALY_MAD_FLOOR_PCT * df["rolling_mean_7"]).abs()
    mad = df["rolling_mad_7"].where(df["rolling_mad_7"] > floor, floor)
    mad = mad.replace(0, np.nan)
    return (MAD_SCALE * (df["fuel_liter"] - df["rolling_median_7"]) / mad)


def run_isolation_forest_per_category(df: pd.DataFrame) -> pd.Series:
    """Isolation Forest dilatih TERPISAH per kategori (equipment sejenis
    dipool bersama supaya cukup sampel). Mengembalikan isolation_score:
    semakin NEGATIF -> semakin dianggap anomali (konvensi sklearn
    decision_function).
    """
    scores = pd.Series(np.nan, index=df.index)
    feature_cols = ["fuel_liter", "deviation_from_category_median", "day_of_week", "month_num"]

    for cat, sub in df.groupby("equipment_category"):
        sub_valid = sub.dropna(subset=feature_cols)
        if len(sub_valid) < 30:  # sampel terlalu sedikit utk melatih Isolation Forest yg stabil
            continue
        model = IsolationForest(
            contamination=config.ANOMALY_ISOLATION_FOREST_CONTAMINATION,
            random_state=config.RANDOM_STATE, n_estimators=200,
        )
        model.fit(sub_valid[feature_cols])
        scores.loc[sub_valid.index] = model.decision_function(sub_valid[feature_cols])
    return scores


def classify_severity(robust_z: float, isolation_score: float) -> str:
    """Tentukan severity dari robust z-score (utama) + koreksi dari Isolation
    Forest (jika keduanya sepakat anomali, severity dinaikkan 1 tingkat).
    """
    if pd.isna(robust_z):
        return "NORMAL"
    abs_z = abs(robust_z)
    thresholds = config.ANOMALY_ROBUST_Z_THRESHOLDS
    if abs_z >= thresholds["CRITICAL"]:
        base = "CRITICAL"
    elif abs_z >= thresholds["HIGH"]:
        base = "HIGH"
    elif abs_z >= thresholds["MEDIUM"]:
        base = "MEDIUM"
    elif abs_z >= thresholds["LOW"]:
        base = "LOW"
    else:
        base = "NORMAL"

    order = ["NORMAL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
    isolation_flags_anomaly = pd.notna(isolation_score) and isolation_score < 0
    if base != "NORMAL" and isolation_flags_anomaly:
        idx = min(order.index(base) + 1, len(order) - 1)
        return order[idx]
    return base


def classify_anomaly_type_and_reason(row: pd.Series) -> tuple[str, str]:
    """Tentukan jenis & alasan anomali dalam bahasa yang netral (indikasi
    pemeriksaan, bukan vonis kerusakan/pemborosan).
    """
    if row["severity"] == "NORMAL":
        return "NORMAL", "Konsumsi berada dalam pola normal unit."

    reasons = []
    types = []

    if row["fuel_liter"] == 0:
        types.append("UNUSUAL_ZERO_FILL")
        reasons.append("Pengisian tercatat nol liter -- tidak biasa dibanding transaksi lain unit ini.")

    if pd.notna(row["robust_z_score"]) and row["robust_z_score"] > 0:
        types.append("HIGH_VS_OWN_PATTERN")
        reasons.append("Konsumsi jauh di atas pola normal unit ini (dibanding riwayat transaksinya sendiri).")
    elif pd.notna(row["robust_z_score"]) and row["robust_z_score"] < 0:
        types.append("LOW_VS_OWN_PATTERN")
        reasons.append("Konsumsi jauh di bawah pola normal unit ini.")

    if pd.notna(row["percentage_change"]) and abs(row["percentage_change"]) >= 100:
        types.append("SPIKE_VS_PREVIOUS")
        reasons.append("Lonjakan/penurunan besar dibanding transaksi sebelumnya.")

    if pd.notna(row["deviation_from_category_median"]) and row["category_median"] > 0 and \
            abs(row["deviation_from_category_median"]) / row["category_median"] >= 1.0:
        types.append("EXTREME_VS_CATEGORY_PEERS")
        reasons.append("Berbeda ekstrem dibanding median alat sejenis pada kategori yang sama.")

    if pd.notna(row["isolation_score"]) and row["isolation_score"] < -0.05:
        types.append("MULTIVARIATE_OUTLIER")
        reasons.append("Kombinasi pola (nilai, hari, bulan) terdeteksi sebagai outlier oleh Isolation Forest.")

    if not types:
        types.append("STATISTICAL_DEVIATION")
        reasons.append("Terdeteksi menyimpang secara statistik dari pola historis unit.")

    return "|".join(types), " ".join(reasons)


def detect_anomalies(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Entry point utama: hasilkan tabel anomaly_report sesuai skema di
    spesifikasi proyek. Equipment dengan observasi < config.ANOMALY_MIN_OBSERVATIONS
    diberi status INSUFFICIENT_DATA, bukan dipaksakan dianalisis.
    """
    df = build_equipment_features(cleaned)

    obs_count = df.groupby(["equipment_category", "equipment_id"])["fuel_liter"].transform("count")
    sufficient = obs_count >= config.ANOMALY_MIN_OBSERVATIONS

    df["robust_z_score"] = np.nan
    df["isolation_score"] = np.nan
    df.loc[sufficient, "robust_z_score"] = compute_robust_z_score(df.loc[sufficient])
    df.loc[sufficient, "isolation_score"] = run_isolation_forest_per_category(df.loc[sufficient])

    df["expected_fuel"] = df["rolling_median_7"].fillna(df["equipment_mean"])
    df["deviation_liter"] = df["fuel_liter"] - df["expected_fuel"]
    with np.errstate(divide="ignore", invalid="ignore"):
        df["deviation_percentage"] = np.where(
            (df["expected_fuel"].notna()) & (df["expected_fuel"] != 0),
            df["deviation_liter"] / df["expected_fuel"] * 100, np.nan)

    df["severity"] = "INSUFFICIENT_DATA"
    df.loc[sufficient, "severity"] = df.loc[sufficient].apply(
        lambda r: classify_severity(r["robust_z_score"], r["isolation_score"]), axis=1)

    anomaly_type_reason = df.apply(
        lambda r: classify_anomaly_type_and_reason(r) if r["severity"] != "INSUFFICIENT_DATA"
        else ("INSUFFICIENT_DATA", f"Observasi ({obs_count[r.name]}) di bawah ambang minimum "
                                    f"({config.ANOMALY_MIN_OBSERVATIONS}) -- belum layak dianalisis."),
        axis=1, result_type="expand")
    df["anomaly_type"], df["anomaly_reason"] = anomaly_type_reason[0], anomaly_type_reason[1]

    out_cols = ["date", "equipment_id", "equipment_category", "fuel_liter", "expected_fuel",
                "deviation_liter", "deviation_percentage", "robust_z_score", "isolation_score",
                "anomaly_type", "severity", "anomaly_reason"]
    return df[out_cols].sort_values(["equipment_category", "equipment_id", "date"]).reset_index(drop=True)


def summarize_anomalies(anomaly_df: pd.DataFrame) -> pd.DataFrame:
    """Ringkasan jumlah anomali per kategori & severity -- dipakai dashboard
    Executive Overview / Fuel Anomaly.
    """
    return (anomaly_df.groupby(["equipment_category", "severity"])
            .size().rename("n_records").reset_index()
            .sort_values(["equipment_category", "severity"]))


def save_outputs(anomaly_df: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    anomaly_df.to_csv(output_dir / "anomaly_report.csv", index=False)
    summarize_anomalies(anomaly_df).to_csv(output_dir / "anomaly_summary_by_category.csv", index=False)


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()
    anomaly_df = detect_anomalies(result.cleaned_fuel_data)
    save_outputs(anomaly_df)

    print("=== Distribusi severity ===")
    print(anomaly_df["severity"].value_counts())

    print("\n=== Ringkasan per kategori & severity ===")
    print(summarize_anomalies(anomaly_df))

    print("\n=== Contoh anomali CRITICAL/HIGH ===")
    print(anomaly_df[anomaly_df["severity"].isin(["CRITICAL", "HIGH"])]
          [["date", "equipment_category", "equipment_id", "fuel_liter", "expected_fuel",
            "deviation_percentage", "anomaly_type", "anomaly_reason"]].head(15))
