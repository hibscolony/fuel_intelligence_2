"""
ujb_source.py
==============
Membaca data UJB yang sudah ditransformasi ke skema long-form.

Untuk pipeline hybrid, histori persisten ``ujb_history.csv`` diprioritaskan
atas snapshot ``ujb_scraped_latest.csv``. Snapshot tetap menjadi fallback
untuk kompatibilitas saat history belum tersedia.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import config
from src.ujb_unit_mapping import normalize_ujb_category_and_id
from src.ujb_coverage import load_ujb_coverage_manifest


class NoUjbDataError(Exception):
    """Dilempar kalau tidak ada file UJB yang dapat dipakai."""


_REQUIRED_COLUMNS = [
    "date", "year", "month", "equipment_category", "equipment_id",
    "fuel_liter", "status_text", "source_sheet", "source_file", "source_row",
]
_OPTIONAL_PROVENANCE_COLUMNS = ["event_time", "source_event_key", "ujb_coverage_status"]


def _candidate_paths() -> list[Path]:
    history_path = config.RAW_DATA_DIR / "ujb_history.csv"
    return [history_path, config.UJB_SCRAPE_PATH]


def _read_first_available_ujb_file() -> tuple[pd.DataFrame, Path]:
    seen_existing: list[Path] = []
    for path in _candidate_paths():
        if not path.exists():
            continue
        seen_existing.append(path)
        df = pd.read_csv(path)
        if not df.empty:
            return df, path

    if seen_existing:
        names = ", ".join(p.name for p in seen_existing)
        raise NoUjbDataError(
            f"File UJB ditemukan ({names}) tetapi semuanya kosong. "
            "Cek workflow scraper terakhir di GitHub Actions."
        )
    raise NoUjbDataError(
        "Belum ada data UJB. Diharapkan salah satu dari "
        "data/raw/ujb_history.csv atau data/raw/ujb_scraped_latest.csv."
    )


def load_ujb_long_df() -> pd.DataFrame:
    """Kembalikan histori UJB siap masuk ke source reconciliation/cleaning.

    - ``ujb_history.csv`` dipakai lebih dulu agar histori tidak hilang saat
      snapshot 7-hari bergeser.
    - taxonomy legacy dinormalisasi ulang saat load;
    - ``event_time`` dan ``source_event_key`` dipertahankan untuk audit dan
      identitas transaksi;
    - duplicate event key di history dibuang secara idempotent.
    """
    df, source_path = _read_first_available_ujb_file()

    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise NoUjbDataError(
            f"File {source_path.name} kehilangan kolom wajib: {missing}. "
            "Kemungkinan skema scraper berubah."
        )

    normalized = df.apply(
        lambda r: normalize_ujb_category_and_id(
            r["equipment_category"], r["equipment_id"]
        ),
        axis=1,
    )
    df["equipment_category"] = normalized.apply(lambda t: t[0])
    df["equipment_id"] = normalized.apply(lambda t: t[1])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    coverage = load_ujb_coverage_manifest(config.RAW_DATA_DIR)
    if coverage.empty:
        df["ujb_coverage_status"] = "UNKNOWN"
    else:
        status_by_date = coverage.set_index("date")["coverage_status"]
        df["ujb_coverage_status"] = (
            df["date"].dt.normalize().map(status_by_date).fillna("UNKNOWN")
        )

    if "source_event_key" in df.columns:
        key = df["source_event_key"].astype("string")
        valid_key = key.notna() & key.str.strip().ne("")
        keyed = df.loc[valid_key].drop_duplicates(
            subset=["source_event_key"], keep="last"
        )
        unkeyed = df.loc[~valid_key]
        df = pd.concat([keyed, unkeyed], ignore_index=True, sort=False)

    df["source_system"] = "UJB"

    output_columns = _REQUIRED_COLUMNS + [
        c for c in _OPTIONAL_PROVENANCE_COLUMNS if c in df.columns
    ] + ["source_system"]
    return df[output_columns].copy()
