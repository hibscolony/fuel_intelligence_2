"""
Normalisasi nama unit dari laporan UJB ke taxonomy equipment dashboard.

Aturan di sini dipakai di dua tempat:
1. saat scraper mentransformasi kolom ``Unit`` dari dashboard UJB;
2. saat membaca CSV UJB lama yang mungkin masih memakai taxonomy sebelum
   mapping diperbaiki.

Mapping yang sudah dikonfirmasi operasional:
- HT -> HEAD_TRUCK
- BUS -> BUS
- HILUX / RANGGA / INOVA / plat B xxxx ... -> KEND_OPS
- FRK -> FORKLIFT
- RFK -> FORKLIFT (typo dari FRK)

ELF belum digabung ke KEND_OPS karena klasifikasinya belum dikonfirmasi.
"""
from __future__ import annotations

import re


PREFIX_TO_CATEGORY = {
    "HT": "HEAD_TRUCK",
    "BUS": "BUS",
    "HILUX": "KEND_OPS",
    "RANGGA": "KEND_OPS",
    "INOVA": "KEND_OPS",
    "FRK": "FORKLIFT",
    "RFK": "FORKLIFT",
    "ELF": "ELF",
}

KEND_OPS_NAMED_PREFIXES = {"HILUX", "RANGGA", "INOVA"}

# Contoh yang sudah muncul di data: "B 8137 OH".
# Sengaja cukup konservatif agar prefix "B" biasa tidak otomatis dianggap plat.
_LICENSE_PLATE_RE = re.compile(r"^B\s+\d{1,4}\s+[A-Z]{1,3}$", re.IGNORECASE)


def _clean_spaces(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip())


def parse_ujb_unit(unit: object) -> tuple[str, str]:
    """Parse string mentah kolom ``Unit`` menjadi (category, equipment_id).

    Untuk KEND_OPS berbasis nama/plat, equipment_id dipertahankan lengkap
    (mis. ``HILUX 02``, ``INOVA DM``, atau ``B 8137 OH``) agar identifier pendek
    tidak bentrok antar jenis kendaraan. Untuk FRK/RFK, ID numeriknya
    dipertahankan sehingga ``FRK 26`` dan ``RFK 26`` menjadi unit kanonik yang
    sama: FORKLIFT / 26.
    """
    raw = _clean_spaces(unit)
    if not raw:
        return "UNKNOWN", ""

    if _LICENSE_PLATE_RE.fullmatch(raw):
        return "KEND_OPS", raw.upper()

    match = re.match(r"^([A-Za-z]+)\s*(.*)$", raw)
    if not match:
        return raw.upper(), raw

    prefix = match.group(1).upper()
    rest = _clean_spaces(match.group(2))
    category = PREFIX_TO_CATEGORY.get(prefix, prefix)

    if prefix in KEND_OPS_NAMED_PREFIXES:
        return "KEND_OPS", f"{prefix} {rest}".strip()

    # Fallback mempertahankan perilaku lama: kategori = prefix apa adanya,
    # equipment_id = bagian setelah prefix. Dengan begitu kategori baru tetap
    # terlihat dan bisa diaudit tanpa kehilangan record.
    return category, rest or raw


def normalize_ujb_category_and_id(category: object, equipment_id: object) -> tuple[str, str]:
    """Normalisasi output CSV UJB lama yang sudah telanjur diparse.

    Contoh legacy CSV:
      HILUX, 02     -> KEND_OPS, HILUX 02
      RANGGA, 05    -> KEND_OPS, RANGGA 05
      INOVA, DM     -> KEND_OPS, INOVA DM
      B, 8137 OH    -> KEND_OPS, B 8137 OH
      RFK, 26       -> FORKLIFT, 26
      FRK, 26       -> FORKLIFT, 26
    """
    cat = _clean_spaces(category).upper()
    eq = _clean_spaces(equipment_id)

    if cat in KEND_OPS_NAMED_PREFIXES:
        return "KEND_OPS", f"{cat} {eq}".strip()

    if cat == "B":
        candidate = f"B {eq}".strip()
        if _LICENSE_PLATE_RE.fullmatch(candidate):
            return "KEND_OPS", candidate.upper()

    if cat in {"FRK", "RFK", "FORKLIFT"}:
        return "FORKLIFT", eq

    mapped = PREFIX_TO_CATEGORY.get(cat, cat)
    return mapped, eq