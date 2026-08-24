"""
ujb_dashboard_scraper.py
========================
Scraper browser-automation untuk report UJB JICT.

Catatan penting:
- Tidak mengandalkan API JSON tersembunyi; data dibaca dari tabel HTML.
- Pagination dibuat kompatibel dengan DataTables lama (<a> Next) maupun
  DataTables baru (<button> Next).
- Taxonomy unit dinormalisasi lewat src.ujb_unit_mapping.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from src.ujb_unit_mapping import parse_ujb_unit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ujb_scraper")

BASE_URL = "https://dashboard.ujbgroup.com"
REPORT_URL = f"{BASE_URL}/report/custom_jict"

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
            "Masih di halaman login setelah submit -- cek username/password, "
            "captcha, atau OTP."
        )
    logger.info("Login berhasil.")


def _parse_unit(unit: str) -> tuple[str, str]:
    """Compatibility wrapper untuk parser taxonomy UJB terpusat."""
    return parse_ujb_unit(unit)


def _numeric_select_options(select_locator) -> list[int]:
    """Ambil option integer dari satu <select>; kosong bila bukan page-length selector."""
    try:
        texts = select_locator.locator("option").all_text_contents()
    except Exception:
        return []
    return [int(t.strip()) for t in texts if t.strip().isdigit()]


def set_entries_per_page_max(page: Page) -> Optional[int]:
    """Set page length DataTables ke angka terbesar yang tersedia.

    Implementasi lama mencari satu ``select`` dengan regex pada seluruh text
    node. Itu rapuh. Sekarang semua select diperiksa dan kandidat dengan >=2
    option numerik diperlakukan sebagai page-length selector.
    """
    selects = page.locator("select")
    for i in range(selects.count()):
        candidate = selects.nth(i)
        options = _numeric_select_options(candidate)
        if len(options) < 2:
            continue

        max_entries = max(options)
        try:
            candidate.select_option(str(max_entries))
            # DataTables bisa client-side (tanpa network), jadi beri waktu DOM update.
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
    """Cari kontrol Next pada DataTables versi lama maupun baru.

    DataTables 1.x lazim memakai ``<a>Next</a>``, sedangkan DataTables 2.x
    lazim memakai ``<button>Next</button>``. Bug lama hanya mencari role link,
    sehingga scrape berhenti di page pertama (sering tepat 100 row).
    """
    name_re = re.compile(r"^(next|berikutnya|selanjutnya)\s*[›»]?$", re.I)

    button = page.get_by_role("button", name=name_re)
    if button.count() > 0:
        return button.first

    link = page.get_by_role("link", name=name_re)
    if link.count() > 0:
        return link.first

    # Fallback class DataTables umum. Tidak memakai text global agar tidak
    # salah mengambil kontrol lain di halaman.
    css = page.locator(
        ".dt-paging-button.next, .paginate_button.next, "
        "button.next, a.next"
    )
    if css.count() > 0:
        return css.first

    return None


def _control_is_disabled(control) -> bool:
    """Deteksi disabled pada elemen Next atau wrapper parent-nya."""
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
    """Signature row pertama untuk memastikan pagination benar-benar berpindah."""
    if body_rows.count() == 0:
        return ""
    try:
        return " | ".join(c.strip() for c in body_rows.nth(0).all_inner_texts())
    except Exception:
        return ""


def scrape_report_table(page: Page) -> pd.DataFrame:
    """Baca SEMUA halaman tabel report UJB.

    Safety:
    - mendukung Next link maupun button;
    - berhenti saat Next disabled;
    - memverifikasi row pertama berubah setelah klik agar tidak infinite loop;
    - guard maksimal 200 halaman.
    """
    all_rows: list[dict] = []
    headers: Optional[list[str]] = None
    page_num = 1

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
                    "Jumlah kolom tabel (%s) != KNOWN_REPORT_HEADERS (%s). "
                    "Pakai Col_0... sementara.",
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
            page_num,
            row_count,
            len(all_rows),
        )

        next_btn = _next_control(page)
        if next_btn is None:
            logger.info("Kontrol Next tidak ditemukan -- pagination selesai atau markup tidak dikenal.")
            break
        if _control_is_disabled(next_btn):
            logger.info("Kontrol Next disabled -- sudah halaman terakhir.")
            break

        next_btn.click()

        # DataTables biasanya memperbarui DOM tanpa full page navigation.
        # Tunggu sampai row pertama berubah; networkidle saja tidak cukup.
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
                break

        page_num += 1
        if page_num > 200:
            logger.warning("Berhenti paksa di 200 halaman -- cek pagination UJB.")
            break

    result = pd.DataFrame(all_rows)

    # Guard diagnostik: tepat sama dengan page length maksimum adalah pola yang
    # patut dicurigai bila Next gagal ditemukan. Tidak mengubah data, hanya log.
    if len(result) == 100:
        logger.warning(
            "Hasil tepat 100 row. Jika report seharusnya lebih banyak, cek log pagination/markup Next."
        )

    return result


def transform_to_dashboard_schema(
    raw_df: pd.DataFrame,
    source_label: str = "ujb_dashboard_scrape",
) -> pd.DataFrame:
    """Ubah tabel vendor ke skema long-form Fuel Intelligence."""
    output_columns = [
        "date", "year", "month", "equipment_category", "equipment_id",
        "fuel_liter", "status_text", "source_sheet", "source_file",
        "source_row", "data_status", "issue_code",
    ]
    if raw_df.empty:
        return pd.DataFrame(columns=output_columns)

    df = raw_df.copy()
    df.columns = [c.strip() for c in df.columns]

    date_col = next((c for c in df.columns if c.lower() == "date"), None)
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

    parsed = df[unit_col].astype(str).apply(_parse_unit)
    df["equipment_category"] = parsed.apply(lambda t: t[0])
    df["equipment_id"] = parsed.apply(lambda t: t[1])

    df["date"] = pd.to_datetime(df[date_col], errors="coerce")
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


def run_scrape(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    headless: bool = True,
) -> pd.DataFrame:
    """Login, pilih rentang tanggal, scrape seluruh tabel, lalu transform."""
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "UJB_USERNAME / UJB_PASSWORD belum di-set di environment variable."
        )

    date_from = date_from or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = date_to or datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            login(page, USERNAME, PASSWORD)
            logger.info("Membuka halaman report: %s", REPORT_URL)
            page.goto(REPORT_URL, wait_until="networkidle")

            try:
                date_field = page.get_by_label(re.compile("date range", re.I)).first
                date_field.fill(f"{date_from} to {date_to}")
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeoutError:
                    pass
                logger.info("Rentang tanggal diminta: %s s.d. %s", date_from, date_to)
            except Exception as exc:
                logger.info("Field date range tidak ditemukan/tidak diisi: %s", exc)

            set_entries_per_page_max(page)
            raw_df = scrape_report_table(page)
            logger.info("Total baris ter-scrape: %s", len(raw_df))

            return transform_to_dashboard_schema(raw_df)
        finally:
            browser.close()


if __name__ == "__main__":
    result_df = run_scrape(headless=True)
    print(result_df.head(20))
    print(f"\nTotal baris: {len(result_df)}")

    output_dir = os.environ.get("UJB_OUTPUT_DIR")
    if output_dir:
        out_path = os.path.join(output_dir, "ujb_scraped_latest.csv")
    else:
        out_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "raw",
            "ujb_scraped_latest.csv",
        )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    result_df.to_csv(out_path, index=False)
    print(f"Tersimpan ke {out_path}")
