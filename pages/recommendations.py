"""pages/recommendations.py -- Daftar tindak lanjut berbasis rule engine."""
import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_recommendations
from src.formatting import (
    format_date_label,
    format_number,
    format_recommendation_evidence,
    format_recommendation_role,
)
from src import ui


PRIORITY_LABELS = {"HIGH": "Tinggi", "MEDIUM": "Sedang", "LOW": "Rendah"}


ui.inject_global_css()

ui.page_header(
    title="Tindak Lanjut Operasional",
    description="Daftar sinyal yang perlu divalidasi berdasarkan pola pencatatan pengisian solar.",
    context="Rule engine membantu menentukan urutan pemeriksaan; hasilnya bukan diagnosis teknis final.",
)

recommendations = get_recommendations()

if recommendations.empty:
    st.success("Tidak ada sinyal tindak lanjut yang terpicu berdasarkan ambang rule engine saat ini.")
    st.stop()

# ── Overall summary ────────────────────────────────────────────────────────
high_count = recommendations["priority"].eq("HIGH").sum()
affected_entities = recommendations["equipment_id"].nunique()
responsible_roles = recommendations["responsible_role"].nunique()

c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.metric_card("Total Sinyal", format_number(len(recommendations)))
with c2:
    ui.metric_card("Prioritas Tinggi", format_number(high_count), "Periksa lebih dahulu", "danger")
with c3:
    ui.metric_card("Entitas Terdampak", format_number(affected_entities))
with c4:
    ui.metric_card("Kelompok PIC", format_number(responsible_roles))

st.info(
    "**Mode prototipe — read-only.** Status penyelesaian belum dapat disimpan karena aplikasi belum "
    "memiliki database operasional. Gunakan daftar ini untuk menentukan urutan verifikasi, lalu catat "
    "hasil tindak lanjut pada sistem perusahaan yang berlaku.",
    icon="ℹ️",
)

# ── Operational filters ───────────────────────────────────────────────────
ui.section_header("Filter Daftar Kerja")

f1, f2, f3 = st.columns([2, 1.25, 0.75])
search_unit = f1.text_input(
    "Cari unit atau temuan",
    placeholder="Contoh: 177, RTGC, perubahan pola...",
)
priorities = f2.multiselect(
    "Prioritas",
    options=["HIGH", "MEDIUM", "LOW"],
    default=["HIGH", "MEDIUM", "LOW"],
    format_func=lambda value: PRIORITY_LABELS[value],
)
display_limit = f3.selectbox("Tampilkan", options=[10, 20, 50], index=1)

f4, f5 = st.columns(2)
categories = f4.multiselect(
    "Kategori alat",
    options=sorted(recommendations["equipment_category"].dropna().unique()),
    format_func=lambda value: str(value).replace("_", " "),
)
roles = f5.multiselect(
    "Penanggung jawab",
    options=sorted(recommendations["responsible_role"].dropna().unique()),
    format_func=format_recommendation_role,
)

filtered = recommendations.copy()
if priorities:
    filtered = filtered[filtered["priority"].isin(priorities)]
if categories:
    filtered = filtered[filtered["equipment_category"].isin(categories)]
if roles:
    filtered = filtered[filtered["responsible_role"].isin(roles)]
if search_unit.strip():
    query = search_unit.strip()
    searchable = (
        filtered["equipment_id"].astype(str)
        + " " + filtered["equipment_category"].astype(str)
        + " " + filtered["finding"].astype(str)
        + " " + filtered["recommended_action"].astype(str)
    )
    filtered = filtered[searchable.str.contains(query, case=False, regex=False, na=False)]

# ── Work list ─────────────────────────────────────────────────────────────
ui.section_header("Daftar Sinyal Tindak Lanjut")

if filtered.empty:
    st.warning("Tidak ada sinyal yang cocok dengan filter. Kurangi filter atau gunakan kata pencarian lain.")
else:
    visible = filtered.head(display_limit)
    st.caption(
        f"Menampilkan {len(visible)} dari {len(filtered)} sinyal hasil filter, mencakup "
        f"{filtered['equipment_id'].nunique()} entitas. Urutan mengikuti prioritas rule engine."
    )

    for _, action in visible.iterrows():
        ui.action_card(
            priority=action["priority"],
            equipment_id=action["equipment_id"],
            equipment_category=str(action["equipment_category"]).replace("_", " "),
            finding=action["finding"],
            recommended_action=action["recommended_action"],
            responsible_role=format_recommendation_role(action["responsible_role"]),
            target_date=format_date_label(action["target_date"]),
            evidence=format_recommendation_evidence(action["evidence"]),
            evidence_collapsed=True,
        )

    if len(filtered) > len(visible):
        st.caption(
            f"{len(filtered) - len(visible)} sinyal lain disembunyikan. Persempit filter atau ubah jumlah tampilan."
        )

# ── Export filtered result ────────────────────────────────────────────────
export_columns = {
    "priority": "Prioritas",
    "equipment_id": "Unit/Entitas",
    "equipment_category": "Kategori",
    "finding": "Temuan",
    "evidence": "Bukti Data",
    "recommended_action": "Tindakan Disarankan",
    "responsible_role": "Penanggung Jawab",
    "target_date": "Target Verifikasi",
    "status": "Status Sistem",
}
export_data = filtered[list(export_columns)].rename(columns=export_columns).copy()
export_data["Penanggung Jawab"] = export_data["Penanggung Jawab"].map(format_recommendation_role)
export_data["Bukti Data"] = export_data["Bukti Data"].map(format_recommendation_evidence)

st.download_button(
    "Unduh hasil filter (CSV)",
    export_data.to_csv(index=False).encode("utf-8-sig"),
    "tindak_lanjut_terfilter.csv",
    "text/csv",
    width="stretch",
)
