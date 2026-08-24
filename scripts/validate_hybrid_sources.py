"""Validate the real hybrid Excel + UJB pipeline and persist an audit summary.

This script intentionally runs against repository data, not synthetic fixtures.
It fails with a non-zero exit code when core source-precedence invariants are
violated, making it suitable for a manual GitHub Actions validation workflow.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from src.data_cleaning import run_cleaning_pipeline
from src.source_reconciliation import UJB_PREFERRED_CATEGORIES
from src.ujb_source import load_ujb_long_df


OUTPUT_DIR = config.PROCESSED_DATA_DIR
AUDIT_PATH = OUTPUT_DIR / "source_reconciliation_audit.csv"
SUMMARY_PATH = OUTPUT_DIR / "hybrid_validation_summary.json"


def _numeric_sum(df: pd.DataFrame, column: str) -> float:
    if df.empty or column not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum())


def _source_summary(cleaned: pd.DataFrame) -> dict:
    result: dict[str, dict[str, float | int]] = {}
    if cleaned.empty or "source_system" not in cleaned.columns:
        return result
    for source, sub in cleaned.groupby("source_system", dropna=False):
        name = str(source)
        result[name] = {
            "rows": int(len(sub)),
            "fuel_liter": _numeric_sum(sub, "fuel_liter"),
            "valid_rows": int((sub["data_status"] == "VALID").sum()) if "data_status" in sub else 0,
        }
    return result


def _audit_reason_summary(audit: pd.DataFrame) -> list[dict]:
    if audit.empty:
        return []
    grouped = (
        audit.groupby(["source_system", "selection_reason"], dropna=False)
        .agg(
            input_rows=("input_rows", "sum"),
            selected_rows=("selected_rows", "sum"),
            suppressed_rows=("suppressed_rows", "sum"),
            input_liter=("input_liter", "sum"),
            selected_liter=("selected_liter", "sum"),
            suppressed_liter=("suppressed_liter", "sum"),
        )
        .reset_index()
    )
    records = []
    for row in grouped.to_dict(orient="records"):
        records.append({
            "source_system": str(row["source_system"]),
            "selection_reason": str(row["selection_reason"]),
            "input_rows": int(row["input_rows"]),
            "selected_rows": int(row["selected_rows"]),
            "suppressed_rows": int(row["suppressed_rows"]),
            "input_liter": float(row["input_liter"]),
            "selected_liter": float(row["selected_liter"]),
            "suppressed_liter": float(row["suppressed_liter"]),
        })
    return records


def main() -> int:
    if config.DATA_SOURCE_MODE != "hybrid":
        raise RuntimeError(
            f"Validator harus dijalankan dengan FUEL_DATA_SOURCE=hybrid; "
            f"mode saat ini {config.DATA_SOURCE_MODE!r}."
        )

    result = run_cleaning_pipeline()
    cleaned = result.cleaned_fuel_data.copy()
    audit = result.source_reconciliation_audit.copy()
    ujb = load_ujb_long_df()

    cleaned["date"] = pd.to_datetime(cleaned["date"], errors="coerce").dt.normalize()
    audit["date"] = pd.to_datetime(audit["date"], errors="coerce").dt.normalize()
    ujb["date"] = pd.to_datetime(ujb["date"], errors="coerce").dt.normalize()

    direct_categories = sorted(UJB_PREFERRED_CATEGORIES)
    direct_selected = cleaned[
        cleaned["equipment_category"].astype(str).str.upper().isin(direct_categories)
    ].copy()

    source_counts = (
        direct_selected.groupby(["date", "equipment_category"])["source_system"]
        .nunique(dropna=True)
    )
    overlapping_selected_groups = source_counts[source_counts > 1]

    ujb_selected = cleaned[cleaned["source_system"].astype(str).str.upper().eq("UJB")].copy()
    duplicate_event_keys = 0
    duplicate_event_key_examples: list[str] = []
    if "source_event_key" in ujb_selected.columns:
        keys = ujb_selected["source_event_key"].astype("string")
        keyed = keys[keys.notna() & keys.str.strip().ne("")]
        dup_mask = keyed.duplicated(keep=False)
        duplicate_event_keys = int(dup_mask.sum())
        duplicate_event_key_examples = keyed[dup_mask].drop_duplicates().head(10).astype(str).tolist()

    unapproved_selected_rows = int(
        audit.loc[
            audit["selection_reason"].eq("UJB_UNAPPROVED_CATEGORY_SUPPRESSED"),
            "selected_rows",
        ].sum()
    )

    blank_reason_rows = int(
        cleaned["source_selection_reason"].fillna("").astype(str).str.strip().eq("").sum()
    )

    audit_selected_liter = _numeric_sum(audit, "selected_liter")
    cleaned_selected_liter = _numeric_sum(cleaned, "fuel_liter")
    selected_liter_diff = abs(audit_selected_liter - cleaned_selected_liter)

    coverage_dates = sorted(ujb["date"].dropna().unique())
    coverage_start = pd.Timestamp(coverage_dates[0]).strftime("%Y-%m-%d") if coverage_dates else None
    coverage_end = pd.Timestamp(coverage_dates[-1]).strftime("%Y-%m-%d") if coverage_dates else None

    violations = {
        "direct_category_multi_source_groups": int(len(overlapping_selected_groups)),
        "duplicate_selected_ujb_event_key_rows": duplicate_event_keys,
        "unapproved_ujb_selected_rows": unapproved_selected_rows,
        "blank_source_selection_reason_rows": blank_reason_rows,
        "selected_liter_audit_mismatch": bool(selected_liter_diff > 1e-6),
    }

    passed = not any([
        violations["direct_category_multi_source_groups"],
        violations["duplicate_selected_ujb_event_key_rows"],
        violations["unapproved_ujb_selected_rows"],
        violations["blank_source_selection_reason_rows"],
        violations["selected_liter_audit_mismatch"],
    ])

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_source_mode": config.DATA_SOURCE_MODE,
        "passed": passed,
        "hybrid_selected_rows": int(len(cleaned)),
        "hybrid_selected_liter": cleaned_selected_liter,
        "audit_selected_liter": audit_selected_liter,
        "selected_liter_abs_diff": selected_liter_diff,
        "source_summary": _source_summary(cleaned),
        "ujb_history": {
            "rows": int(len(ujb)),
            "unique_event_keys": int(ujb["source_event_key"].nunique()) if "source_event_key" in ujb else None,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "coverage_days": int(len(coverage_dates)),
        },
        "violations": violations,
        "duplicate_event_key_examples": duplicate_event_key_examples,
        "source_reason_summary": _audit_reason_summary(audit),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    audit.to_csv(AUDIT_PATH, index=False)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nAudit written to: {AUDIT_PATH}")
    print(f"Summary written to: {SUMMARY_PATH}")

    if not passed:
        print("\nHYBRID VALIDATION FAILED", file=sys.stderr)
        return 1

    print("\nHYBRID VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
