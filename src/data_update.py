"""Safe validation and activation of operator-provided source files."""
from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import pandas as pd

import config
from src.data_loader import WorkbookStructureError, parse_single_workbook
from src.ujb_history import merge_ujb_history


UJB_REQUIRED_COLUMNS = {
    "date", "year", "month", "equipment_category", "equipment_id",
    "fuel_liter", "status_text", "source_sheet", "source_file", "source_row",
    "source_event_key",
}


def safe_upload_name(filename: str, allowed_suffixes: set[str]) -> str:
    """Return a path-safe filename and reject unsupported extensions."""
    basename = Path(str(filename)).name.strip()
    suffix = Path(basename).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Format {suffix or '(tanpa ekstensi)'} tidak didukung.")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(basename).stem).strip(" ._")
    if not stem:
        stem = "upload"
    return f"{stem}{suffix}"


def validate_excel(content: bytes, filename: str) -> dict:
    """Parse an uploaded workbook without touching the active raw directory."""
    safe_name = safe_upload_name(filename, {".xls", ".xlsx"})
    if not content:
        raise ValueError("File kosong.")
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=Path(safe_name).suffix) as temp:
            temp.write(content)
            temp_path = Path(temp.name)
        result = parse_single_workbook(temp_path)
    except WorkbookStructureError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    data = result.long_df
    valid_dates = pd.to_datetime(data["date"], errors="coerce").dropna()
    if valid_dates.empty:
        raise ValueError("Workbook tidak memiliki tanggal transaksi yang valid.")
    return {
        "filename": safe_name,
        "kind": "EXCEL",
        "rows": int(len(data)),
        "years": sorted(int(year) for year in valid_dates.dt.year.unique()),
        "date_min": valid_dates.min(),
        "date_max": valid_dates.max(),
        "equipment_count": int(data["equipment_id"].nunique()),
        "preview": data.head(20),
    }


