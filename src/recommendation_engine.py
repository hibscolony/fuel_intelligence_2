"""
recommendation_engine.py
=========================
Rule-based recommendation engine. Menggabungkan sinyal dari SEMUA modul
sebelumnya (health_score, anomaly, change_point, reconciliation, saving
simulator) menjadi rekomendasi terstruktur yang bisa langsung dipakai tim
operasional. Setiap rekomendasi WAJIB menyertakan evidence -- tidak ada
klaim tanpa dasar angka.

Rekomendasi ini adalah SARAN tindak lanjut berbasis pola data, BUKAN
kesimpulan teknis (lihat catatan proyek: bukan diagnosis kerusakan/vonis
pemborosan).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

RESPONSIBLE_ROLES = {"OPERATIONS_ADMIN": "Operations Admin", "EQUIPMENT": "Equipment",
                     "MAINTENANCE": "Maintenance", "FUEL_ADMIN": "Fuel Administration",
                     "SUPERVISOR": "Supervisor", "ICT_DATA": "ICT/Data Team"}


def _target_date(priority: str, today: Optional[datetime] = None) -> str:
    today = today or datetime.now()
    offset = config.REC_TARGET_DATE_OFFSET_DAYS.get(priority, 14)
    return (today + timedelta(days=offset)).strftime("%Y-%m-%d")


def _make_row(priority: str, equipment_id: str, equipment_category: str, finding: str,
              evidence: str, action: str, role: str) -> dict:
    return {
        "priority": priority, "equipment_id": equipment_id, "equipment_category": equipment_category,
        "finding": finding, "evidence": evidence, "recommended_action": action,
        "responsible_role": role, "target_date": _target_date(priority), "status": "OPEN",
    }


def rule_critical_health_with_trend(health_scores: pd.DataFrame) -> list[dict]:
    """R1: health_score rendah + anomali kritis berulang + tren naik ->
    prioritas tinggi validasi & inspeksi (contoh persis dari spesifikasi).
    """
    rows = []
    cond = (
        (health_scores["health_score"] < 50)
        & (health_scores["critical_anomaly_count"] >= config.HEALTH_REPEATED_CRITICAL_THRESHOLD)
        & (health_scores["trend_percentage"] > 0)
    )
    for _, r in health_scores[cond].iterrows():
        rows.append(_make_row(
            "HIGH", r["equipment_id"], r["equipment_category"],
            "Health score rendah, disertai anomali kritis berulang dan tren konsumsi naik.",
            f"health_score={r['health_score']:.1f}, critical_anomaly_count={r['critical_anomaly_count']}, "
            f"trend_percentage={r['trend_percentage']:.1f}%",
            "Prioritas tinggi untuk validasi data dan inspeksi operasional.",
            RESPONSIBLE_ROLES["MAINTENANCE"],
        ))
    return rows


def rule_high_anomaly_low_completeness(health_scores: pd.DataFrame) -> list[dict]:
    """R2: anomali tinggi TAPI data completeness rendah -> validasi data dulu,
    jangan langsung menyimpulkan kondisi unit (contoh persis dari spesifikasi).
    """
    rows = []
    cond = (
        (health_scores["anomaly_count"] >= config.REC_HIGH_ANOMALY_COUNT_THRESHOLD)
        & (health_scores["data_completeness"] < config.REC_LOW_COMPLETENESS_THRESHOLD_PCT)
    )
    for _, r in health_scores[cond].iterrows():
        rows.append(_make_row(
            "MEDIUM", r["equipment_id"], r["equipment_category"],
            "Jumlah anomali cukup tinggi, namun kelengkapan data unit ini rendah.",
            f"anomaly_count={r['anomaly_count']}, data_completeness={r['data_completeness']:.1f}%",
            "Validasi kualitas data sebelum menyimpulkan kondisi unit.",
            RESPONSIBLE_ROLES["ICT_DATA"],
        ))
    return rows


def rule_budget_exceeded(scenarios_df: pd.DataFrame) -> list[dict]:
    """R3: proyeksi BAU melebihi target anggaran -> perlu pengurangan
    rata-rata X liter/hari (contoh persis dari spesifikasi). Rekomendasi
    di level ARMADA, bukan per unit -- saving simulator bekerja di level total.
    """
    bau = scenarios_df[scenarios_df["scenario"] == "Business as Usual"]
    if bau.empty:
        return []
    row = bau.iloc[0]
    if row["gap_to_target_liter"] <= 0:
        return []
    required_daily = row["required_daily_reduction"]
    return [_make_row(
        "HIGH", "ARMADA (seluruh alat)", "ALL",
        "Proyeksi konsumsi Business-as-Usual melebihi target penghematan tahunan.",
        f"gap_to_target={row['gap_to_target_liter']:,.0f} L, "
        f"required_daily_reduction={required_daily:,.1f} L/hari",
        f"Diperlukan pengurangan rata-rata {required_daily:,.1f} liter per hari "
        f"untuk mencapai target penghematan tahunan.",
        RESPONSIBLE_ROLES["SUPERVISOR"],
    )]


def rule_above_category_median(health_scores: pd.DataFrame) -> list[dict]:
    """R4: unit dengan rata-rata konsumsi jauh di atas median kategorinya ->
    bandingkan jam operasi/trip/idle time/hour meter dengan unit sejenis
    (contoh persis dari spesifikasi).
    """
    rows = []
    medians = health_scores.groupby("equipment_category")["average_daily_fuel"].median()
    for _, r in health_scores.iterrows():
        cat_median = medians.get(r["equipment_category"], np.nan)
        if pd.isna(cat_median) or cat_median <= 0:
            continue
        ratio = r["average_daily_fuel"] / cat_median
        if ratio >= config.REC_CATEGORY_MEDIAN_EXCESS_RATIO:
            rows.append(_make_row(
                "LOW", r["equipment_id"], r["equipment_category"],
                "Rata-rata konsumsi per pengisian jauh di atas median unit sejenis.",
                f"average_daily_fuel={r['average_daily_fuel']:.1f} L vs median kategori "
                f"{cat_median:.1f} L (rasio {ratio:.2f}x)",
                "Bandingkan jam operasi, trip, idle time, dan hour meter dengan unit sejenis.",
                RESPONSIBLE_ROLES["OPERATIONS_ADMIN"],
            ))
    return rows


def rule_reconciliation_issue(category_reconciliation: pd.DataFrame) -> list[dict]:
    """R5: kategori dengan selisih rekonsiliasi signifikan -> verifikasi
    pencatatan sebelum data dipakai untuk kesimpulan lebih lanjut."""
    rows = []
    problem = category_reconciliation[
        category_reconciliation["validation_status"].isin(["MAJOR DIFFERENCE", "REQUIRES REVIEW"])]
    for (cat,), sub in problem.groupby(["equipment_category"]):
        months = ", ".join(sub["month_name"].tolist())
        rows.append(_make_row(
            "MEDIUM", f"SEMUA UNIT ({cat})", cat,
            f"Selisih rekonsiliasi signifikan pada kategori {cat} untuk bulan: {months}.",
            f"n_bulan_bermasalah={len(sub)}, "
            f"max_diff_pct={sub['difference_percentage'].abs().max():.1f}%",
            "Verifikasi pencatatan pengisian solar kategori ini pada bulan terkait "
            "sebelum dipakai untuk kesimpulan operasional.",
            RESPONSIBLE_ROLES["FUEL_ADMIN"],
        ))
    return rows


def rule_persistent_change_point(health_scores: pd.DataFrame) -> list[dict]:
    """R6: change point dgn penalty tinggi (kenaikan level/volatilitas
    signifikan & confidence tinggi) -> bandingkan dgn hour meter/log operasi.
    """
    rows = []
    cond = health_scores["change_point_penalty"] >= 50
    for _, r in health_scores[cond].iterrows():
        rows.append(_make_row(
            "MEDIUM", r["equipment_id"], r["equipment_category"],
            "Perubahan pola konsumsi yang menetap terdeteksi (bukan sekadar lonjakan sesaat).",
            f"change_point_penalty={r['change_point_penalty']:.1f}, "
            f"last_change_point={r['last_change_point']}",
            "Bandingkan dengan hour meter dan log operasi unit ini di sekitar tanggal perubahan.",
            RESPONSIBLE_ROLES["EQUIPMENT"],
        ))
    return rows


def rule_long_zero_consumption_streak(health_scores: pd.DataFrame) -> list[dict]:
    """R7: missing_data_penalty tinggi (zero-consumption streak panjang) ->
    konfirmasi status alat, cek kemungkinan duplikasi/kesalahan pencatatan.
    """
    rows = []
    cond = health_scores["missing_data_penalty"] >= 50
    for _, r in health_scores[cond].iterrows():
        rows.append(_make_row(
            "LOW", r["equipment_id"], r["equipment_category"],
            "Terdapat jeda panjang tanpa transaksi solar di tengah masa aktif unit.",
            f"missing_data_penalty={r['missing_data_penalty']:.1f}, "
            f"data_completeness={r['data_completeness']:.1f}%",
            "Cek kemungkinan duplikasi transaksi / konfirmasi status alat (aktif, dipindah, atau nonaktif).",
            RESPONSIBLE_ROLES["OPERATIONS_ADMIN"],
        ))
    return rows


PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def build_recommendations(health_scores: pd.DataFrame,
                           category_reconciliation: pd.DataFrame,
                           scenarios_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Entry point utama: jalankan semua rule, gabungkan, urutkan berdasarkan
    prioritas. Satu equipment BISA memiliki lebih dari satu rekomendasi jika
    beberapa kondisi terpenuhi sekaligus (rule bersifat independen).
    """
    scored = health_scores[health_scores["health_status"] != "INSUFFICIENT_DATA"]

    rows: list[dict] = []
    rows += rule_critical_health_with_trend(scored)
    rows += rule_high_anomaly_low_completeness(scored)
    rows += rule_above_category_median(scored)
    rows += rule_reconciliation_issue(category_reconciliation)
    rows += rule_persistent_change_point(scored)
    rows += rule_long_zero_consumption_streak(scored)
    if scenarios_df is not None:
        rows += rule_budget_exceeded(scenarios_df)

    if not rows:
        return pd.DataFrame(columns=["priority", "equipment_id", "equipment_category", "finding",
                                      "evidence", "recommended_action", "responsible_role",
                                      "target_date", "status"])

    df = pd.DataFrame(rows)
    df["_priority_rank"] = df["priority"].map(PRIORITY_ORDER)
    df = df.sort_values(["_priority_rank", "equipment_id"]).drop(columns=["_priority_rank"])
    return df.reset_index(drop=True)


