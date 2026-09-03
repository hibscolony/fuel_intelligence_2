"""
data_quality.py
===============
Modul audit kualitas data (bagian G spesifikasi). Menghasilkan KPI ringkas
+ status keseluruhan (PASS/REVIEW/FAILED), serta dua pemeriksaan tambahan
yang belum ditangani modul lain:

- zero-consumption streak per equipment (equipment tanpa transaksi solar
  dalam waktu lama -- indikasi pemeriksaan, BUKAN otomatis dianggap idle
  atau rusak)
- missing-date check pada level armada (hari tanpa transaksi sama sekali)

Semua ambang batas diambil dari config.py (bisa diubah tanpa mengubah kode).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config
from src.reconciliation import summarize_reconciliation_issues


@dataclass
class DataQualityKPIs:
    data_completeness_percentage: float
    valid_transaction_percentage: float
    duplicate_count: int
    missing_equipment_count: int
    invalid_value_count: int
    unusually_high_count: int
    invalid_date_count: int
    monthly_difference_liter_max: float
    n_months_major_reconciliation_issue: int
    reconciliation_status: str
    overall_status: str

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([self.__dict__])


def detect_missing_dates(cleaned: pd.DataFrame,
                         source_coverage_calendar: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Hari kalender (dalam rentang tanggal data yang tersedia) tanpa
    transaksi solar SAMA SEKALI (seluruh armada). Bukan berarti data hilang
    -- bisa juga hari libur operasional/administratif; tetap dilaporkan
    sebagai temuan untuk ditinjau.
    """
    if source_coverage_calendar is not None and not source_coverage_calendar.empty:
        calendar = source_coverage_calendar.copy()
        calendar["date"] = pd.to_datetime(calendar["date"], errors="coerce")
        missing = calendar[
            calendar["date"].notna()
            & ~calendar["known_source_coverage"].fillna(False).astype(bool)
        ]["date"].drop_duplicates().sort_values()
        return pd.DataFrame({"date": missing, "issue": "SOURCE_COVERAGE_GAP"}).reset_index(drop=True)

    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"]
    daily_counts = valid.groupby("date").size()
    if daily_counts.empty:
        return pd.DataFrame(columns=["date", "issue"])
    full_range = pd.date_range(daily_counts.index.min(), daily_counts.index.max(), freq="D")
    missing = full_range.difference(daily_counts.index)
    return pd.DataFrame({"date": missing, "issue": "NO_TRANSACTION_RECORDED_THIS_DAY"})


