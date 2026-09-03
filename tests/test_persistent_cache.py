import json

import pandas as pd

from src.persistent_cache import get_metadata, invalidate_all, load_artifact, save_artifact


def test_round_trip_with_matching_fingerprint(tmp_path):
    expected = pd.DataFrame({"equipment_id": ["HT-01"], "fuel_liter": [42.5]})
    metadata = save_artifact("sample", expected, "fp-1", cache_dir=tmp_path)
    actual = load_artifact("sample", "fp-1", cache_dir=tmp_path)
    pd.testing.assert_frame_equal(actual, expected)
    assert metadata["raw_data_fingerprint"] == "fp-1"
    assert get_metadata("sample", "fp-1", cache_dir=tmp_path) is not None


def test_fingerprint_mismatch_is_a_cache_miss(tmp_path):
    save_artifact("sample", {"value": 1}, "old", cache_dir=tmp_path)
    assert load_artifact("sample", "new", cache_dir=tmp_path) is None
    assert get_metadata("sample", "new", cache_dir=tmp_path) is None


def test_corrupt_cache_fails_safely(tmp_path):
    (tmp_path / "sample.pkl").write_bytes(b"not-a-pickle")
    (tmp_path / "sample.json").write_text(
        json.dumps({"cache_version": 1, "raw_data_fingerprint": "fp-1"}), encoding="utf-8"
    )
    assert load_artifact("sample", "fp-1", cache_dir=tmp_path) is None


def test_invalidate_removes_only_cache_artifacts(tmp_path):
    save_artifact("sample", {"value": 1}, "fp-1", cache_dir=tmp_path)
    keep = tmp_path / "keep.txt"
    keep.write_text("user data", encoding="utf-8")
    invalidate_all(cache_dir=tmp_path)
    assert keep.exists()
    assert not (tmp_path / "sample.pkl").exists()
    assert not (tmp_path / "sample.json").exists()
