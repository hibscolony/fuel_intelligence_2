"""
reconciliation.py
==================
Modul rekonsiliasi: membandingkan total solar hasil hitung ulang dari
cleaned_fuel_data.csv (baris transaksi unit harian) dengan angka yang
tertulis pada blok TOTALISATOR workbook. Workbook TIDAK PERNAH dipakai
sebagai sumber angka final -- hanya sebagai pembanding/validasi.

Dua level rekonsiliasi:
1. Bulanan, total seluruh alat (GRAND_TOTAL vs recalculated_total).
2. Bulanan, per kategori alat (baris kategori pada TOTALISATOR vs
   recalculated per kategori) -- mendekati "subtotal per equipment" pada
   spesifikasi, karena workbook tidak menyediakan subtotal per ID individual,
   hanya per kategori per hari.

PENTING: pengelompokan memakai (year, month) -- BUKAN month saja -- supaya
data multi-tahun (mis. 2025 dan 2026) tidak tercampur jadi satu "Januari".
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config

MONTH_NAMES = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
               7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"}

CATEGORY_LABEL_MAP = {
    "RTGC": ["RTGC"],
    "HEAD_TRUCK": ["HEAD TRUCK", "HEAD TRUK"],
    # 2025 melaporkan SUPPORT sbg satu baris gabungan; 2026 memecahnya jadi 3 baris terpisah
    # (Site Loader/Reach Stacker/Forklift) -- keduanya dijumlahkan sbg satu kategori SUPPORT.
    "SUPPORT": ["SL/RS/FRK", "SITE LOADER", "RIGTH STAGGER", "RICHT STAGGER", "REACH STACKER", "FORKLIF"],
    "KEND_OPS": ["KEND OP", "KEND OPS"],
    "BUS": ["BUS"],
    "ELF": ["ELF"],
    "MODUL": ["MODUL"],
    # Kategori baru yang muncul di layout 2026 (tidak ada di 2025)
    "COMPRESSOR": ["COMP MEANTENAN", "COMP MEANTENANT", "COMPRESOR", "KOMPRESOR"],
}


def classify_reconciliation(pct: float) -> str:
    """Klasifikasikan besaran selisih (%) menjadi status rekonsiliasi.
    Ambang diambil dari config -- bisa diubah tanpa mengubah kode ini.
    """
    pct = abs(pct)
    if pct < config.RECONCILIATION_MATCH_PCT:
        return "MATCH"
    if pct < config.RECONCILIATION_MINOR_PCT:
        return "MINOR DIFFERENCE"
    if pct < config.RECONCILIATION_MAJOR_PCT:
        return "MAJOR DIFFERENCE"
    return "REQUIRES REVIEW"


def build_monthly_reconciliation(cleaned: pd.DataFrame, totalisator_df: pd.DataFrame) -> pd.DataFrame:
    """Rekonsiliasi bulanan, total SELURUH alat -- dikelompokkan per
    (year, month) supaya bulan yang sama di tahun berbeda tidak tercampur.

    Baris dengan `data_status == "INVALID_DATE"` dikeluarkan dari perhitungan
    ulang (bukan dipaksakan ke bulan manapun), karena tanggalnya sendiri
    tidak valid.
    """
    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"]
    recalc_month = valid.groupby(["year", "month"])["fuel_liter"].sum(min_count=1)

    if totalisator_df.empty:
        # PENTING: index kosong ini HARUS MultiIndex ber-nama (year, month) yang
        # sama persis dengan recalc_month, bukan Series kosong polos -- kalau
        # tidak, saat digabung lewat pd.DataFrame({...}) di bawah, pandas bisa
        # kehilangan nama level index (union Index kosong vs MultiIndex), yang
        # bikin reset_index() gagal menghasilkan kolom "year"/"month" (KeyError).
        wb_month = pd.Series(
            dtype=float,
            index=pd.MultiIndex.from_tuples([], names=["year", "month"]),
        )
    else:
        wb_month = (totalisator_df[totalisator_df["category_label"] == "GRAND_TOTAL"]
                    .groupby(["year", "month"])["workbook_value"].sum())

    table = pd.DataFrame({"workbook_reported_total": wb_month,
                           "recalculated_total": recalc_month}).reset_index()
    table["month_name"] = table["month"].map(MONTH_NAMES) + " " + table["year"].astype(str)
    table["difference_liter"] = table["recalculated_total"] - table["workbook_reported_total"]
    table["difference_percentage"] = (
        table["difference_liter"] / table["workbook_reported_total"] * 100).round(2)
    table["validation_status"] = table["difference_percentage"].apply(classify_reconciliation)

    return table[["year", "month", "month_name", "workbook_reported_total", "recalculated_total",
                  "difference_liter", "difference_percentage", "validation_status"]].sort_values(
        ["year", "month"]).reset_index(drop=True)


def build_category_monthly_reconciliation(cleaned: pd.DataFrame,
                                           totalisator_df: pd.DataFrame) -> pd.DataFrame:
    """Rekonsiliasi bulanan PER KATEGORI alat, dikelompokkan per (year, month)
    -- pendekatan paling dekat dengan "subtotal per equipment" workbook,
    karena workbook hanya menyediakan baris TOTAL per kategori per hari,
    bukan per ID individual.

    Satu kategori internal bisa berpadanan dengan LEBIH DARI SATU label baris
    TOTALISATOR (mis. layout 2026 memecah "SUPPORT" jadi 3 baris terpisah:
    Site Loader/Reach Stacker/Forklift) -- semuanya dijumlahkan sesuai
    `CATEGORY_LABEL_MAP` sebelum dibandingkan dgn hasil hitung ulang.
    """
    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"].copy()
    recalc = (valid.groupby(["year", "month", "equipment_category"])["fuel_liter"]
              .sum(min_count=1).reset_index())

    if totalisator_df.empty:
        wb = pd.DataFrame(columns=["year", "month", "equipment_category", "workbook_reported_total"])
    else:
        label_to_category = {label: cat for cat, labels in CATEGORY_LABEL_MAP.items() for label in labels}
        tot = totalisator_df[totalisator_df["category_label"] != "GRAND_TOTAL"].copy()
        tot["equipment_category"] = tot["category_label"].map(label_to_category)
        tot = tot.dropna(subset=["equipment_category"])  # buang baris non-kategori (stock tank, dst)
        wb = (tot.groupby(["year", "month", "equipment_category"])["workbook_value"]
              .sum().reset_index().rename(columns={"workbook_value": "workbook_reported_total"}))

    merged = recalc.merge(wb, on=["year", "month", "equipment_category"], how="left").rename(
        columns={"fuel_liter": "recalculated_total"})
    merged["month_name"] = merged["month"].map(MONTH_NAMES) + " " + merged["year"].astype(str)
    merged["difference_liter"] = merged["recalculated_total"] - merged["workbook_reported_total"]
    merged["difference_percentage"] = (
        merged["difference_liter"] / merged["workbook_reported_total"] * 100).round(2)
    merged["validation_status"] = merged["difference_percentage"].apply(
        lambda p: classify_reconciliation(p) if pd.notna(p) else "NO_WORKBOOK_VALUE")

    cols = ["year", "month", "month_name", "equipment_category", "workbook_reported_total",
            "recalculated_total", "difference_liter", "difference_percentage", "validation_status"]
    return merged[cols].sort_values(["year", "month", "equipment_category"]).reset_index(drop=True)


def summarize_reconciliation_issues(monthly: pd.DataFrame, category_monthly: pd.DataFrame) -> dict:
    """Ringkasan singkat untuk dipakai KPI di data_quality.py / dashboard."""
    problem_months = monthly[monthly["validation_status"].isin(
        ["MAJOR DIFFERENCE", "REQUIRES REVIEW"])]
    problem_cat_month = category_monthly[category_monthly["validation_status"].isin(
        ["MAJOR DIFFERENCE", "REQUIRES REVIEW"])]
    return {
        "n_months_with_major_difference": len(problem_months),
        "months_with_major_difference": problem_months["month_name"].tolist(),
        "n_category_months_with_major_difference": len(problem_cat_month),
        "max_abs_difference_percentage": monthly["difference_percentage"].abs().max(),
    }


if __name__ == "__main__":
    from src.data_cleaning import run_cleaning_pipeline

    result = run_cleaning_pipeline()
    print("=== Rekonsiliasi bulanan (total seluruh alat) ===")
    print(result.monthly_reconciliation)
    print("\n=== Rekonsiliasi bulanan per kategori (10 baris pertama) ===")
    print(result.category_monthly_reconciliation.head(10))
    print("\n=== Ringkasan isu rekonsiliasi ===")
    print(summarize_reconciliation_issues(result.monthly_reconciliation,
                                           result.category_monthly_reconciliation))
