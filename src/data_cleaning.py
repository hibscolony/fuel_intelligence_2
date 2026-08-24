"""
data_cleaning.py
================
Mengubah sumber Excel/UJB menjadi dataset transaksi bersih yang dapat diaudit.

Prinsip utama:
- tidak ada double counting lintas sumber di mode hybrid;
- provenance sumber dipertahankan pada setiap baris terpilih;
- transaksi UJB dibedakan memakai source_event_key/event_time, sehingga satu
  unit boleh melakukan beberapa pengisian sah pada hari yang sama;
- baris bermasalah diberi data_status/issue_code, bukan dibuang diam-diam.
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
from src.data_loader import parse_workbook
from src.reconciliation import build_monthly_reconciliation, build_category_monthly_reconciliation
from src.source_reconciliation import reconcile_excel_and_ujb
from src.ujb_source import load_ujb_long_df, NoUjbDataError

MONTH_NAMES = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
               7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"}


def standardize_equipment_id(raw_id: str) -> str:
    """Rapikan ID mentah menjadi bentuk kanonik.

    Bagian setelah '/' diperlakukan sebagai alias/catatan tambahan, bukan unit
    berbeda. Whitespace juga dinormalisasi.
    """
    base = str(raw_id).split("/")[0].strip()
    return re.sub(r"\s+", " ", base)


def canonicalize_id_for_matching(equipment_id: str) -> str:
    """Kunci longgar untuk mendeteksi kemungkinan varian penulisan ID."""
    base = equipment_id.split("/")[0]
    return re.sub(r"[^A-Za-z0-9]", "", base).upper()


def flag_high_outliers(df: pd.DataFrame) -> pd.Series:
    """Tandai nilai fuel_liter tinggi per kategori dengan ambang IQR."""
    flags = pd.Series(False, index=df.index)
    valid_mask = df["date"].notna()
    for _, sub in df[valid_mask].groupby("equipment_category"):
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
    source_reconciliation_audit: pd.DataFrame


def _classify_row(row: pd.Series) -> tuple[str, Optional[str]]:
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


def _flag_source_aware_duplicates(df: pd.DataFrame) -> pd.Series:
    """Duplicate semantics berbeda untuk Excel dan UJB.

    Excel merepresentasikan satu cell unit-hari, jadi pengulangan
    (date, category, equipment_id) tetap mencurigakan. UJB adalah event stream:
    beberapa pengisian unit yang sama pada hari yang sama adalah sah; duplicate
    UJB hanya ditandai jika source_event_key yang sama benar-benar berulang.
    """
    flags = pd.Series(False, index=df.index)
    systems = df.get("source_system", pd.Series("EXCEL", index=df.index)).fillna("EXCEL").astype(str).str.upper()
    valid_date = df["date"].notna()

    excel_mask = systems.ne("UJB") & valid_date
    if excel_mask.any():
        excel = df.loc[excel_mask]
        flags.loc[excel.index] = excel.duplicated(
            subset=["date", "equipment_category", "equipment_id"], keep=False
        )

    ujb_mask = systems.eq("UJB") & valid_date
    if ujb_mask.any() and "source_event_key" in df.columns:
        keys = df["source_event_key"].astype("string")
        keyed_mask = ujb_mask & keys.notna() & keys.str.strip().ne("")
        if keyed_mask.any():
            keyed = df.loc[keyed_mask]
            flags.loc[keyed.index] = keyed.duplicated(
                subset=["source_event_key"], keep=False
            )

    return flags


def build_cleaned_fuel_data(long_df: pd.DataFrame) -> pd.DataFrame:
    """Bangun cleaned_fuel_data dari long-form hasil source reconciliation."""
    df = long_df.copy()
    df["equipment_id"] = df["equipment_id"].map(standardize_equipment_id)

    if "source_system" not in df.columns:
        source_file = df.get("source_file", pd.Series("", index=df.index)).astype(str).str.lower()
        df["source_system"] = np.where(source_file.str.contains("ujb"), "UJB", "EXCEL")
    if "source_selection_reason" not in df.columns:
        df["source_selection_reason"] = "SOURCE_PRECEDENCE_NOT_APPLIED"

    df["flag_invalid_date"] = df["date"].isna()
    df["flag_negative_value"] = df["fuel_liter"] < 0
    df["flag_duplicate"] = _flag_source_aware_duplicates(df)
    df["flag_unusually_high"] = flag_high_outliers(df)

    # UJB status seperti "online" adalah metadata transaksi vendor, bukan token
    # status cell Excel (FULL/PM/dll), sehingga tidak boleh dianggap UNKNOWN.
    is_ujb = df["source_system"].astype(str).str.upper().eq("UJB")
    df["flag_unknown_status"] = (
        ~is_ujb
        & df["status_text"].notna()
        & ~df["status_text"].isin(config.STATUS_TOKENS)
    )

    statuses = df.apply(_classify_row, axis=1, result_type="expand")
    df["data_status"], df["issue_code"] = statuses[0], statuses[1]

    out_cols = [
        "date", "event_time", "year", "month", "equipment_category", "equipment_id",
        "fuel_liter", "status_text", "source_sheet", "source_file", "source_row",
        "source_event_key", "source_system", "source_selection_reason",
        "data_status", "issue_code",
    ]
    for c in out_cols:
        if c not in df.columns:
            df[c] = None
    return df[out_cols].sort_values(
        ["equipment_category", "equipment_id", "date", "event_time"],
        na_position="last",
    ).reset_index(drop=True)


def build_equipment_master(cleaned: pd.DataFrame) -> pd.DataFrame:
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
    grouped["is_new_after_january"] = grouped["first_seen_date"].dt.month > 1

    return grouped.drop(columns=["match_key"]).sort_values(
        ["equipment_category", "equipment_id"]).reset_index(drop=True)


def build_data_quality_report(cleaned: pd.DataFrame) -> pd.DataFrame:
    issues = cleaned[cleaned["data_status"] != "VALID"].copy()
    if issues.empty:
        return pd.DataFrame(columns=["issue_code", "data_status", "n_occurrences",
                                      "n_equipment_affected", "example_equipment_id",
                                      "example_source_sheet", "example_source_row", "description"])

    descriptions = {
        "INV_DATE_001": "Tanggal tidak valid (kemungkinan kolom hari ikut ter-copy dari bulan lain)",
        "NEG_VAL_001": "Nilai liter negatif -- kemungkinan salah input",
        "DUP_001": "Duplikat sumber: cell unit-hari Excel berulang atau source_event_key UJB berulang",
        "HIGH_VAL_001": "Nilai liter tidak wajar tinggi dibanding pola historis kategori yang sama (ambang IQR)",
        "UNK_STAT_001": "Teks status Excel tidak dikenali (bukan FULL/PM/GRAHA/SCRUB/LELANG/KURAS)",
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
    """Load sources, reconcile precedence, then run one shared cleaning path."""
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
                raise

    if config.DATA_SOURCE_MODE in ("hybrid", "ujb"):
        try:
            ujb_long_df = load_ujb_long_df()
        except NoUjbDataError:
            if config.DATA_SOURCE_MODE == "ujb":
                raise

    if config.DATA_SOURCE_MODE == "excel":
        source_result = reconcile_excel_and_ujb(excel_long_df, pd.DataFrame())
    elif config.DATA_SOURCE_MODE == "ujb":
        source_result = reconcile_excel_and_ujb(pd.DataFrame(), ujb_long_df)
    else:
        source_result = reconcile_excel_and_ujb(excel_long_df, ujb_long_df)

    combined_long_df = source_result.selected_df
    if combined_long_df.empty:
        raise NoUjbDataError(
            "Tidak ada data dari sumber manapun -- baik Excel (data/raw/*.xls*) "
            "maupun UJB history/snapshot tidak ditemukan."
        )

    cleaned = build_cleaned_fuel_data(combined_long_df)
    equipment_master = build_equipment_master(cleaned)
    dq_report = build_data_quality_report(cleaned)
    reconciliation = build_monthly_reconciliation(cleaned, totalisator_df)
    category_reconciliation = build_category_monthly_reconciliation(cleaned, totalisator_df)
    return CleaningResult(
        cleaned,
        equipment_master,
        dq_report,
        reconciliation,
        category_reconciliation,
        totalisator_df,
        source_result.audit_df,
    )


def save_outputs(result: CleaningResult, output_dir: Optional[Path] = None) -> None:
    output_dir = output_dir or config.PROCESSED_DATA_DIR
    result.cleaned_fuel_data.to_csv(output_dir / "cleaned_fuel_data.csv", index=False)
    result.equipment_master.to_csv(output_dir / "equipment_master.csv", index=False)
    result.data_quality_report.to_csv(output_dir / "data_quality_report.csv", index=False)
    result.monthly_reconciliation.to_csv(output_dir / "monthly_reconciliation.csv", index=False)
    result.category_monthly_reconciliation.to_csv(
        output_dir / "monthly_reconciliation_by_category.csv", index=False
    )
    result.source_reconciliation_audit.to_csv(
        output_dir / "source_reconciliation_audit.csv", index=False
    )


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

    print("\n=== source_reconciliation_audit.csv ===")
    print(result.source_reconciliation_audit.tail(20))
