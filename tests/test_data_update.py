from datetime import datetime

import pandas as pd
import pytest

from src.data_update import activate_ujb, safe_upload_name, validate_ujb


def _ujb_frame(event_key="event-1", fuel_liter=100.0):
    return pd.DataFrame([{
        "date": "2026-09-01",
        "year": 2026,
        "month": 9,
        "equipment_category": "HEAD_TRUCK",
        "equipment_id": "HT-01",
        "fuel_liter": fuel_liter,
        "status_text": "",
        "source_sheet": "ujb",
        "source_file": "export.csv",
        "source_row": 1,
        "source_event_key": event_key,
    }])


def test_safe_upload_name_strips_directories_and_rejects_extension():
    assert safe_upload_name("../../Laporan 2026.xlsx", {".xlsx"}) == "Laporan 2026.xlsx"
    with pytest.raises(ValueError):
        safe_upload_name("payload.exe", {".xlsx"})


def test_validate_ujb_reports_summary():
    content = _ujb_frame().to_csv(index=False).encode("utf-8")
    result = validate_ujb(content, "snapshot.csv")
    assert result["rows"] == 1
    assert result["years"] == [2026]
    assert result["equipment_count"] == 1


def test_validate_ujb_rejects_missing_event_key():
    content = _ujb_frame().drop(columns=["source_event_key"]).to_csv(index=False).encode("utf-8")
    with pytest.raises(ValueError, match="source_event_key"):
        validate_ujb(content, "snapshot.csv")


def test_activate_ujb_archives_previous_files_and_deduplicates(tmp_path):
    raw_dir = tmp_path / "raw"
    archive_dir = tmp_path / "archive"
    audit_dir = tmp_path / "audit"
    raw_dir.mkdir()
    original = _ujb_frame()
    original.to_csv(raw_dir / "ujb_history.csv", index=False)
    original.to_csv(raw_dir / "ujb_scraped_latest.csv", index=False)
    upload = pd.concat([original, _ujb_frame("event-2", 125.0)], ignore_index=True)
    content = upload.to_csv(index=False).encode("utf-8")
    validation = validate_ujb(content, "new.csv")

    record = activate_ujb(
        content,
        validation,
        raw_dir=raw_dir,
        archive_dir=archive_dir,
        audit_dir=audit_dir,
        now=datetime(2026, 9, 3, 10, 0, 0),
    )

    history = pd.read_csv(raw_dir / "ujb_history.csv")
    assert len(history) == 2
    assert record["new_unique_rows"] == 1
    assert len(list(archive_dir.glob("*.csv"))) == 2
    assert (audit_dir / "data_update_log.csv").exists()
