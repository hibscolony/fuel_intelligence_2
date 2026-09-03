"""Production runner for UJB scraping with robust date-filter diagnostics.

This wrapper deliberately reuses the stable pagination, transform, and history
code from ``ujb_dashboard_scraper.py`` while using resilient navigation and the
newer date-filter logic from ``src.ujb_date_filter``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

import ujb_dashboard_scraper as base
from src.ujb_date_filter import apply_date_range_robust, collect_filter_diagnostics
from src.ujb_history import write_snapshot_and_history
from src.ujb_coverage import write_coverage_manifest


def _goto_resilient(page, url: str, label: str, attempts: int = 3) -> None:
    """Navigate without requiring permanent network-idle state.

    Dashboard/vendor pages can keep analytics/websocket requests alive, making
    ``wait_until='networkidle'`` flaky on GitHub-hosted runners. A successful
    DOMContentLoaded is sufficient; network-idle is only a best-effort wait.
    """
    last_error: Exception | None = None
    page.set_default_navigation_timeout(60_000)

    for attempt in range(1, attempts + 1):
        try:
            base.logger.info(
                "Membuka %s (attempt %s/%s): %s", label, attempt, attempts, url
            )
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except PlaywrightTimeoutError:
                base.logger.info(
                    "%s belum network-idle setelah 8 detik; lanjut karena DOM sudah termuat.",
                    label,
                )
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
            base.logger.warning(
                "Timeout membuka %s pada attempt %s/%s: %s",
                label,
                attempt,
                attempts,
                exc,
            )
            if attempt < attempts:
                page.wait_for_timeout(2_000 * attempt)

    raise RuntimeError(
        f"Gagal membuka {label} setelah {attempts} percobaan: {url}"
    ) from last_error


def _login_resilient(page, username: str, password: str) -> None:
    """Login UJB without depending on network-idle as a hard condition."""
    _goto_resilient(page, base.BASE_URL, "halaman login")

    username_input = page.get_by_label(re.compile("username", re.I)).or_(
        page.get_by_placeholder(re.compile("username", re.I))
    ).first
    password_input = page.get_by_label(re.compile("password", re.I)).or_(
        page.get_by_placeholder(re.compile("password", re.I))
    ).first

    username_input.wait_for(state="visible", timeout=30_000)
    username_input.fill(username)
    password_input.fill(password)

    page.get_by_role(
        "button", name=re.compile("log ?in|masuk|sign ?in", re.I)
    ).first.click()

    # Tunggu perubahan halaman/form login, bukan seluruh jaringan berhenti.
    try:
        page.wait_for_url(re.compile(r"^(?!.*login).*$", re.I), timeout=30_000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(2_000)

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PlaywrightTimeoutError:
        pass

    if "login" in page.url.lower():
        raise RuntimeError(
            "Masih di halaman login setelah submit -- cek username/password, captcha, atau OTP."
        )
    base.logger.info("Login berhasil. URL saat ini: %s", page.url)


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
            "reaches_requested_end": False,
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
        "reaches_requested_end": bool(
            observed_end is not None and observed_end >= requested_end
        ),
    }


def run(
    date_from: str | None = None,
    date_to: str | None = None,
    headless: bool = True,
) -> tuple[pd.DataFrame, dict]:
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
            _login_resilient(page, base.USERNAME, base.PASSWORD)
            _goto_resilient(page, base.REPORT_URL, "halaman report UJB")

            before = collect_filter_diagnostics(page)
            strategy = apply_date_range_robust(page, date_from, date_to)
            base.logger.info(
                "Rentang tanggal diminta: %s s.d. %s (robust_strategy=%s)",
                date_from,
                date_to,
                strategy,
            )

            base.set_entries_per_page_max(page)
            raw_df, pagination = base.scrape_report_table(page, return_diagnostics=True)
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
                "pagination": pagination,
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
    print(
        json.dumps(
            {"strategy": diagnostics["strategy"], "coverage": diagnostics["coverage"]},
            indent=2,
        )
    )

    output_dir = Path(
        os.environ.get("UJB_OUTPUT_DIR")
        or (Path(__file__).resolve().parent / "data" / "raw")
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = write_snapshot_and_history(result_df, output_dir)
    coverage_manifest_path = write_coverage_manifest(diagnostics, result_df, output_dir)
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
    print(f"Coverage manifest tersimpan: {coverage_manifest_path}")


if __name__ == "__main__":
    main()
