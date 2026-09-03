"""Persistent, source-aware cache for expensive dashboard analytics."""
from __future__ import annotations

import json
import pickle
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import config


CACHE_VERSION = 1
CACHE_DIR = config.DATA_DIR / "cache"


def _paths(name: str, cache_dir: Path | None = None) -> tuple[Path, Path]:
    base = cache_dir or CACHE_DIR
    return base / f"{name}.pkl", base / f"{name}.json"


def load_artifact(name: str, fingerprint: str, *, cache_dir: Path | None = None) -> Any | None:
    """Load only when cache version and raw-data fingerprint both match."""
    data_path, metadata_path = _paths(name, cache_dir)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("cache_version") != CACHE_VERSION:
            return None
        if metadata.get("raw_data_fingerprint") != fingerprint:
            return None
        with data_path.open("rb") as handle:
            return pickle.load(handle)
    except (FileNotFoundError, OSError, ValueError, TypeError, pickle.UnpicklingError, EOFError):
        return None


def save_artifact(
    name: str, value: Any, fingerprint: str, *, cache_dir: Path | None = None
) -> dict:
    """Atomically persist an artifact and its validation metadata."""
    base = cache_dir or CACHE_DIR
    base.mkdir(parents=True, exist_ok=True)
    data_path, metadata_path = _paths(name, base)
    metadata = {
        "cache_version": CACHE_VERSION,
        "raw_data_fingerprint": fingerprint,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with NamedTemporaryFile("wb", delete=False, dir=base, prefix=f".{name}-") as temp:
        pickle.dump(value, temp, protocol=pickle.HIGHEST_PROTOCOL)
        temp_data_path = Path(temp.name)
    with NamedTemporaryFile(
        "w", delete=False, dir=base, prefix=f".{name}-", encoding="utf-8"
    ) as temp:
        json.dump(metadata, temp, ensure_ascii=False, indent=2)
        temp_metadata_path = Path(temp.name)
    temp_data_path.replace(data_path)
    temp_metadata_path.replace(metadata_path)
    return metadata


def get_metadata(name: str, fingerprint: str, *, cache_dir: Path | None = None) -> dict | None:
    """Read metadata for a currently valid artifact without unpickling its data."""
    _, metadata_path = _paths(name, cache_dir)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    if metadata.get("cache_version") != CACHE_VERSION:
        return None
    if metadata.get("raw_data_fingerprint") != fingerprint:
        return None
    return metadata


def invalidate_all(*, cache_dir: Path | None = None) -> None:
    """Remove only reproducible cache files; source and report files stay intact."""
    base = cache_dir or CACHE_DIR
    if not base.exists():
        return
    for pattern in ("*.pkl", "*.json"):
        for path in base.glob(pattern):
            path.unlink(missing_ok=True)