def detect_zero_consumption_streaks(cleaned: pd.DataFrame,
                                     min_streak_days: Optional[int] = None,
                                     source_coverage_calendar: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Untuk tiap equipment, cari rentang hari kalender terpanjang TANPA
    transaksi solar (baik numerik maupun status), dihitung dari rentang
    first_seen-last_seen equipment tsb (bukan dari 1 Jan, supaya equipment
    yang memang baru aktif pertengahan tahun tidak otomatis "dihukum").

    Equipment dengan streak >= min_streak_days ditandai utk ditinjau --
    INI BUKAN kesimpulan bahwa alat idle/rusak, hanya indikasi pemeriksaan
    (lihat catatan penting proyek).
    """
    min_streak_days = min_streak_days or config.ZERO_CONSUMPTION_STREAK_DAYS
    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"]
    known_source_dates = None
    if source_coverage_calendar is not None and not source_coverage_calendar.empty:
        calendar = source_coverage_calendar.copy()
        calendar["date"] = pd.to_datetime(calendar["date"], errors="coerce").dt.normalize()
        known_source_dates = pd.DatetimeIndex(
            calendar.loc[
                calendar["known_source_coverage"].fillna(False).astype(bool), "date"
            ].dropna().unique()
        )

    records = []
    for (cat, eq_id), sub in valid.groupby(["equipment_category", "equipment_id"]):
        dates_with_activity = pd.DatetimeIndex(sorted(sub["date"].dropna().unique()))
        if len(dates_with_activity) < 2:
            continue
        full_range = pd.date_range(dates_with_activity.min(), dates_with_activity.max(), freq="D")
        gaps = full_range.difference(dates_with_activity)
        if known_source_dates is not None:
            gaps = gaps.intersection(known_source_dates)
        if len(gaps) == 0:
            continue
        # cari rentang gap konsekutif terpanjang
        gap_groups = (pd.Series(gaps).diff().dt.days != 1).cumsum()
        longest = pd.Series(gaps).groupby(gap_groups).agg(["first", "last", "count"])
        longest = longest.sort_values("count", ascending=False).iloc[0]
        max_streak_days = int(longest["count"])
        if max_streak_days >= min_streak_days:
            records.append({
                "equipment_category": cat, "equipment_id": eq_id,
                "longest_gap_start": longest["first"], "longest_gap_end": longest["last"],
                "longest_gap_days": max_streak_days,
                "active_range_start": dates_with_activity.min(),
                "active_range_end": dates_with_activity.max(),
            })
    if not records:
        return pd.DataFrame(columns=[
            "equipment_category", "equipment_id", "longest_gap_start", "longest_gap_end",
            "longest_gap_days", "active_range_start", "active_range_end",
        ])
    return pd.DataFrame(records).sort_values("longest_gap_days", ascending=False).reset_index(drop=True)


def compute_dq_kpis(cleaned: pd.DataFrame, monthly_reconciliation: pd.DataFrame,
                     zero_streaks: Optional[pd.DataFrame] = None,
                     source_coverage_calendar: Optional[pd.DataFrame] = None) -> DataQualityKPIs:
    """Hitung KPI ringkas kualitas data + status akhir PASS/REVIEW/FAILED.

    CATATAN DESAIN (per diskusi proyek): data ini adalah catatan PENGISIAN
    solar, bukan log konsumsi harian wajib -- jadi kepadatan transaksi
    harian yang rendah adalah NORMAL, bukan tanda data hilang. Karena itu
    `data_completeness_percentage` didefinisikan berbasis
    zero-consumption-streak (equipment dgn jeda tak wajar panjang di TENGAH
    rentang aktifnya), bukan rasio hari-ada-transaksi/hari-kalender.
    """
    total_rows = len(cleaned)
    valid_rows = (cleaned["data_status"] == "VALID").sum()
    status_only_rows = (cleaned["data_status"] == "STATUS_ONLY").sum()
    valid_transaction_percentage = ((valid_rows + status_only_rows) / total_rows * 100
                                     if total_rows else 0.0)

    duplicate_count = int((cleaned["data_status"] == "DUPLICATE").sum())
    missing_equipment_count = int((cleaned["equipment_id"].astype(str).str.strip() == "").sum())
    invalid_value_count = int(cleaned["data_status"].isin(["NEGATIVE_VALUE", "UNKNOWN_STATUS"]).sum())
    unusually_high_count = int((cleaned["data_status"] == "UNUSUALLY_HIGH").sum())
    invalid_date_count = int((cleaned["data_status"] == "INVALID_DATE").sum())

    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"]
    total_equipment = valid.groupby(["equipment_category", "equipment_id"]).ngroups
    if zero_streaks is None:
        zero_streaks = detect_zero_consumption_streaks(
            cleaned, source_coverage_calendar=source_coverage_calendar
        )
    n_equipment_with_streak = len(zero_streaks)
    data_completeness_percentage = (
        100.0 * (1 - n_equipment_with_streak / total_equipment) if total_equipment else 0.0
    )

    monthly_difference_liter_max = float(monthly_reconciliation["difference_liter"].abs().max())
    n_months_major = int(monthly_reconciliation["validation_status"].isin(
        ["MAJOR DIFFERENCE", "REQUIRES REVIEW"]).sum())
    reconciliation_status = (
        "FAILED" if (monthly_reconciliation["validation_status"] == "REQUIRES REVIEW").any()
        else "REVIEW" if n_months_major > config.DQ_MAX_MAJOR_RECONCILIATION_MONTHS_FOR_PASS
        else "PASS"
    )

    if (valid_transaction_percentage >= config.DQ_VALID_PCT_PASS_MIN
            and data_completeness_percentage >= config.DQ_COMPLETENESS_PASS_MIN
            and reconciliation_status == "PASS"):
        overall_status = "PASS"
    elif (valid_transaction_percentage >= config.DQ_VALID_PCT_REVIEW_MIN
          and data_completeness_percentage >= config.DQ_COMPLETENESS_REVIEW_MIN
          and reconciliation_status != "FAILED"):
        overall_status = "REVIEW"
    else:
        overall_status = "FAILED"

    return DataQualityKPIs(
        data_completeness_percentage=round(data_completeness_percentage, 2),
        valid_transaction_percentage=round(valid_transaction_percentage, 2),
        duplicate_count=duplicate_count,
        missing_equipment_count=missing_equipment_count,
        invalid_value_count=invalid_value_count,
        unusually_high_count=unusually_high_count,
        invalid_date_count=invalid_date_count,
        monthly_difference_liter_max=round(monthly_difference_liter_max, 1),
        n_months_major_reconciliation_issue=n_months_major,
        reconciliation_status=reconciliation_status,
        overall_status=overall_status,
    )


def save_outputs(kpis: DataQualityKPIs, missing_dates: pd.DataFrame, streaks: pd.DataFrame,
                  output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    kpis.to_frame().to_csv(output_dir / "data_quality_summary.csv", index=False)
    missing_dates.to_csv(output_dir / "missing_dates_report.csv", index=False)
    streaks.to_csv(output_dir / "zero_consumption_streaks.csv", index=False)


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()

    missing_dates = detect_missing_dates(
        result.cleaned_fuel_data, result.source_coverage_calendar
    )
    streaks = detect_zero_consumption_streaks(
        result.cleaned_fuel_data, source_coverage_calendar=result.source_coverage_calendar
    )
    kpis = compute_dq_kpis(
        result.cleaned_fuel_data, result.monthly_reconciliation, streaks,
        source_coverage_calendar=result.source_coverage_calendar,
    )
    save_outputs(kpis, missing_dates, streaks)

    print("=== KPI Data Quality ===")
    for k, v in kpis.__dict__.items():
        print(f"  {k}: {v}")

    print(f"\n=== Missing dates (armada, seluruh kategori): {len(missing_dates)} hari ===")
    print(missing_dates)

    print(f"\n=== Equipment dengan zero-consumption streak >= "
          f"{config.ZERO_CONSUMPTION_STREAK_DAYS} hari: {len(streaks)} ===")
    print(streaks.head(15))

    print("\n=== Ringkasan isu rekonsiliasi ===")
    print(summarize_reconciliation_issues(result.monthly_reconciliation,
                                           result.category_monthly_reconciliation))
