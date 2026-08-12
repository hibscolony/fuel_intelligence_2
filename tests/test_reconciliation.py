"""Unit test dasar untuk src/data_cleaning.py -- dijalankan dengan: pytest tests/"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.data_cleaning import run_cleaning_pipeline, canonicalize_id_for_matching, standardize_equipment_id


@pytest.fixture(scope="module")
def cleaning_result():
    return run_cleaning_pipeline()


def test_standardize_equipment_id_strips_slash_alias():
    assert standardize_equipment_id("B 7963 TAA/ BUS 03") == "B 7963 TAA"
    assert standardize_equipment_id("B 7985 TAA/ BUS 01") == "B 7985 TAA"
    assert standardize_equipment_id("B 7963 TAA") == "B 7963 TAA"


def test_no_slash_suffix_remains_in_cleaned_equipment_ids(cleaning_result):
    ids = cleaning_result.cleaned_fuel_data["equipment_id"].unique()
    assert not any("/" in i for i in ids)


def test_cleaned_fuel_data_status_values_are_known(cleaning_result):
    known = {"VALID", "STATUS_ONLY", "MISSING", "INVALID_DATE", "NEGATIVE_VALUE",
             "DUPLICATE", "UNUSUALLY_HIGH", "UNKNOWN_STATUS"}
    assert set(cleaning_result.cleaned_fuel_data["data_status"].unique()) <= known


def test_no_row_silently_dropped(cleaning_result):
    # setiap baris yang bukan VALID tetap harus punya alasan (data_status terisi)
    assert cleaning_result.cleaned_fuel_data["data_status"].notna().all()


def test_equipment_master_no_duplicate_id_per_category(cleaning_result):
    master = cleaning_result.equipment_master
    dup = master.duplicated(subset=["equipment_category", "equipment_id"]).sum()
    assert dup == 0


def test_reconciliation_status_values_are_known(cleaning_result):
    known = {"MATCH", "MINOR DIFFERENCE", "MAJOR DIFFERENCE", "REQUIRES REVIEW"}
    assert set(cleaning_result.monthly_reconciliation["validation_status"].unique()) <= known


def test_reconciliation_covers_every_distinct_year_month_present(cleaning_result):
    valid = cleaning_result.cleaned_fuel_data[cleaning_result.cleaned_fuel_data["data_status"] != "INVALID_DATE"]
    n_distinct_year_months = valid[["year", "month"]].drop_duplicates().shape[0]
    assert len(cleaning_result.monthly_reconciliation) == n_distinct_year_months


def test_canonicalize_id_for_matching_groups_spacing_variants():
    assert canonicalize_id_for_matching("FRK 05 A") == canonicalize_id_for_matching("FRK 05A")
    assert canonicalize_id_for_matching("B 7963 TAA/ BUS 03") == canonicalize_id_for_matching("B 7963 TAA")
