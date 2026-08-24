"""Production runner for UJB scraping with robust date-filter diagnostics.

This wrapper deliberately reuses the stable login, pagination, transform, and
history code from ``ujb_dashboard_scraper.py`` while using the newer date-filter
logic from ``src.ujb_date_filter``. It can be folded back into the main scraper
once the vendor UI behavior is confirmed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

import ujb_dashboard_scraper as base
from src.ujb_date_filter import apply_date_range_robust, collect_filter_diagnostics
from src.ujb_history import write_snapshot_and_history


def _coverage(raw_df: pd.DataFrame, date_from: str, date_to: str) -> dict:
    requested_start = pd.Timestamp(date_from).normalize()
    requested_end = pd.Timestamp(date_to).normalize()

    if raw_df.empty or "Date" not in raw_df.columns:
        return {
            "requested_start": str(requested_start.date()),
            "requested_end": str(requested_end.date()),
            "observed_start": None,
            "observed_end": None,
            "observed_days": 0,
            "requested_days": int((requested_end - requested_start).days + 1),
            "reaches_requested_start": False,
        }

    parsed = pd.to_datetime(raw_df["Date"], errors="coerce").dropna().dt.normalize()
    if parsed.empty:
        observed_start = observed_end = None
        observed_days = 0
    else:
        observed_start = parsed.min()
        observed_end = parsed.max()
        observed_days = int(parsed.nunique())

    return {
        "requested_start": str(requested_start.date()),
        "requested_end": str(requested_end.date()),
        "observed_start": str(observed_start.date()) if observed_start is not None else None,
        "observed_end": str(observed_end.date()) if observed_end is not None else None,
        "observed_days": observed_days,
        "requested_days": int((requested_end - requested_start).days + 1),
        "reaches_requested_start": bool(
            observed_start is not None and observed_start <= requested_start
        ),
    }


def run(date_from: str | None = None, date_to: str | None = None, headless: bool = True) -> tuple[pd.DataFrame, dict]:
    if not base.USERNAME or not base.PASSWORD:
        raise RuntimeError("UJB_USERNAME / UJB_PASSWORD belum di-set di environment variable.")

    default_from, default_to = base._default_date_window()
    date_from = date_from or default_from
    date_to = date_to or default_to

    if pd.Timestamp(date_from) > pd.Timestamp(date_to):
        raise ValueError("date_from tidak boleh lebih besar dari date_to.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            base.login(page, base.USERNAME, base.PASSWORD)
            base.logger.info("Membuka halaman report: %s", base.REPORT_URL)
            page.goto(base.REPORT_URL, wait_until="networkidle")

            before = collect_filter_diagnostics(page)
            strategy = apply_date_range_robust(page, date_from, date_to)
            base.logger.info(
                "Rentang tanggal diminta: %s s.d. %s (robust_strategy=%s)",
                date_from,
                date_to,
                strategy,
            )

            base.set_entries_per_page_max(page)
            raw_df = base.scrape_report_table(page)
            coverage = _coverage(raw_df, date_from, date_to)
            base.logger.info("Coverage UJB sesudah filter: %s", coverage)

            if not coverage["reaches_requested_start"]:
                base.logger.warning(
                    "Hasil belum mencapai requested start %s; diagnostics akan disimpan.",
                    date_from,
                )

            # Never silently keep rows outside the requested window.
            raw_df = base._audit_and_filter_window(raw_df, date_from, date_to)
            transformed = base.transform_to_dashboard_schema(raw_df)

            after = collect_filter_diagnostics(page)
            diagnostics = {
                "strategy": strategy,
                "coverage": coverage,
                "before": before,
                "after": after,
            }
            return transformed, diagnostics
        finally:
            browser.close()


def main() -> None:
    result_df, diagnostics = run(headless=True)
    print(result_df.head(20))
    print(f"\nTotal baris snapshot: {len(result_df)}")
    print("Date-filter diagnostics:")
    print(json.dumps({"strategy": diagnostics["strategy"], "coverage": diagnostics["coverage"]}, indent=2))

    output_dir = Path(
        os.environ.get("UJB_OUTPUT_DIR")
        or (Path(__file__).resolve().parent / "data" / "raw")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = write_snapshot_and_history(result_df, output_dir)
    diagnostics_path = output_dir / "ujb_filter_diagnostics.json"
    diagnostics_path.write_text(
        json.dumps(diagnostics, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Snapshot tersimpan: {stats['latest_path']} ({stats['latest_rows']} row)")
    print(
        f"History tersimpan: {stats['history_path']} "
        f"({stats['history_rows']} unique event; +{stats['new_unique_rows']} event baru)"
    )
    print(f"Diagnostics tersimpan: {diagnostics_path}")


if __name__ == "__main__":
    main()
