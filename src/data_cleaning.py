"""
data_cleaning.py
================
Mengubah hasil parsing mentah (data_loader.ParseResult) menjadi 4 output
Tahap 2/3:

- cleaned_fuel_data.csv       : baris transaksi + data_status/issue_code
- equipment_master.csv        : daftar equipment unik + deteksi varian penulisan ID
- data_quality_report.csv     : rekap semua isu data per kode
- monthly_reconciliation.csv  : validasi total bulanan vs workbook

Prinsip: TIDAK ADA baris yang dihapus diam-diam. Baris bermasalah selalu
diberi label lewat kolom `data_status` + `issue_code`, dan tetap muncul di
cleaned_fuel_data.csv (kecuali murni duplikat exact, yang tetap disimpan
tapi ditandai DUPLICATE alih-alih dihapus).
"""
from __future__ import annotations

import re
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
from src.data_loader import ParseResult, parse_workbook
from src.reconciliation import build_monthly_reconciliation, build_category_monthly_reconciliation
from src.ujb_source import load_ujb_long_df, NoUjbDataError

MONTH_NAMES = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
               7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"}


def standardize_equipment_id(raw_id: str) -> str:
    """Rapikan ID mentah menjadi bentuk kanonik.

    ID yang mengandung '/' (mis. "B 7963 TAA/ BUS 03") dianggap SAMA dengan
    ID di depan '/' ("B 7963 TAA") -- bagian setelah '/' adalah alias/catatan
    tambahan (nama unit lama, kode radio, dsb), BUKAN unit yang berbeda.
    Ini dikonfirmasi langsung oleh pengguna, jadi diterapkan di titik paling
    awal pembersihan data supaya konsisten di SELURUH pipeline (equipment
    master, anomaly detection, health score, clustering, dashboard, dst).
    """
    base = str(raw_id).split("/")[0].strip()
    return re.sub(r"\s+", " ", base)


def canonicalize_id_for_matching(equipment_id: str) -> str:
    """Bentuk kunci pencocokan yang lebih longgar dari `equipment_id`, dipakai
    HANYA untuk mengelompokkan kemungkinan varian penulisan ID yang sama --
    bukan pengganti equipment_id asli. Menghapus semua non-alfanumerik dan
    membuang alias tambahan setelah '/'.
    """
    base = equipment_id.split("/")[0]
    return re.sub(r"[^A-Za-z0-9]", "", base).upper()


def flag_high_outliers(df: pd.DataFrame) -> pd.Series:
    """Tandai nilai fuel_liter yang tidak wajar tinggi, per kategori alat,
    memakai ambang IQR (Q3 + config.OUTLIER_IQR_MULTIPLIER * IQR).
    Hanya dihitung dari baris yang tanggalnya valid.
    """
    flags = pd.Series(False, index=df.index)
    valid_mask = df["date"].notna()
    for cat, sub in df[valid_mask].groupby("equipment_category"):
        vals = sub["fuel_liter"].dropna()
        if len(vals) < 10:
            continue
        q1, q3 = vals.quantile([0.25, 0.75])
        iqr = q3 - q1
        threshold = q3 + config.OUTLIER_IQR_MULTIPLIER * iqr
        flags.loc[sub.index] = sub["fuel_liter"] > threshold
    return flags


@dataclass
class CleaningResult:
    cleaned_fuel_data: pd.DataFrame
    equipment_master: pd.DataFrame
    data_quality_report: pd.DataFrame
    monthly_reconciliation: pd.DataFrame
    category_monthly_reconciliation: pd.DataFrame
    totalisator_df: pd.DataFrame


def _classify_row(row: pd.Series) -> tuple[str, Optional[str]]:
    """Tentukan (data_status, issue_code) satu baris berdasarkan flag-flag
    yang sudah dihitung sebelumnya. Prioritas: invalid_date > negative >
    duplicate > unusually_high > unknown_status > status_only > valid.
    """
    if row["flag_invalid_date"]:
        return "INVALID_DATE", "INV_DATE_001"
    if row["flag_negative_value"]:
        return "NEGATIVE_VALUE", "NEG_VAL_001"
    if row["flag_duplicate"]:
        return "DUPLICATE", "DUP_001"
    if row["flag_unusually_high"]:
        return "UNUSUALLY_HIGH", "HIGH_VAL_001"
    if row["flag_unknown_status"]:
        return "UNKNOWN_STATUS", "UNK_STAT_001"
    if pd.isna(row["fuel_liter"]) and pd.notna(row["status_text"]):
        return "STATUS_ONLY", None
    if pd.isna(row["fuel_liter"]):
        return "MISSING", "MISS_001"
    return "VALID", None


