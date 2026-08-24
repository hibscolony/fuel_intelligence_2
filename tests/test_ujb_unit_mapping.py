"""Tests taxonomy unit UJB yang sudah dikonfirmasi operasional."""

from src.ujb_unit_mapping import parse_ujb_unit, normalize_ujb_category_and_id


def test_confirmed_head_truck_and_bus_mapping():
    assert parse_ujb_unit("HT 152") == ("HEAD_TRUCK", "152")
    assert parse_ujb_unit("BUS 03") == ("BUS", "03")


def test_kend_ops_named_units_keep_full_equipment_id():
    assert parse_ujb_unit("HILUX 02") == ("KEND_OPS", "HILUX 02")
    assert parse_ujb_unit("RANGGA 05") == ("KEND_OPS", "RANGGA 05")
    assert parse_ujb_unit("INOVA DM") == ("KEND_OPS", "INOVA DM")


def test_jakarta_plate_is_kend_ops_and_keeps_full_plate():
    assert parse_ujb_unit("B 8137 OH") == ("KEND_OPS", "B 8137 OH")


def test_rfk_typo_and_frk_collapse_to_same_forklift_unit():
    assert parse_ujb_unit("FRK 26") == ("FORKLIFT", "26")
    assert parse_ujb_unit("RFK 26") == ("FORKLIFT", "26")


def test_legacy_scrape_categories_are_repaired_on_load():
    assert normalize_ujb_category_and_id("HILUX", "02") == ("KEND_OPS", "HILUX 02")
    assert normalize_ujb_category_and_id("RANGGA", "05") == ("KEND_OPS", "RANGGA 05")
    assert normalize_ujb_category_and_id("INOVA", "DM") == ("KEND_OPS", "INOVA DM")
    assert normalize_ujb_category_and_id("B", "8137 OH") == ("KEND_OPS", "B 8137 OH")
    assert normalize_ujb_category_and_id("RFK", "26") == ("FORKLIFT", "26")
    assert normalize_ujb_category_and_id("FRK", "26") == ("FORKLIFT", "26")


def test_elf_is_not_reclassified_without_confirmation():
    assert parse_ujb_unit("ELF 03") == ("ELF", "03")