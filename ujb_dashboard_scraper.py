"""
ujb_dashboard_scraper.py
========================
Scraper browser-automation untuk report UJB JICT.

Catatan penting:
- Tidak mengandalkan API JSON tersembunyi; data dibaca dari tabel HTML.
- Pagination kompatibel dengan DataTables lama (<a> Next) dan baru (<button> Next).
- Rentang tanggal dicari secara defensif pada beberapa pola form umum.
- Default window memakai Asia/Jakarta, bukan timezone runner GitHub Actions.
- Taxonomy unit dinormalisasi lewat src.ujb_unit_mapping.
- Output terdiri dari snapshot terbaru + history persisten yang didedup per event.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from src.ujb_history import make_source_event_key, write_snapshot_and_history
from src.ujb_unit_mapping import parse_ujb_unit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ujb_scraper")

BASE_URL = "https://dashboard.ujbgroup.com"
REPORT_URL = f"{BASE_URL}/report/custom_jict"
JICT_TIMEZONE = ZoneInfo("Asia/Jakarta")

USERNAME = os.environ.get("UJB_USERNAME")
PASSWORD = os.environ.get("UJB_PASSWORD")

KNOWN_REPORT_HEADERS = [
    "No", "Site", "Product", "Date", "Time", "Unit", "Status",
    "Volume (L)", "Kilometer", "Stock (L)",
]


def login(page: Page, username: str, password: str) -> None:
    """Login ke dashboard UJB."""
    logger.info("Membuka halaman login...")
    page.goto(BASE_URL, wait_until="networkidle")

    page.get_by_label(re.compile("username", re.I)).or_(
        page.get_by_placeholder(re.compile("username", re.I))
    ).first.fill(username)

    page.get_by_label(re.compile("password", re.I)).or_(
        page.get_by_placeholder(re.compile("password", re.I))
    ).first.fill(password)

    page.get_by_role(
        "button", name=re.compile("log ?in|masuk|sign ?in", re.I)
    ).first.click()
    page.wait_for_load_state("networkidle")

    if "login" in page.url.lower():
        raise RuntimeError(
            "Masih di halaman login setelah submit -- cek username/password, captcha, atau OTP."
        )
    logger.info("Login berhasil.")


def _parse_unit(unit: str) -> tuple[str, str]:
    return parse_ujb_unit(unit)


def _wait_after_filter(page: Page) -> None:
    page.wait_for_timeout(700)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass


def _input_metadata(locator) -> str:
    parts = []
    for attr in ("type", "name", "id", "placeholder", "aria-label"):
        try:
            value = locator.get_attribute(attr)
        except Exception:
            value = None
        if value:
            parts.append(f"{attr}={value}")
    return " ".join(parts)


def _visible_inputs(page: Page) -> list:
    inputs = page.locator("input")
    out = []
    for i in range(inputs.count()):
        loc = inputs.nth(i)
        try:
            if loc.is_visible() and (loc.get_attribute("type") or "text").lower() not in {
                "hidden", "password", "submit", "button", "checkbox", "radio"
            }:
                out.append(loc)
        except Exception:
            continue
    return out


def _trigger_filter(page: Page, anchor=None) -> None:
    """Submit/filter dengan tombol yang masuk akal, fallback Enter pada input."""
    action_re = re.compile(r"filter|apply|search|cari|tampil|proses|submit", re.I)
    buttons = page.get_by_role("button", name=action_re)
    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            if btn.is_visible() and not btn.is_disabled():
                btn.click()
                _wait_after_filter(page)
                return
        except Exception:
            continue

    if anchor is not None:
        try:
            anchor.press("Enter")
        except Exception:
            pass
    _wait_after_filter(page)


def _format_range_like_current(current_value: str, date_from: str, date_to: str) -> str:
    """Pertahankan format text daterangepicker bila bisa diinfer dari value sekarang."""
    current = (current_value or "").strip()
    start = pd.Timestamp(date_from)
    end = pd.Timestamp(date_to)

    if re.search(r"\d{2}/\d{2}/\d{4}", current):
        left = start.strftime("%d/%m/%Y")
        right = end.strftime("%d/%m/%Y")
    else:
        left = start.strftime("%Y-%m-%d")
        right = end.strftime("%Y-%m-%d")

    separator = " to " if re.search(r"\bto\b", current, re.I) else " - "
    return f"{left}{separator}{right}"


def apply_date_range(page: Page, date_from: str, date_to: str) -> str:
    """Coba terapkan date range pada beberapa pola form umum.

    Return nama strategi yang berhasil dipakai. Jika tidak ada input yang cocok,
    return ``not_found`` dan log metadata input untuk diagnosis run berikutnya.
    """
    visible = _visible_inputs(page)

    # 1) HTML-native date inputs: paling deterministik.
    date_inputs = [loc for loc in visible if (loc.get_attribute("type") or "").lower() == "date"]
    if len(date_inputs) >= 2:
        date_inputs[0].fill(date_from)
        date_inputs[1].fill(date_to)
        _trigger_filter(page, date_inputs[1])
        logger.info("Date filter diterapkan via dua input type=date: %s s.d. %s", date_from, date_to)
        return "two_html_date_inputs"

    # 2) Dua input text yang metadata-nya menunjukkan start/from dan end/to.
    start_re = re.compile(r"(^|[_\- ])(from|start|awal)([_\- ]|$)|date.?from|start.?date|tanggal.?awal", re.I)
    end_re = re.compile(r"(^|[_\- ])(to|end|akhir)([_\- ]|$)|date.?to|end.?date|tanggal.?akhir", re.I)
    start_input = None
    end_input = None
    for loc in visible:
        meta = _input_metadata(loc)
        if start_input is None and start_re.search(meta):
            start_input = loc
        if end_input is None and end_re.search(meta):
            end_input = loc

    if start_input is not None and end_input is not None:
        start_input.fill(date_from)
        end_input.fill(date_to)
        _trigger_filter(page, end_input)
        logger.info("Date filter diterapkan via pasangan start/end input: %s s.d. %s", date_from, date_to)
        return "named_start_end_inputs"

    # 3) Satu text input daterangepicker.
    range_re = re.compile(r"date|tanggal|range|period|periode", re.I)
    for loc in visible:
        meta = _input_metadata(loc)
        if not range_re.search(meta):
            continue
        try:
            current = loc.input_value()
            requested = _format_range_like_current(current, date_from, date_to)
            loc.fill(requested)
            _trigger_filter(page, loc)
            logger.info(
                "Date filter diterapkan via single range input (%s): %s",
                meta or "metadata kosong", requested,
            )
            return "single_range_input"
        except Exception as exc:
            logger.debug("Kandidat date range gagal: %s", exc)

    diagnostics = [_input_metadata(loc) for loc in visible]
    logger.warning(
        "Input date range tidak ditemukan. Visible input metadata: %s",
        diagnostics[:12],
    )
    return "not_found"


def _numeric_select_options(select_locator) -> list[int]:
    try:
        texts = select_locator.locator("option").all_text_contents()
    except Exception:
        return []
    return [int(t.strip()) for t in texts if t.strip().isdigit()]


def set_entries_per_page_max(page: Page) -> Optional[int]:
    """Set page length DataTables ke angka terbesar yang tersedia."""
    selects = page.locator("select")
    for i in range(selects.count()):
        candidate = selects.nth(i)
        options = _numeric_select_options(candidate)
        if len(options) < 2:
            continue

        max_entries = max(options)
        try:
            candidate.select_option(str(max_entries))
            page.wait_for_timeout(300)
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except PlaywrightTimeoutError:
                pass
            logger.info("Show entries diset ke %s.", max_entries)
            return max_entries
        except Exception as exc:
            logger.warning("Kandidat page-length select gagal dipakai: %s", exc)

    logger.warning("Dropdown 'Show entries' numerik tidak ditemukan; pakai page length default.")
    return None


def _next_control(page: Page):
    name_re = re.compile(r"^(next|berikutnya|selanjutnya)\s*[›»]?$", re.I)

    button = page.get_by_role("button", name=name_re)
    if button.count() > 0:
        return button.first

    link = page.get_by_role("link", name=name_re)
    if link.count() > 0:
        return link.first

    css = page.locator(
        ".dt-paging-button.next, .paginate_button.next, button.next, a.next"
    )
    if css.count() > 0:
        return css.first

    return None


def _control_is_disabled(control) -> bool:
    if control is None:
        return True

    aria_disabled = (control.get_attribute("aria-disabled") or "").lower()
    disabled_attr = control.get_attribute("disabled")
    classes = (control.get_attribute("class") or "").lower()

    parent = control.locator("xpath=..")
    parent_classes = (parent.get_attribute("class") or "").lower()
    parent_aria = (parent.get_attribute("aria-disabled") or "").lower()

    return (
        aria_disabled == "true"
        or parent_aria == "true"
        or disabled_attr is not None
        or "disabled" in classes
        or "disabled" in parent_classes
    )


def _row_signature(body_rows) -> str:
    if body_rows.count() == 0:
        return ""
    try:
        return " | ".join(c.strip() for c in body_rows.nth(0).all_inner_texts())
    except Exception:
        return ""


def scrape_report_table(page: Page, return_diagnostics: bool = False):
    """Baca semua halaman tabel dan optionally return pagination evidence."""
    all_rows: list[dict] = []
    headers: Optional[list[str]] = None
    page_num = 1
    pagination = {
        "complete": None,
        "termination_reason": "unknown",
        "pages_read": 0,
    }

    while True:
        page.wait_for_selector("table tbody tr")
        body_rows = page.locator("table tbody tr")
        row_count = body_rows.count()
        first_signature = _row_signature(body_rows)

        if headers is None and row_count > 0:
            first_row_cell_count = body_rows.nth(0).locator("td").count()
            if first_row_cell_count == len(KNOWN_REPORT_HEADERS):
                headers = KNOWN_REPORT_HEADERS
            else:
                headers = [f"Col_{i}" for i in range(first_row_cell_count)]
                logger.warning(
                    "Jumlah kolom tabel (%s) != KNOWN_REPORT_HEADERS (%s). Pakai Col_0... sementara.",
                    first_row_cell_count,
                    len(KNOWN_REPORT_HEADERS),
                )

        if headers is None:
            logger.warning("Tabel tidak mempunyai row yang bisa dibaca.")
            break

        for i in range(row_count):
            cells = [c.strip() for c in body_rows.nth(i).locator("td").all_inner_texts()]
            if len(cells) == len(headers):
                all_rows.append(dict(zip(headers, cells)))

        logger.info(
            "Halaman %s: %s baris terkumpul (total: %s).",
            page_num, row_count, len(all_rows),
        )
        pagination["pages_read"] = page_num

        next_btn = _next_control(page)
        if next_btn is None:
            logger.info("Kontrol Next tidak ditemukan -- pagination selesai atau markup tidak dikenal.")
            pagination["termination_reason"] = "next_control_not_found"
            break
        if _control_is_disabled(next_btn):
            logger.info("Kontrol Next disabled -- sudah halaman terakhir.")
            pagination["complete"] = True
            pagination["termination_reason"] = "next_disabled_last_page"
            break

        next_btn.click()
        try:
            page.wait_for_function(
                """prev => {
                    const row = document.querySelector('table tbody tr');
                    return row && row.innerText.trim() !== prev;
                }""",
                arg=first_signature.replace(" | ", "\t").strip(),
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            page.wait_for_timeout(500)
            new_signature = _row_signature(page.locator("table tbody tr"))
            if new_signature == first_signature:
                logger.warning(
                    "Next terlihat aktif tetapi isi tabel tidak berubah; berhenti untuk mencegah loop."
                )
                pagination["complete"] = False
                pagination["termination_reason"] = "content_unchanged_after_next"
                break

        page_num += 1
        if page_num > 200:
            logger.warning("Berhenti paksa di 200 halaman -- cek pagination UJB.")
            pagination["complete"] = False
            pagination["termination_reason"] = "page_cap_reached"
            break

    result = pd.DataFrame(all_rows)
    if len(result) == 100:
        logger.warning(
            "Hasil tepat 100 row. Jika report seharusnya lebih banyak, cek log pagination/markup Next."
        )
    if return_diagnostics:
        return result, pagination
    return result


def _audit_and_filter_window(raw_df: pd.DataFrame, date_from: str, date_to: str) -> pd.DataFrame:
    """Log coverage tanggal dan buang event di luar window yang diminta."""
    if raw_df.empty or "Date" not in raw_df.columns:
        return raw_df

    parsed = pd.to_datetime(raw_df["Date"], errors="coerce").dt.normalize()
    requested_start = pd.Timestamp(date_from)
    requested_end = pd.Timestamp(date_to)
    valid_dates = parsed.dropna()

    if not valid_dates.empty:
        observed_start = valid_dates.min()
        observed_end = valid_dates.max()
        logger.info(
            "Coverage tanggal hasil UJB: %s s.d. %s (requested %s s.d. %s).",
            observed_start.date(), observed_end.date(), requested_start.date(), requested_end.date(),
        )
        if observed_start > requested_start:
            logger.warning(
                "Tanggal paling awal hasil (%s) lebih baru dari requested start (%s). "
                "Bisa berarti tidak ada transaksi pada hari awal, atau filter UI belum diterapkan penuh.",
                observed_start.date(), requested_start.date(),
            )
        if observed_end < requested_end:
            logger.warning(
                "Tanggal paling akhir hasil (%s) lebih lama dari requested end (%s).",
                observed_end.date(), requested_end.date(),
            )

    in_window = parsed.between(requested_start, requested_end, inclusive="both")
    outside_count = int((~in_window & parsed.notna()).sum())
    if outside_count:
        logger.warning("Membuang %s row di luar requested date window.", outside_count)

    return raw_df.loc[in_window | parsed.isna()].reset_index(drop=True)


def transform_to_dashboard_schema(
    raw_df: pd.DataFrame,
    source_label: str = "ujb_dashboard_scrape",
) -> pd.DataFrame:
    """Ubah tabel vendor ke skema long-form Fuel Intelligence."""
    output_columns = [
        "date", "event_time", "year", "month", "equipment_category", "equipment_id",
        "fuel_liter", "status_text", "source_sheet", "source_file", "source_row",
        "source_event_key", "data_status", "issue_code",
    ]
    if raw_df.empty:
        return pd.DataFrame(columns=output_columns)

    df = raw_df.copy()
    df.columns = [c.strip() for c in df.columns]

    date_col = next((c for c in df.columns if c.lower() == "date"), None)
    time_col = next((c for c in df.columns if c.lower() == "time"), None)
    unit_col = next((c for c in df.columns if c.lower() == "unit"), None)
    volume_col = next((c for c in df.columns if "volume" in c.lower()), None)
    status_col = next((c for c in df.columns if c.lower() == "status"), None)

    missing_semantic = [
        label for label, col in {
            "Date": date_col,
            "Unit": unit_col,
            "Volume": volume_col,
        }.items() if col is None
    ]
    if missing_semantic:
        raise ValueError(f"Kolom report UJB tidak dikenali: {missing_semantic}")

    # Event key dibuat dari row vendor SEBELUM taxonomy/formatting diubah.
    df["source_event_key"] = raw_df.apply(
        lambda row: make_source_event_key(row.to_dict()), axis=1
    )

    parsed = df[unit_col].astype(str).apply(_parse_unit)
    df["equipment_category"] = parsed.apply(lambda t: t[0])
    df["equipment_id"] = parsed.apply(lambda t: t[1])

    df["date"] = pd.to_datetime(df[date_col], errors="coerce")
    df["event_time"] = df[time_col].astype(str).str.strip() if time_col else ""
    df["fuel_liter"] = pd.to_numeric(
        df[volume_col].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )
    df["status_text"] = df[status_col] if status_col else ""
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["source_sheet"] = "N/A"
    df["source_file"] = source_label
    df["source_row"] = range(1, len(df) + 1)
    df["data_status"] = df["fuel_liter"].apply(
        lambda v: "VALID" if pd.notna(v) else "INVALID"
    )
    df["issue_code"] = df["fuel_liter"].apply(
        lambda v: "" if pd.notna(v) else "MISSING_VOLUME"
    )
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    return df[output_columns]


def _default_date_window() -> tuple[str, str]:
    today = datetime.now(JICT_TIMEZONE).date()
    try:
        lookback_days = max(1, int(os.environ.get("UJB_LOOKBACK_DAYS", "7")))
    except ValueError:
        lookback_days = 7
    start = today - timedelta(days=lookback_days - 1)
    return start.isoformat(), today.isoformat()


def run_scrape(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    headless: bool = True,
) -> pd.DataFrame:
    """Login, terapkan date range, scrape semua halaman, lalu transform."""
    if not USERNAME or not PASSWORD:
        raise RuntimeError("UJB_USERNAME / UJB_PASSWORD belum di-set di environment variable.")

    default_from, default_to = _default_date_window()
    date_from = date_from or default_from
    date_to = date_to or default_to

    start_ts = pd.Timestamp(date_from)
    end_ts = pd.Timestamp(date_to)
    if start_ts > end_ts:
        raise ValueError("date_from tidak boleh lebih besar dari date_to.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            login(page, USERNAME, PASSWORD)
            logger.info("Membuka halaman report: %s", REPORT_URL)
            page.goto(REPORT_URL, wait_until="networkidle")

            strategy = apply_date_range(page, date_from, date_to)
            logger.info(
                "Rentang tanggal diminta: %s s.d. %s (strategy=%s)",
                date_from, date_to, strategy,
            )

            set_entries_per_page_max(page)
            raw_df = scrape_report_table(page)
            raw_df = _audit_and_filter_window(raw_df, date_from, date_to)
            logger.info("Total baris ter-scrape dalam requested window: %s", len(raw_df))

            return transform_to_dashboard_schema(raw_df)
        finally:
            browser.close()


if __name__ == "__main__":
    result_df = run_scrape(headless=True)
    print(result_df.head(20))
    print(f"\nTotal baris snapshot: {len(result_df)}")

    output_dir = os.environ.get("UJB_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "raw"
        )

    stats = write_snapshot_and_history(result_df, output_dir)
    print(f"Snapshot tersimpan: {stats['latest_path']} ({stats['latest_rows']} row)")
    print(
        f"History tersimpan: {stats['history_path']} "
        f"({stats['history_rows']} unique event; +{stats['new_unique_rows']} event baru)"
    )