def build_cleaned_fuel_data(long_df: pd.DataFrame) -> pd.DataFrame:
    """Bangun cleaned_fuel_data.csv dari long_df mentah (kolom: date, year,
    month, equipment_category, equipment_id, fuel_liter, status_text,
    source_sheet, source_file, source_row) -- dipakai baik untuk long_df
    hasil parsing Excel (data_loader.parse_workbook) MAUPUN long_df hasil
    scrape UJB (ujb_source.load_ujb_long_df), supaya logika flagging
    (duplikat, outlier, dst) konsisten terlepas dari sumber datanya.
    """
    df = long_df.copy()
    df["equipment_id"] = df["equipment_id"].map(standardize_equipment_id)

    df["flag_invalid_date"] = df["date"].isna()
    df["flag_negative_value"] = df["fuel_liter"] < 0
    df["flag_duplicate"] = (
        df.duplicated(subset=["date", "equipment_category", "equipment_id"], keep=False)
        & ~df["flag_invalid_date"]
    )
    df["flag_unusually_high"] = flag_high_outliers(df)
    df["flag_unknown_status"] = df["status_text"].notna() & ~df["status_text"].isin(config.STATUS_TOKENS)

    statuses = df.apply(_classify_row, axis=1, result_type="expand")
    df["data_status"], df["issue_code"] = statuses[0], statuses[1]

    out_cols = ["date", "year", "month", "equipment_category", "equipment_id", "fuel_liter",
                "status_text", "source_sheet", "source_file", "source_row", "data_status", "issue_code"]
    for c in out_cols:
        if c not in df.columns:
            df[c] = None
    return df[out_cols].sort_values(["equipment_category", "equipment_id", "date"]).reset_index(drop=True)


