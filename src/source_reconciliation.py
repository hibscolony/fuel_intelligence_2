"""Source precedence and audit layer for Excel + UJB fuel records.

Hybrid mode must not simply concatenate both sources. UJB is the preferred
operational source for equipment that refuels through the dispenser, but only
on calendar dates for which UJB actually has source coverage. Excel remains
the source for non-UJB equipment and for periods before/gaps in UJB history.

Forklift needs one conservative exception: the current Excel parser groups the
legacy/support block as ``SUPPORT`` while UJB exposes ``FORKLIFT`` explicitly.
Until the Excel taxonomy is split safely, UJB forklift events are excluded from
the *hybrid total* on dates where Excel SUPPORT is present, preventing a hidden
double count. The UJB events remain available in UJB history for operational
analysis and are used when Excel SUPPORT is absent.

MODUL uses the inverse bridge: Excel remains authoritative on dates where an
Excel MODUL row exists. UJB MODUL (for example GENSET transactions) is retained
as fallback only when Excel MODUL is absent on that date.

Unknown/new UJB categories are quarantined from the hybrid total until their
source relationship is explicitly approved. They remain visible in the source
audit/history so a new vendor unit type can never inflate totals silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


UJB_PREFERRED_CATEGORIES = frozenset({
    "HEAD_TRUCK",
    "BUS",
    "ELF",
    "KEND_OPS",
})
FORKLIFT_CATEGORY = "FORKLIFT"
EXCEL_SUPPORT_CATEGORY = "SUPPORT"
MODUL_CATEGORY = "MODUL"


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

    audit_input["_fuel"] = pd.to_numeric(
        audit_input.get("fuel_liter"), errors="coerce"
    ).fillna(0.0)
    audit_input["_selected_liter"] = audit_input["_fuel"].where(
        audit_input["_selected"], 0.0
    )
    audit_input["_selected_int"] = audit_input["_selected"].astype(int)
    audit_input["date"] = pd.to_datetime(
        audit_input["date"], errors="coerce"
    ).dt.normalize()

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

    Direct precedence rules
    -----------------------
    - HEAD_TRUCK / BUS / ELF / KEND_OPS: UJB replaces Excel on dates actually
      covered by UJB history.
    - Non-UJB Excel categories: retained unless a dedicated bridge says
      otherwise.
    - UJB coverage gaps: Excel is retained; a min/max range alone is never
      treated as proof of coverage.

    Forklift bridge rule
    --------------------
    Excel still aggregates Forklift into SUPPORT. Therefore on a date where
    Excel SUPPORT exists, UJB FORKLIFT is *not* added to the hybrid total.
    If Excel SUPPORT is absent, UJB FORKLIFT is retained as fallback.

    MODUL bridge rule
    -----------------
    Excel MODUL remains authoritative when present on a date. UJB MODUL is
    retained only when no Excel MODUL row exists on that date.

    New-category guard
    ------------------
    UJB categories outside the approved direct set, FORKLIFT, and MODUL are
    suppressed from the hybrid total until explicitly classified. They remain
    in history and the audit table for review.
    """
    preferred = {str(c).strip().upper() for c in preferred_categories}
    excel = _annotate_source(excel_df, "EXCEL")
    ujb = _annotate_source(ujb_df, "UJB")
    coverage = _coverage_dates(ujb)
    coverage_set = set(coverage)

    excel_categories = (
        excel["equipment_category"].astype(str).str.upper()
        if not excel.empty else pd.Series(dtype="object")
    )
    excel_dates = (
        excel["date"].dt.normalize()
        if not excel.empty else pd.Series(dtype="datetime64[ns]")
    )
    excel_support_dates = set(
        excel_dates.loc[excel_categories.eq(EXCEL_SUPPORT_CATEGORY)].dropna().tolist()
    ) if not excel.empty else set()
    excel_modul_dates = set(
        excel_dates.loc[excel_categories.eq(MODUL_CATEGORY)].dropna().tolist()
    ) if not excel.empty else set()

    audit_frames: list[pd.DataFrame] = []
    selected_frames: list[pd.DataFrame] = []

    if not excel.empty:
        is_covered_date = excel_dates.isin(coverage_set)
        is_direct_ujb_category = excel_categories.isin(preferred)
        suppress_excel = is_covered_date & is_direct_ujb_category

        reasons = pd.Series(
            "EXCEL_NON_UJB_CATEGORY", index=excel.index, dtype="object"
        )
        reasons.loc[is_direct_ujb_category & ~is_covered_date] = (
            "EXCEL_OUTSIDE_UJB_COVERAGE"
        )
        reasons.loc[suppress_excel] = "EXCEL_SUPPRESSED_UJB_PRECEDENCE"
        reasons.loc[excel_categories.eq(EXCEL_SUPPORT_CATEGORY)] = (
            "EXCEL_SUPPORT_RETAINED_FORKLIFT_BRIDGE"
        )
        reasons.loc[excel_categories.eq(MODUL_CATEGORY)] = (
            "EXCEL_MODUL_AUTHORITATIVE_WHEN_PRESENT"
        )

        excel["source_selection_reason"] = reasons
        selected_frames.append(excel.loc[~suppress_excel].copy())

        audit_excel = excel.copy()
        audit_excel["_selected"] = ~suppress_excel
        audit_excel["_reason"] = reasons
        audit_frames.append(audit_excel)

    if not ujb.empty:
        ujb_categories = ujb["equipment_category"].astype(str).str.upper()
        ujb_dates = ujb["date"].dt.normalize()

        direct_mask = ujb_categories.isin(preferred)
        forklift_mask = ujb_categories.eq(FORKLIFT_CATEGORY)
        modul_mask = ujb_categories.eq(MODUL_CATEGORY)
        approved_mask = direct_mask | forklift_mask | modul_mask
        unknown_mask = ~approved_mask

        forklift_overlaps_excel_support = forklift_mask & ujb_dates.isin(
            excel_support_dates
        )
        modul_overlaps_excel = modul_mask & ujb_dates.isin(excel_modul_dates)
        suppress_ujb = (
            forklift_overlaps_excel_support
            | modul_overlaps_excel
            | unknown_mask
        )

        reasons = pd.Series(
            "UJB_UNAPPROVED_CATEGORY_SUPPRESSED", index=ujb.index, dtype="object"
        )
        reasons.loc[direct_mask] = "UJB_PREFERRED_ON_COVERED_DATE"
        reasons.loc[forklift_mask & ~forklift_overlaps_excel_support] = (
            "UJB_FORKLIFT_FALLBACK_NO_EXCEL_SUPPORT"
        )
        reasons.loc[forklift_overlaps_excel_support] = (
            "UJB_FORKLIFT_SUPPRESSED_EXCEL_SUPPORT_BRIDGE"
        )
        reasons.loc[modul_mask & ~modul_overlaps_excel] = (
            "UJB_MODUL_FALLBACK_NO_EXCEL_MODUL"
        )
        reasons.loc[modul_overlaps_excel] = (
            "UJB_MODUL_SUPPRESSED_EXCEL_MODUL_PRESENT"
        )

        ujb["source_selection_reason"] = reasons
        selected_frames.append(ujb.loc[~suppress_ujb].copy())

        audit_ujb = ujb.copy()
        audit_ujb["_selected"] = ~suppress_ujb
        audit_ujb["_reason"] = reasons
        audit_frames.append(audit_ujb)

    if selected_frames:
        selected = pd.concat(selected_frames, ignore_index=True, sort=False)
        sort_cols = [
            c for c in ["date", "equipment_category", "equipment_id", "event_time"]
            if c in selected.columns
        ]
        if sort_cols:
            selected = selected.sort_values(
                sort_cols, na_position="last"
            ).reset_index(drop=True)
    else:
        selected = pd.DataFrame()

    audit = _build_audit(audit_frames)
    return SourceReconciliationResult(
        selected_df=selected,
        audit_df=audit,
        ujb_coverage_dates=coverage,
    )
