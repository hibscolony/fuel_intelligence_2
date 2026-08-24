"""Tests taxonomy unit UJB yang sudah dikonfirmasi operasional."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.ujb_unit_mapping import parse_ujb_unit, normalize_ujb_category_and_id


def test_confirmed_head_truck_and_bus_mapping():
    assert parse_ujb_unit("HT 152") == ("HEAD_TRUCK", "152")
    assert parse_ujb_unit("BUS 03") == ("BUS", "03")


def test_kend_ops_named_units_keep_full_equipment_id():
    assert parse_ujb_unit("HILUX 02") == ("KEND_OPS", "HILUX 02")
    assert parse_ujb_unit("RANGGA 05") == ("KEND_OPS", "RANGGA 05")
    assert parse_ujb_unit("INOVA DM") == ("KEND_OPS", "INOVA DM")
    assert parse_ujb_unit("ENGINEERING") == ("KEND_OPS", "ENGINEERING")
    assert parse_ujb_unit("TRAGA") == ("KEND_OPS", "TRAGA")
    assert parse_ujb_unit("AMBULANCE") == ("KEND_OPS", "AMBULANCE")
    assert parse_ujb_unit("GALMON") == ("KEND_OPS", "GALMON")
    assert parse_ujb_unit("POLISI") == ("KEND_OPS", "POLISI")


def test_confirmed_genset_mapping_is_modul():
    assert parse_ujb_unit("GENSET") == ("MODUL", "GENSET")
    assert parse_ujb_unit("GENSET 01") == ("MODUL", "GENSET 01")


def test_confirmed_operational_vehicle_plates_are_kend_ops():
    assert parse_ujb_unit("B 8137 OH") == ("KEND_OPS", "B 8137 OH")
    assert parse_ujb_unit("AD 8137 OH") == ("KEND_OPS", "AD 8137 OH")


def test_rfk_typo_and_frk_collapse_to_same_forklift_unit():
    assert parse_ujb_unit("FRK 26") == ("FORKLIFT", "26")
    assert parse_ujb_unit("RFK 26") == ("FORKLIFT", "26")


def test_legacy_scrape_categories_are_repaired_on_load():
    assert normalize_ujb_category_and_id("HILUX", "02") == ("KEND_OPS", "HILUX 02")
    assert normalize_ujb_category_and_id("RANGGA", "05") == ("KEND_OPS", "RANGGA 05")
    assert normalize_ujb_category_and_id("INOVA", "DM") == ("KEND_OPS", "INOVA DM")
    assert normalize_ujb_category_and_id("ENGINEERING", "ENGINEERING") == (
        "KEND_OPS",
        "ENGINEERING",
    )
    assert normalize_ujb_category_and_id("TRAGA", "TRAGA") == ("KEND_OPS", "TRAGA")
    assert normalize_ujb_category_and_id("AMBULANCE", "AMBULANCE") == (
        "KEND_OPS", "AMBULANCE"
    )
    assert normalize_ujb_category_and_id("GALMON", "GALMON") == ("KEND_OPS", "GALMON")
    assert normalize_ujb_category_and_id("POLISI", "POLISI") == ("KEND_OPS", "POLISI")
    assert normalize_ujb_category_and_id("GENSET", "GENSET") == ("MODUL", "GENSET")
    assert normalize_ujb_category_and_id("B", "8137 OH") == ("KEND_OPS", "B 8137 OH")
    assert normalize_ujb_category_and_id("AD", "8137 OH") == ("KEND_OPS", "AD 8137 OH")
    assert normalize_ujb_category_and_id("RFK", "26") == ("FORKLIFT", "26")
    assert normalize_ujb_category_and_id("FRK", "26") == ("FORKLIFT", "26")


def test_elf_remains_its_own_confirmed_category():
    assert parse_ujb_unit("ELF 03") == ("ELF", "03")