def build_equipment_master(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Bangun daftar equipment unik + deteksi kemungkinan varian penulisan ID
    (mis. 'FRK 05 A' vs 'FRK 05A') lewat kunci pencocokan longgar. Deteksi ini
    bersifat INFORMATIF -- perlu verifikasi manual sebelum digabung permanen.
    """
    valid = cleaned[cleaned["data_status"] != "INVALID_DATE"]
    grouped = valid.groupby(["equipment_category", "equipment_id"]).agg(
        first_seen_date=("date", "min"),
        last_seen_date=("date", "max"),
        n_records=("date", "count"),
        n_valid_liter_records=("fuel_liter", lambda s: s.notna().sum()),
    ).reset_index()

    grouped["match_key"] = grouped["equipment_id"].map(canonicalize_id_for_matching)
    variant_groups = (
        grouped.groupby(["equipment_category", "match_key"])["equipment_id"]
        .apply(lambda s: sorted(set(s)))
    )
    grouped["id_variants"] = grouped.apply(
        lambda r: [v for v in variant_groups.loc[(r["equipment_category"], r["match_key"])]
                   if v != r["equipment_id"]],
        axis=1,
    )
    grouped["has_possible_variant"] = grouped["id_variants"].map(len) > 0

    # unit dianggap "baru" jika kemunculan pertamanya bukan di bulan Januari
    grouped["is_new_after_january"] = grouped["first_seen_date"].dt.month > 1

    return grouped.drop(columns=["match_key"]).sort_values(
        ["equipment_category", "equipment_id"]).reset_index(drop=True)


def build_data_quality_report(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Rekap semua isu data per issue_code, dengan contoh & jumlah kejadian."""
    issues = cleaned[cleaned["data_status"] != "VALID"].copy()
    if issues.empty:
        return pd.DataFrame(columns=["issue_code", "data_status", "n_occurrences",
                                      "n_equipment_affected", "example_equipment_id",
                                      "example_source_sheet", "example_source_row", "description"])

    descriptions = {
        "INV_DATE_001": "Tanggal tidak valid (kemungkinan kolom hari ikut ter-copy dari bulan lain, mis. tanggal 30/31 Februari)",
        "NEG_VAL_001": "Nilai liter negatif -- tidak masuk akal secara fisik, kemungkinan salah input",
        "DUP_001": "Transaksi duplikat: kombinasi (tanggal, kategori, equipment_id) muncul lebih dari sekali",
        "HIGH_VAL_001": "Nilai liter tidak wajar tinggi dibanding pola historis kategori yang sama (ambang IQR)",
        "UNK_STAT_001": "Teks status pada sel tidak dikenali (bukan FULL/PM/GRAHA/SCRUB/LELANG/KURAS)",
        "MISS_001": "Nilai liter kosong tanpa status yang jelas",
    }

    report = (
        issues.groupby(["issue_code", "data_status"])
        .agg(
            n_occurrences=("equipment_id", "count"),
            n_equipment_affected=("equipment_id", "nunique"),
            example_equipment_id=("equipment_id", "first"),
            example_source_sheet=("source_sheet", "first"),
            example_source_row=("source_row", "first"),
        )
        .reset_index()
    )
    report["description"] = report["issue_code"].map(descriptions).fillna("")
    return report.sort_values("n_occurrences", ascending=False).reset_index(drop=True)


def run_cleaning_pipeline(path: Optional[Path] = None) -> CleaningResult:
    """Jalankan pipeline penuh: ambil data mentah -> cleaning -> output tabel.

    Sumber data ditentukan config.DATA_SOURCE_MODE:
    - "hybrid" (default): gabungan Excel + UJB SEBELUM proses cleaning/flagging,
      supaya duplikat & outlier terdeteksi konsisten lintas sumber. Excel tetap
      relevan untuk kategori alat yang tidak lewat dispenser UJB (RTGC, genset).
    - "ujb": murni dari hasil scrape UJB.
    - "excel": mode lama, murni workbook manual.

    Kalau salah satu sumber tidak tersedia di mode "hybrid" (mis. belum ada
    file Excel, atau scraper UJB belum pernah jalan), pipeline tetap jalan
    pakai sumber yang ada -- TIDAK error, supaya dashboard tetap bisa dipakai
    di tahap transisi (mis. baru pasang UJB, histori Excel lama masih mau
    dipertahankan bertahap).
    """
    excel_long_df = pd.DataFrame()
    totalisator_df = pd.DataFrame()
    ujb_long_df = pd.DataFrame()

    if config.DATA_SOURCE_MODE in ("hybrid", "excel"):
        try:
            parse_result = parse_workbook(path)
            excel_long_df = parse_result.long_df
            totalisator_df = parse_result.totalisator_df
        except FileNotFoundError:
            if config.DATA_SOURCE_MODE == "excel":
                raise  # mode "excel" murni memang wajib ada filenya

    if config.DATA_SOURCE_MODE in ("hybrid", "ujb"):
        try:
            ujb_long_df = load_ujb_long_df()
        except NoUjbDataError:
            if config.DATA_SOURCE_MODE == "ujb":
                raise  # mode "ujb" murni memang wajib ada datanya

    combined_long_df = pd.concat([excel_long_df, ujb_long_df], ignore_index=True)
    if combined_long_df.empty:
        raise NoUjbDataError(
            "Tidak ada data dari sumber manapun -- baik Excel (data/raw/*.xls*) "
            "maupun hasil scrape UJB (data/raw/ujb_scraped_latest.csv) tidak ditemukan."
        )

    cleaned = build_cleaned_fuel_data(combined_long_df)
    equipment_master = build_equipment_master(cleaned)
    dq_report = build_data_quality_report(cleaned)
    reconciliation = build_monthly_reconciliation(cleaned, totalisator_df)
    category_reconciliation = build_category_monthly_reconciliation(cleaned, totalisator_df)
    return CleaningResult(cleaned, equipment_master, dq_report, reconciliation,
                           category_reconciliation, totalisator_df)


def save_outputs(result: CleaningResult, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    result.cleaned_fuel_data.to_csv(output_dir / "cleaned_fuel_data.csv", index=False)
    result.equipment_master.to_csv(output_dir / "equipment_master.csv", index=False)
    result.data_quality_report.to_csv(output_dir / "data_quality_report.csv", index=False)
    result.monthly_reconciliation.to_csv(output_dir / "monthly_reconciliation.csv", index=False)
    result.category_monthly_reconciliation.to_csv(
        output_dir / "monthly_reconciliation_by_category.csv", index=False)


if __name__ == "__main__":
    result = run_cleaning_pipeline()
    save_outputs(result)

    print("=== cleaned_fuel_data.csv ===")
    print(f"Total baris: {len(result.cleaned_fuel_data):,}")
    print(result.cleaned_fuel_data["data_status"].value_counts())

    print("\n=== equipment_master.csv ===")
    print(f"Total equipment unik: {len(result.equipment_master)}")
    print(f"Equipment dengan kemungkinan varian ID: "
          f"{result.equipment_master['has_possible_variant'].sum()}")
    print(result.equipment_master[result.equipment_master["has_possible_variant"]]
          [["equipment_category", "equipment_id", "id_variants"]].head(10))

    print("\n=== data_quality_report.csv ===")
    print(result.data_quality_report)

    print("\n=== monthly_reconciliation.csv ===")
    print(result.monthly_reconciliation)
