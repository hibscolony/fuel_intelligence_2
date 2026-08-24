"""Utility untuk identitas transaksi dan histori persisten UJB.

`ujb_scraped_latest.csv` adalah snapshot window terbaru, sedangkan
`ujb_history.csv` adalah union seluruh event yang pernah terlihat. History
dideduplikasi memakai `source_event_key`, bukan nomor urut hasil scrape,
karena `source_row` dapat berubah setiap workflow berjalan.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

import pandas as pd


RAW_EVENT_KEY_FIELDS = (
    "Site",
    "Product",
    "Date",
    "Time",
    "Unit",
    "Status",
    "Volume (L)",
    "Kilometer",
)


def _canonical(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " ".join(str(value).strip().split()).upper()


def make_source_event_key(row: Mapping[str, object]) -> str:
    """Buat ID stabil untuk satu event pengisian dari kolom report vendor.

    Field `No` dan `Stock (L)` sengaja tidak dipakai: nomor baris dapat berubah
    ketika sort/filter berubah, sedangkan stock adalah state dispenser yang
    tidak diperlukan untuk mengidentifikasi transaksi.
    """
    payload = "|".join(_canonical(row.get(field, "")) for field in RAW_EVENT_KEY_FIELDS)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def merge_ujb_history(existing: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """Gabungkan snapshot baru ke history tanpa menggandakan event yang sama."""
    if "source_event_key" not in latest.columns:
        raise ValueError("Snapshot UJB wajib memiliki kolom source_event_key.")

    latest = latest.copy()
    if existing is None or existing.empty:
        merged = latest
    else:
        existing = existing.copy()
        if "source_event_key" not in existing.columns:
            # History format lama belum punya event key. Pertahankan recordnya,
            # tetapi buat fallback deterministik dari skema transformed.
            fallback_cols = [
                "date", "event_time", "equipment_category", "equipment_id",
                "fuel_liter", "status_text", "source_file",
            ]
            for col in fallback_cols:
                if col not in existing.columns:
                    existing[col] = ""
            existing["source_event_key"] = existing.apply(
                lambda r: hashlib.sha1(
                    "|".join(_canonical(r.get(c, "")) for c in fallback_cols).encode("utf-8")
                ).hexdigest(),
                axis=1,
            )
        merged = pd.concat([existing, latest], ignore_index=True, sort=False)

    merged = merged.drop_duplicates(subset=["source_event_key"], keep="last")

    if "date" in merged.columns:
        merged["_sort_date"] = pd.to_datetime(merged["date"], errors="coerce")
        sort_cols = ["_sort_date"]
        ascending = [False]
        if "event_time" in merged.columns:
            sort_cols.append("event_time")
            ascending.append(False)
        merged = merged.sort_values(sort_cols, ascending=ascending, na_position="last")
        merged = merged.drop(columns=["_sort_date"])

    return merged.reset_index(drop=True)


def write_snapshot_and_history(latest: pd.DataFrame, output_dir: str | Path) -> dict:
    """Tulis snapshot terbaru dan update `ujb_history.csv` secara idempotent."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    latest_path = output_dir / "ujb_scraped_latest.csv"
    history_path = output_dir / "ujb_history.csv"

    latest.to_csv(latest_path, index=False)

    if history_path.exists():
        existing = pd.read_csv(history_path)
    else:
        existing = pd.DataFrame()

    history = merge_ujb_history(existing, latest)
    history.to_csv(history_path, index=False)

    return {
        "latest_path": latest_path,
        "history_path": history_path,
        "latest_rows": int(len(latest)),
        "history_rows": int(len(history)),
        "new_unique_rows": int(len(history) - len(existing)) if not existing.empty else int(len(history)),
    }