def summarize_recommendations(recommendations: pd.DataFrame) -> pd.DataFrame:
    if recommendations.empty:
        return pd.DataFrame(columns=["priority", "responsible_role", "n_recommendations"])
    return (recommendations.groupby(["priority", "responsible_role"])
            .size().rename("n_recommendations").reset_index()
            .sort_values(["priority", "n_recommendations"], ascending=[True, False]))


def save_outputs(recommendations: pd.DataFrame, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    recommendations.to_csv(output_dir / "recommendation_report.csv", index=False)
    summarize_recommendations(recommendations).to_csv(
        output_dir / "recommendation_summary.csv", index=False)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")

    from src.data_cleaning import run_cleaning_pipeline
    from src.anomaly_detection import detect_anomalies
    from src.change_point import detect_all_change_points
    from src.data_quality import detect_zero_consumption_streaks
    from src.health_score import build_health_score_table
    from src.saving_simulator import SavingSimulatorInputs, run_saving_scenarios

    result = run_cleaning_pipeline()
    anomaly_df = detect_anomalies(result.cleaned_fuel_data)
    change_points = detect_all_change_points(result.cleaned_fuel_data)
    zero_streaks = detect_zero_consumption_streaks(result.cleaned_fuel_data)
    health_scores = build_health_score_table(
        result.cleaned_fuel_data, anomaly_df, change_points, zero_streaks,
        result.category_monthly_reconciliation, forecast_summary=None)

    valid = result.cleaned_fuel_data[result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    baseline_total = float(valid["fuel_liter"].sum())
    scenarios_df = run_saving_scenarios(SavingSimulatorInputs(baseline_total_liter=baseline_total))

    recommendations = build_recommendations(health_scores, result.category_monthly_reconciliation,
                                             scenarios_df)
    save_outputs(recommendations)

    print(f"Total rekomendasi: {len(recommendations)}")
    print("\n=== Ringkasan per prioritas & peran ===")
    print(summarize_recommendations(recommendations).to_string(index=False))

    print("\n=== Rekomendasi prioritas HIGH ===")
    print(recommendations[recommendations["priority"] == "HIGH"].to_string(index=False))
