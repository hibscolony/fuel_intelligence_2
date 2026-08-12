"""
ujb_dashboard_scraper.py
==========================
Otomasi login + ambil data dari dashboard.ujbgroup.com/report/custom_jict
lewat browser automation (Playwright), bukan API JSON -- karena reverse-
engineering di DevTools tidak menemukan endpoint JSON tersembunyi (kemungkinan
besar halaman ini full server-side render, DataTables cuma untuk UI).

Pendekatan ini meniru persis yang dilakukan manusia di browser: login, buka
halaman report, baca tabel HTML. Karena itu dia akan tetap jalan apa pun
mekanisme di baliknya (JSON, form POST biasa, dll) -- selama tampilan
tabelnya tidak berubah drastis.

SETUP:
    pip install playwright pandas --break-system-packages
    playwright install chromium

KREDENSIAL:
    JANGAN hardcode username/password di file ini. Set lewat environment
    variable supaya tidak ke-commit ke git / ke-share tidak sengaja:

        export UJB_USERNAME="staffjict"
        export UJB_PASSWORD="********"

    (Command di atas contoh untuk Linux/Mac. Windows: gunakan `set` di cmd
    atau `$env:UJB_USERNAME=...` di PowerShell.)
"""

import os
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ujb_scraper")

BASE_URL = "https://dashboard.ujbgroup.com"
REPORT_URL = f"{BASE_URL}/report/custom_jict"

USERNAME = os.environ.get("UJB_USERNAME")
PASSWORD = os.environ.get("UJB_PASSWORD")

# Mapping prefix "Unit" di laporan vendor -> equipment_category di pipeline dashboard.
# Sesuaikan/lengkapi kalau ternyata ada kategori lain (RTGC, FORKLIFT, dll) yang
# formatnya beda dari yang sudah dicek (HT xxx -> HEAD_TRUCK).
UNIT_PREFIX_TO_CATEGORY = {
    "HT": "HEAD_TRUCK",
    "BUS": "BUS",
    # TODO: tambahkan mapping lain kalau muncul, mis. "RTGC": "RTGC", "FL": "FORKLIFT"
}


def login(page: Page, username: str, password: str) -> None:
    """Login ke dashboard pakai field 'Username' & 'Password' standar."""
    logger.info("Membuka halaman login...")
    page.goto(BASE_URL, wait_until="networkidle")

    # Cari field berdasarkan label/placeholder, lebih tahan-banting daripada
    # hardcode CSS selector/id yang bisa berubah sewaktu-waktu.
    page.get_by_label(re.compile("username", re.I)).or_(
        page.get_by_placeholder(re.compile("username", re.I))
    ).first.fill(username)

    page.get_by_label(re.compile("password", re.I)).or_(
        page.get_by_placeholder(re.compile("password", re.I))
    ).first.fill(password)

    # Tombol submit -- coba beberapa kemungkinan teks umum.
    submit_btn = page.get_by_role("button", name=re.compile("log ?in|masuk|sign ?in", re.I))
    submit_btn.first.click()

    page.wait_for_load_state("networkidle")
    if "login" in page.url.lower():
        raise RuntimeError(
            "Masih di halaman login setelah submit -- kemungkinan username/password "
            "salah, atau ada field tambahan (captcha/OTP) yang belum ditangani script ini."
        )
    logger.info("Login berhasil.")


def _parse_unit(unit: str) -> tuple[str, str]:
    """Pecah string 'Unit' dari laporan (mis. 'HT 136') jadi
    (equipment_category, equipment_id) sesuai skema dashboard.
    Fallback: kalau prefix tidak dikenali, category = prefix apa adanya
    dan equipment_id = string lengkap, supaya tidak ada data yang hilang
    diam-diam -- cukup mudah dicari di CSV hasil untuk dilengkapi mapping-nya.
    """
    unit = unit.strip()
    match = re.match(r"([A-Za-z]+)\s*(.+)", unit)
    if not match:
        return unit, unit
    prefix, rest = match.group(1).upper(), match.group(2).strip()
    category = UNIT_PREFIX_TO_CATEGORY.get(prefix, prefix)
    return category, rest


