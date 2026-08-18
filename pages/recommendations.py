"""pages/recommendations.py -- Rekomendasi tindak lanjut berbasis rule engine."""
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_recommendations
from src.formatting import format_number
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Recommendations",
    description="Rekomendasi tindak lanjut berbasis pola data (Rule Engine).",
    context="Saran prioritas untuk operasi, bukan kesimpulan teknis final.",
)

recommendations = get_recommendations()

if recommendations.empty:
    st.success("Tidak ada rekomendasi yang terpicu saat ini berdasarkan ambang konfigurasi.")
    st.stop()

# ── KPI ───────────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
with c1: ui.metric_card("Total Rekomendasi",    format_number(len(recommendations)))
with c2: ui.metric_card("Prioritas HIGH",       format_number((recommendations["priority"] == "HIGH").sum()), "", "danger")
with c3: ui.metric_card("Equipment Terdampak",  format_number(recommendations["equipment_id"].nunique()))

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────
col1, col2 = st.columns(2)

sev_colors = {"HIGH": "#D94C4C", "MEDIUM": "#E8A317", "LOW": "#8FC3F0"}

with col1:
    ui.section_header("Distribusi Prioritas")
    counts = recommendations["priority"].value_counts()
    fig = px.bar(
        x=counts.index, y=counts.values, color=counts.index,
        color_discrete_map=sev_colors,
        labels={"x": "", "y": "Jumlah"},
    )
    fig = ui.format_chart(fig)
    fig.update_layout(height=300, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    ui.section_header("Penanggung Jawab")
    by_role = recommendations["responsible_role"].value_counts()
    fig2 = px.pie(
        values=by_role.values, names=by_role.index, hole=0.5,
        color_discrete_sequence=["#1E4D7A", "#3977C8", "#8FC3F0", "#BFDDF8"],
    )
    fig2 = ui.format_chart(fig2)
    fig2.update_layout(height=300)
    st.plotly_chart(fig2, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Filter & Table ────────────────────────────────────────────────────────
ui.section_header("Daftar Rekomendasi")
with st.expander("Filter Rekomendasi", expanded=False):
    fc1, fc2, fc3 = st.columns(3)
    priorities = fc1.multiselect("Priority",         ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM", "LOW"])
    roles      = fc2.multiselect("Responsible Role", sorted(recommendations["responsible_role"].unique()))
    statuses   = fc3.multiselect("Status",           sorted(recommendations["status"].unique()))

filtered = recommendations.copy()
if priorities: filtered = filtered[filtered["priority"].isin(priorities)]
if roles:      filtered = filtered[filtered["responsible_role"].isin(roles)]
if statuses:   filtered = filtered[filtered["status"].isin(statuses)]

st.dataframe(
    filtered[[
        "priority", "equipment_id", "equipment_category", "finding", "evidence",
        "recommended_action", "responsible_role", "target_date", "status",
    ]],
    use_container_width=True, hide_index=True,
)

st.download_button(
    "Download Recommendation Report (CSV)",
    recommendations.to_csv(index=False),
    "recommendation_report.csv", "text/csv",
)