def validate_ujb(content: bytes, filename: str) -> dict:
    """Validate the transformed UJB snapshot schema used by the pipeline."""
    safe_name = safe_upload_name(filename, {".csv"})
    if not content:
        raise ValueError("File kosong.")
    try:
        data = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise ValueError(f"CSV tidak dapat dibaca: {exc}") from exc
    missing = sorted(UJB_REQUIRED_COLUMNS - set(data.columns))
    if missing:
        raise ValueError(f"Kolom wajib UJB belum lengkap: {', '.join(missing)}")
    if data.empty:
        raise ValueError("CSV UJB tidak memiliki baris data.")
    dates = pd.to_datetime(data["date"], errors="coerce")
    invalid_dates = int(dates.isna().sum())
    if invalid_dates:
        raise ValueError(f"Terdapat {invalid_dates} baris dengan tanggal tidak valid.")
    if data["source_event_key"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Semua baris UJB wajib memiliki source_event_key.")
    return {
        "filename": safe_name,
        "kind": "UJB",
        "rows": int(len(data)),
        "years": sorted(int(year) for year in dates.dt.year.unique()),
        "date_min": dates.min(),
        "date_max": dates.max(),
        "equipment_count": int(data["equipment_id"].nunique()),
        "preview": data.head(20),
        "data": data,
    }


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")


def _archive_file(path: Path, archive_dir: Path, timestamp: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / f"{path.stem}__{timestamp}{path.suffix}"
    counter = 1
    while destination.exists():
        destination = archive_dir / f"{path.stem}__{timestamp}_{counter}{path.suffix}"
        counter += 1
    return Path(shutil.move(str(path), str(destination)))


def _append_log(record: dict, audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / "data_update_log.csv"
    columns = [
        "activated_at", "kind", "filename", "sha256", "rows", "date_min",
        "date_max", "archived_files", "new_unique_rows",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if not exists:
            writer.writeheader()
        writer.writerow({key: record.get(key, "") for key in columns})


def activate_excel(
    content: bytes,
    validation: dict,
    *,
    raw_dir: Path | None = None,
    archive_dir: Path | None = None,
    audit_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Activate a workbook, archiving active workbooks with overlapping years."""
    raw_dir = raw_dir or config.RAW_DATA_DIR
    archive_dir = archive_dir or config.DATA_DIR / "archive" / "raw"
    audit_dir = audit_dir or config.DATA_DIR / "audit"
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp(now)
    upload_years = set(validation["years"])
    archive_candidates: list[Path] = []
    for existing in sorted(raw_dir.glob("*.xls*")):
        try:
            parsed = parse_single_workbook(existing).long_df
            existing_years = set(pd.to_datetime(parsed["date"], errors="coerce").dropna().dt.year)
        except Exception:
            existing_years = set()
        if upload_years & existing_years or existing.name == validation["filename"]:
            archive_candidates.append(existing)

    archived = [_archive_file(path, archive_dir, timestamp) for path in archive_candidates]
    target = raw_dir / validation["filename"]
    try:
        with NamedTemporaryFile("wb", delete=False, dir=raw_dir, prefix=".upload-") as temp:
            temp.write(content)
            staged = Path(temp.name)
        staged.replace(target)
    except Exception:
        for archived_path in archived:
            original_name = archived_path.name.split("__", 1)[0] + archived_path.suffix
            shutil.move(str(archived_path), str(raw_dir / original_name))
        raise

    record = {
        "activated_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "kind": "EXCEL",
        "filename": target.name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "rows": validation["rows"],
        "date_min": validation["date_min"].date().isoformat(),
        "date_max": validation["date_max"].date().isoformat(),
        "archived_files": "; ".join(path.name for path in archived),
        "new_unique_rows": "",
    }
    _append_log(record, audit_dir)
    return record


def activate_ujb(
    content: bytes,
    validation: dict,
    *,
    raw_dir: Path | None = None,
    archive_dir: Path | None = None,
    audit_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Merge a validated UJB snapshot into history while preserving backups."""
    raw_dir = raw_dir or config.RAW_DATA_DIR
    archive_dir = archive_dir or config.DATA_DIR / "archive" / "raw"
    audit_dir = audit_dir or config.DATA_DIR / "audit"
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _timestamp(now)
    history_path = raw_dir / "ujb_history.csv"
    existing_history = pd.read_csv(history_path) if history_path.exists() else pd.DataFrame()
    archived = []
    for name in ("ujb_scraped_latest.csv", "ujb_history.csv"):
        path = raw_dir / name
        if path.exists():
            archived.append(_archive_file(path, archive_dir, timestamp))
    try:
        latest = validation["data"]
        history = merge_ujb_history(existing_history, latest)
        latest.to_csv(raw_dir / "ujb_scraped_latest.csv", index=False)
        history.to_csv(history_path, index=False)
    except Exception:
        (raw_dir / "ujb_scraped_latest.csv").unlink(missing_ok=True)
        history_path.unlink(missing_ok=True)
        for archived_path in archived:
            original_name = archived_path.name.split("__", 1)[0] + archived_path.suffix
            shutil.move(str(archived_path), str(raw_dir / original_name))
        raise

    record = {
        "activated_at": (now or datetime.now()).astimezone().isoformat(timespec="seconds"),
        "kind": "UJB",
        "filename": "ujb_scraped_latest.csv",
        "sha256": hashlib.sha256(content).hexdigest(),
        "rows": validation["rows"],
        "date_min": validation["date_min"].date().isoformat(),
        "date_max": validation["date_max"].date().isoformat(),
        "archived_files": "; ".join(path.name for path in archived),
        "new_unique_rows": max(0, int(len(history) - len(existing_history))),
    }
    _append_log(record, audit_dir)
    return record


def load_update_log(audit_dir: Path | None = None) -> pd.DataFrame:
    path = (audit_dir or config.DATA_DIR / "audit") / "data_update_log.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)
