"""Authoritative daily coverage manifest for UJB scrape runs.

Presence of one transaction is not proof that a vendor report was extracted
completely.  Source precedence therefore consumes an explicit daily manifest
whose status is one of COMPLETE, PARTIAL, FAILED, or UNKNOWN.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


COVERAGE_STATUSES = frozenset({"COMPLETE", "PARTIAL", "FAILED", "UNKNOWN"})
MANIFEST_FILENAME = "ujb_coverage_manifest.csv"
DIAGNOSTICS_FILENAME = "ujb_filter_diagnostics.json"
MANIFEST_COLUMNS = [
    "date",
    "coverage_status",
    "requested_start",
    "requested_end",
    "observed_transactions",
    "evidence",
]


def _normalize_status(value: object) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    return status if status in COVERAGE_STATUSES else "UNKNOWN"


def _classify_run(diagnostics: dict[str, Any]) -> tuple[str, str]:
    explicit = diagnostics.get("run_status")
    if explicit is not None:
        status = _normalize_status(explicit)
        return status, f"explicit_run_status:{status}"

    pagination = diagnostics.get("pagination")
    strategy = str(diagnostics.get("strategy") or "").strip()
    filter_applied = bool(strategy and strategy.lower() != "not_found")
    if isinstance(pagination, dict):
        complete = pagination.get("complete")
        reason = str(pagination.get("termination_reason") or "unknown")
        if complete is True and filter_applied:
            return "COMPLETE", f"pagination_complete:{reason}"
        if complete is True:
            return "UNKNOWN", "date_filter_not_verified"
        if complete is False:
            if reason in {"page_cap_reached", "content_unchanged_after_next", "table_read_error"}:
                return "FAILED", f"pagination_failed:{reason}"
            return "PARTIAL", f"pagination_partial:{reason}"
        return "UNKNOWN", f"pagination_unknown:{reason}"

    # Backward-compatible migration for diagnostics created before pagination
    # evidence was persisted.  This is intentionally strict: every requested
    # day must have been observed and both boundaries must match.
    coverage = diagnostics.get("coverage") or {}
    requested_start = pd.to_datetime(coverage.get("requested_start"), errors="coerce")
    requested_end = pd.to_datetime(coverage.get("requested_end"), errors="coerce")
    observed_start = pd.to_datetime(coverage.get("observed_start"), errors="coerce")
    observed_end = pd.to_datetime(coverage.get("observed_end"), errors="coerce")
    observed_days = int(coverage.get("observed_days") or 0)
    requested_days = int(coverage.get("requested_days") or 0)
    legacy_complete = (
        filter_applied
        and pd.notna(requested_start)
        and pd.notna(requested_end)
        and observed_start == requested_start
        and observed_end == requested_end
        and observed_days == requested_days
        and requested_days > 0
    )
    if legacy_complete:
        return "COMPLETE", "legacy_full_window_observed"
    return "UNKNOWN", "legacy_diagnostics_insufficient"


def build_coverage_manifest_from_diagnostics(
    diagnostics: dict[str, Any],
    ujb_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Expand one scrape-run diagnostic record to one row per requested day."""
    coverage = diagnostics.get("coverage") or {}
    start = pd.to_datetime(coverage.get("requested_start"), errors="coerce")
    end = pd.to_datetime(coverage.get("requested_end"), errors="coerce")
    if pd.isna(start) or pd.isna(end) or start > end:
        return pd.DataFrame(columns=MANIFEST_COLUMNS)

    status, evidence = _classify_run(diagnostics)
    dates = pd.date_range(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize(), freq="D")
    transaction_counts = pd.Series(0, index=dates, dtype="int64")
    if ujb_df is not None and not ujb_df.empty and "date" in ujb_df.columns:
        observed = pd.to_datetime(ujb_df["date"], errors="coerce").dt.normalize()
        transaction_counts = observed.value_counts().reindex(dates, fill_value=0).astype(int)

    return pd.DataFrame({
        "date": dates,
        "coverage_status": status,
        "requested_start": pd.Timestamp(start).normalize(),
        "requested_end": pd.Timestamp(end).normalize(),
        "observed_transactions": transaction_counts.values,
        "evidence": evidence,
    })[MANIFEST_COLUMNS]


def merge_coverage_manifest(existing: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """Merge daily evidence idempotently; the latest run replaces overlapping dates."""
    frames = [df.copy() for df in (existing, latest) if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    merged = pd.concat(frames, ignore_index=True, sort=False)
    for column in MANIFEST_COLUMNS:
        if column not in merged.columns:
            merged[column] = None
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce").dt.normalize()
    merged["coverage_status"] = merged["coverage_status"].map(_normalize_status)
    merged = merged.dropna(subset=["date"]).drop_duplicates(subset=["date"], keep="last")
    return merged[MANIFEST_COLUMNS].sort_values("date").reset_index(drop=True)


def load_ujb_coverage_manifest(raw_dir: str | Path) -> pd.DataFrame:
    """Load persistent manifest, with a strict legacy-diagnostics fallback."""
    raw_dir = Path(raw_dir)
    manifest_path = raw_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        manifest = pd.read_csv(manifest_path)
        return merge_coverage_manifest(pd.DataFrame(), manifest)

    diagnostics_path = raw_dir / DIAGNOSTICS_FILENAME
    if not diagnostics_path.exists():
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    try:
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    return build_coverage_manifest_from_diagnostics(diagnostics)


def write_coverage_manifest(
    diagnostics: dict[str, Any],
    ujb_df: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """Persist the latest run's per-day evidence without losing older dates."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / MANIFEST_FILENAME
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    latest = build_coverage_manifest_from_diagnostics(diagnostics, ujb_df=ujb_df)
    merged = merge_coverage_manifest(existing, latest)
    merged.to_csv(path, index=False)
    return path
