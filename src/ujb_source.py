"""
ujb_source.py
==============
Alternatif dari data_loader.parse_workbook() -- alih-alih parsing Excel
manual, baca data langsung dari hasil scrape dashboard.ujbgroup.com
(lihat ujb_dashboard_scraper.py di root project).

Tujuannya supaya data UJB melewati proses cleaning/flagging (duplikat,
nilai negatif, outlier tinggi, dst) yang PERSIS SAMA dengan yang dulu
dipakai untuk data Excel -- bukan logika terpisah -- supaya hasil anomaly
detection, forecasting, dst tetap konsisten metodenya.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config


class NoUjbDataError(Exception):
    """Dilempar kalau file hasil scrape UJB belum ada / masih kosong --
    kondisi normal untuk deploy pertama sebelum GitHub Actions scraper
    pernah jalan sekali. Ditangkap di app.py untuk ditampilkan sebagai
    pesan ramah, bukan traceback mentah ke pengguna dashboard.
    """


# Kolom yang WAJIB ada di long_df sebelum masuk ke build_cleaned_fuel_data(),
# harus identik dengan output data_loader.parse_workbook().long_df.
_LONG_DF_COLUMNS = [
    "date", "year", "month", "equipment_category", "equipment_id",
    "fuel_liter", "status_text", "source_sheet", "source_file", "source_row",
]


def load_ujb_long_df() -> pd.DataFrame:
    """Baca config.UJB_SCRAPE_PATH, kembalikan DataFrame siap dipakai
    build_cleaned_fuel_data() -- sama seperti ParseResult.long_df dari jalur
    Excel yang lama.
    """
    if not config.UJB_SCRAPE_PATH.exists():
        raise NoUjbDataError(
            f"Belum ada data hasil scrape UJB di {config.UJB_SCRAPE_PATH}. "
            f"Kemungkinan GitHub Actions scraper belum pernah jalan -- cek tab "
            f"'Actions' di repo, atau jalankan workflow-nya manual sekali "
            f"('Run workflow')."
        )

    df = pd.read_csv(config.UJB_SCRAPE_PATH)
    if df.empty:
        raise NoUjbDataError(
            f"File {config.UJB_SCRAPE_PATH.name} ada tapi isinya kosong -- "
            f"kemungkinan scraper terakhir jalan tapi tidak berhasil ambil data "
            f"(cek log run terakhir di GitHub Actions)."
        )

    missing = [c for c in _LONG_DF_COLUMNS if c not in df.columns]
    if missing:
        raise NoUjbDataError(
            f"File {config.UJB_SCRAPE_PATH.name} kehilangan kolom wajib: {missing}. "
            f"Kemungkinan skema ujb_dashboard_scraper.py berubah -- cek "
            f"transform_to_dashboard_schema() di sana."
        )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df[_LONG_DF_COLUMNS].copy()
