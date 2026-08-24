"""Source precedence and audit layer for Excel + UJB fuel records.

Hybrid mode must not simply concatenate both sources. UJB is the preferred
operational source for equipment that refuels through the dispenser, but only
on calendar dates for which UJB actually has source coverage. Excel remains
the source for non-UJB equipment and for periods before/gaps in UJB history.

The output keeps source provenance on every selected row and returns an audit
table describing how many rows/liters were selected or suppressed per source.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


UJB_PREFERRED_CATEGORIES = frozenset({
    "HEAD_TRUCK",
    "BUS",
    "ELF",
    "FORKLIFT",
    "KEND_OPS",
})


@dataclass
class SourceReconciliationResult:
    selected_df: pd.DataFrame
    audit_df: pd.DataFrame
    ujb_coverage_dates: tuple[pd.Timestamp, ...]


def _annotate_source(df: pd.DataFrame, source_system: str) -> pd.DataFrame:
    out = df.copy()
    if "date" not in out.columns:
        out["date"] = pd.NaT
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["source_system"] = source_system
    if "source_selection_reason" not in out.columns:
        out["source_selection_reason"] = ""
    return out


def _coverage_dates(ujb_df: pd.DataFrame) -> tuple[pd.Timestamp, ...]:
    if ujb_df.empty or "date" not in ujb_df.columns:
        return tuple()
    dates = pd.to_datetime(ujb_df["date"], errors="coerce").dropna().dt.normalize()
    return tuple(sorted(pd.Timestamp(d) for d in dates.unique()))


def _build_audit(input_frames: list[pd.DataFrame]) -> pd.DataFrame:
    columns = [
        "date", "equipment_category", "source_system", "selection_reason",
        "input_rows", "selected_rows", "suppressed_rows",
        "input_liter", "selected_liter", "suppressed_liter",
    ]
    if not input_frames:
        return pd.DataFrame(columns=columns)

    audit_input = pd.concat(input_frames, ignore_index=True, sort=False)
    if audit_input.empty:
        return pd.DataFrame(columns=columns)

    audit_input["_fuel"] = pd.to_numeric(audit_input.get("fuel_liter"), errors="coerce").fillna(0.0)
    audit_input["_selected_liter"] = audit_input["_fuel"].where(audit_input["_selected"], 0.0)
    audit_input["_selected_int"] = audit_input["_selected"].astype(int)
    audit_input["date"] = pd.to_datetime(audit_input["date"], errors="coerce").dt.normalize()

    audit = (
        audit_input.groupby(
            ["date", "equipment_category", "source_system", "_reason"],
            dropna=False,
        )
        .agg(
            input_rows=("_selected", "size"),
            selected_rows=("_selected_int", "sum"),
            input_liter=("_fuel", "sum"),
            selected_liter=("_selected_liter", "sum"),
        )
        .reset_index()
        .rename(columns={"_reason": "selection_reason"})
    )
    audit["suppressed_rows"] = audit["input_rows"] - audit["selected_rows"]
    audit["suppressed_liter"] = audit["input_liter"] - audit["selected_liter"]
    return audit[columns].sort_values(
        ["date", "equipment_category", "source_system", "selection_reason"],
        na_position="last",
    ).reset_index(drop=True)


def reconcile_excel_and_ujb(
    excel_df: pd.DataFrame,
    ujb_df: pd.DataFrame,
    preferred_categories: Iterable[str] = UJB_PREFERRED_CATEGORIES,
) -> SourceReconciliationResult:
    """Select one authoritative source without double counting.

    Rules
    -----
    1. UJB rows are retained whenever present.
    2. For configured dispenser categories, Excel rows are suppressed only on
       dates that are actually represented in UJB history.
    3. Excel rows outside UJB coverage are retained for historical continuity.
    4. Excel rows for non-UJB categories are always retained.
    5. Missing dates inside the apparent UJB range are *not* assumed covered;
       Excel is retained on those dates rather than silently creating a gap.
    """
    preferred = {str(c).strip().upper() for c in preferred_categories}
    excel = _annotate_source(excel_df, "EXCEL")
    ujb = _annotate_source(ujb_df, "UJB")
    coverage = _coverage_dates(ujb)
    coverage_set = set(coverage)

    audit_frames: list[pd.DataFrame] = []
    selected_frames: list[pd.DataFrame] = []

    if not excel.empty:
        excel_dates = excel["date"].dt.normalize()
        excel_categories = excel["equipment_category"].astype(str).str.upper()
        is_covered_date = excel_dates.isin(coverage_set)
        is_ujb_category = excel_categories.isin(preferred)
        suppress = is_covered_date & is_ujb_category

        reasons = pd.Series("EXCEL_NON_UJB_CATEGORY", index=excel.index, dtype="object")
        reasons.loc[is_ujb_category & ~is_covered_date] = "EXCEL_OUTSIDE_UJB_COVERAGE"
        reasons.loc[suppress] = "EXCEL_SUPPRESSED_UJB_PRECEDENCE"

        excel["source_selection_reason"] = reasons
        selected_excel = excel.loc[~suppress].copy()
        selected_frames.append(selected_excel)

        audit_excel = excel.copy()
        audit_excel["_selected"] = ~suppress
        audit_excel["_reason"] = reasons
        audit_frames.append(audit_excel)

    if not ujb.empty:
        ujb_categories = ujb["equipment_category"].astype(str).str.upper()
        reasons = pd.Series("UJB_ADDITIONAL_CATEGORY", index=ujb.index, dtype="object")
        reasons.loc[ujb_categories.isin(preferred)] = "UJB_PREFERRED_ON_COVERED_DATE"
        ujb["source_selection_reason"] = reasons
        selected_frames.append(ujb)

        audit_ujb = ujb.copy()
        audit_ujb["_selected"] = True
        audit_ujb["_reason"] = reasons
        audit_frames.append(audit_ujb)

    if selected_frames:
        selected = pd.concat(selected_frames, ignore_index=True, sort=False)
        sort_cols = [c for c in ["date", "equipment_category", "equipment_id", "event_time"] if c in selected.columns]
        if sort_cols:
            selected = selected.sort_values(sort_cols, na_position="last").reset_index(drop=True)
    else:
        selected = pd.DataFrame()

    audit = _build_audit(audit_frames)
    return SourceReconciliationResult(
        selected_df=selected,
        audit_df=audit,
        ujb_coverage_dates=coverage,
    )