def set_entries_per_page_max(page: Page) -> None:
    """Set dropdown 'Show entries' ke nilai terbesar yang tersedia, supaya
    tidak perlu klik 'Next' berkali-kali (mengurangi jumlah request/loop)."""
    try:
        dropdown = page.locator("select").filter(has_text=re.compile(r"^\d+$")).first
        options = dropdown.locator("option").all_text_contents()
        numeric_options = [int(o) for o in options if o.strip().isdigit()]
        if numeric_options:
            dropdown.select_option(str(max(numeric_options)))
            page.wait_for_load_state("networkidle")
    except Exception as e:
        logger.warning(f"Tidak bisa set entries per page, lanjut dengan default: {e}")


# Header tetap sesuai struktur tabel di /report/custom_jict (thead-nya 2 baris
# karena kolom "Date" terbagi jadi sub-kolom "Date" & "Time", yang bikin
# deteksi header otomatis salah hitung kalau cuma ambil semua <th> mentah-mentah).
KNOWN_REPORT_HEADERS = [
    "No", "Site", "Product", "Date", "Time", "Unit", "Status",
    "Volume (L)", "Kilometer", "Stock (L)",
]


def scrape_report_table(page: Page) -> pd.DataFrame:
    """Baca seluruh baris tabel laporan, termasuk loop lewat pagination
    kalau 'Show entries' tidak berhasil di-set ke nilai maksimum."""
    all_rows = []
    headers: Optional[list[str]] = None
    page_num = 1

    while True:
        page.wait_for_selector("table")
        body_rows = page.locator("table tbody tr")
        row_count = body_rows.count()

        if headers is None and row_count > 0:
            first_row_cell_count = body_rows.nth(0).locator("td").count()
            if first_row_cell_count == len(KNOWN_REPORT_HEADERS):
                headers = KNOWN_REPORT_HEADERS
            else:
                # Struktur tabel beda dari yang sudah dicek sebelumnya -- pakai
                # nama kolom generik (Col_0, Col_1, ...) supaya tetap kepakai,
                # daripada diam-diam kehilangan data. Sesuaikan KNOWN_REPORT_HEADERS
                # di atas kalau ini kejadian terus.
                headers = [f"Col_{i}" for i in range(first_row_cell_count)]
                logger.warning(
                    f"Jumlah kolom tabel ({first_row_cell_count}) tidak sama dengan "
                    f"KNOWN_REPORT_HEADERS ({len(KNOWN_REPORT_HEADERS)}). "
                    f"Pakai nama kolom generik -- cek manual hasil scrape-nya."
                )

        for i in range(row_count):
            cells = body_rows.nth(i).locator("td").all_inner_texts()
            cells = [c.strip() for c in cells]
            if len(cells) == len(headers):
                all_rows.append(dict(zip(headers, cells)))

        logger.info(f"Halaman {page_num}: {row_count} baris terkumpul (total: {len(all_rows)}).")

        next_btn = page.get_by_role("link", name=re.compile("^next$", re.I))
        if next_btn.count() == 0:
            break
        # Tombol "Next" biasanya dapat class 'disabled' di elemen induk (<li>)
        # saat sudah di halaman terakhir -- deteksi ini supaya loop berhenti.
        parent_classes = next_btn.first.locator("xpath=..").get_attribute("class") or ""
        if "disabled" in parent_classes.lower():
            break

        next_btn.first.click()
        page.wait_for_load_state("networkidle")
        page_num += 1

        if page_num > 200:  # safety guard, hindari infinite loop kalau selector salah
            logger.warning("Berhenti paksa di 200 halaman -- cek apakah deteksi 'Next' benar.")
            break

    return pd.DataFrame(all_rows)


def transform_to_dashboard_schema(raw_df: pd.DataFrame, source_label: str = "ujb_dashboard_scrape") -> pd.DataFrame:
    """Ubah hasil scrape (kolom: No, Site, Product, Date, Time, Unit, Status,
    Volume (L), Kilometer, Stock (L)) jadi skema yang sama dengan
    data/processed/cleaned_fuel_data.csv, supaya bisa langsung digabung ke
    pipeline dashboard yang sudah ada.
    """
    if raw_df.empty:
        return pd.DataFrame(columns=[
            "date", "year", "month", "equipment_category", "equipment_id",
            "fuel_liter", "status_text", "source_sheet", "source_file",
            "source_row", "data_status", "issue_code",
        ])

    df = raw_df.copy()
    df.columns = [c.strip() for c in df.columns]

    date_col = next((c for c in df.columns if c.lower() == "date"), None)
    unit_col = next((c for c in df.columns if c.lower() == "unit"), None)
    volume_col = next((c for c in df.columns if "volume" in c.lower()), None)
    status_col = next((c for c in df.columns if c.lower() == "status"), None)

    parsed = df[unit_col].apply(_parse_unit)
    df["equipment_category"] = parsed.apply(lambda t: t[0])
    df["equipment_id"] = parsed.apply(lambda t: t[1])

    df["date"] = pd.to_datetime(df[date_col], errors="coerce")
    df["fuel_liter"] = pd.to_numeric(
        df[volume_col].astype(str).str.replace(",", "", regex=False), errors="coerce"
    )
    df["status_text"] = df[status_col] if status_col else ""
    df["year"] = df["date"].dt.year
    df["month"] = df["date"].dt.month

    df["source_sheet"] = "N/A"
    df["source_file"] = source_label
    df["source_row"] = range(1, len(df) + 1)
    df["data_status"] = df["fuel_liter"].apply(lambda v: "VALID" if pd.notna(v) else "INVALID")
    df["issue_code"] = df["fuel_liter"].apply(lambda v: "" if pd.notna(v) else "MISSING_VOLUME")

    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    return df[[
        "date", "year", "month", "equipment_category", "equipment_id",
        "fuel_liter", "status_text", "source_sheet", "source_file",
        "source_row", "data_status", "issue_code",
    ]]


def run_scrape(date_from: Optional[str] = None, date_to: Optional[str] = None,
               headless: bool = True) -> pd.DataFrame:
    """Entry point utama: login, buka report, isi rentang tanggal (kalau
    ada field-nya), scrape tabel, transform ke skema dashboard.
    """
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "UJB_USERNAME / UJB_PASSWORD belum di-set di environment variable. "
            "Lihat komentar SETUP di atas file ini."
        )

    date_from = date_from or (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = date_to or datetime.now().strftime("%Y-%m-%d")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            login(page, USERNAME, PASSWORD)

            logger.info(f"Membuka halaman report: {REPORT_URL}")
            page.goto(REPORT_URL, wait_until="networkidle")

            # Isi date range kalau field-nya ada (opsional -- kalau tidak
            # ketemu, lanjut pakai default yang sudah tampil di halaman).
            try:
                date_field = page.get_by_label(re.compile("date range", re.I)).first
                date_field.fill(f"{date_from} to {date_to}")
                page.keyboard.press("Enter")
                page.wait_for_load_state("networkidle")
            except Exception:
                logger.info("Field date range tidak ditemukan/tidak diisi, pakai default halaman.")

            set_entries_per_page_max(page)
            raw_df = scrape_report_table(page)
            logger.info(f"Total baris ter-scrape: {len(raw_df)}")

            return transform_to_dashboard_schema(raw_df)
        finally:
            browser.close()


if __name__ == "__main__":
    result_df = run_scrape(headless=True)
    print(result_df.head(20))
    print(f"\nTotal baris: {len(result_df)}")

    # Kalau UJB_OUTPUT_DIR di-set (arahkan ke folder data/raw project dashboard-mu),
    # hasil scrape langsung masuk ke situ dan otomatis kepakai oleh dashboard --
    # lihat config.UJB_SCRAPE_PATH & src/analytics.py::_merge_ujb_scrape().
    # Kalau tidak di-set, fallback simpan di sebelah script ini (mode standalone).
    output_dir = os.environ.get("UJB_OUTPUT_DIR")
    if output_dir:
        out_path = os.path.join(output_dir, "ujb_scraped_latest.csv")
    else:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw", "ujb_scraped_latest.csv")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    result_df.to_csv(out_path, index=False)
    print(f"Tersimpan ke {out_path}")
