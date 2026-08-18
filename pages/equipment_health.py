"""pages/equipment_health.py -- Fuel Consumption Health Score per equipment."""
import sys
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from src.analytics import get_health_scores
from src.formatting import format_number
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Equipment Health",
    description="Fuel Consumption Health Score (0–100) sebagai alat bantu prioritasi pemeriksaan.",
    context="Bukan diagnosis kerusakan mesin.",
)

health_scores = get_health_scores()
scored = health_scores[health_scores["health_status"] != "INSUFFICIENT_DATA"]

# ── KPI ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1: ui.metric_card("Avg. Health Score",   f"{scored['health_score'].mean():.1f}")
with c2: ui.metric_card("Critical Equipment",  format_number((health_scores["health_status"] == "CRITICAL").sum()), "", "danger")
with c3: ui.metric_card("Review Equipment",    format_number((health_scores["health_status"] == "REVIEW").sum()), "", "warning")
with c4: ui.metric_card("Insufficient Data",   format_number((health_scores["health_status"] == "INSUFFICIENT_DATA").sum()))

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 2])

sev_colors = {
    "CRITICAL": "#D94C4C", "REVIEW": "#E8A317",
    "MONITOR":  "#F5CC80", "HEALTHY": "#22A06B", "INSUFFICIENT_DATA": "#94A3B8",
}

with col1:
    ui.section_header("Distribusi Status")
    counts = health_scores["health_status"].value_counts()
    colors = [sev_colors.get(s, "#3977C8") for s in counts.index]
    fig = go.Figure(go.Bar(
        x=counts.values, y=counts.index, orientation="h", marker_color=colors,
    ))
    fig = ui.format_chart(fig)
    fig.update_layout(height=280)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    ui.section_header("Health Score per Kategori")
    fig2 = px.box(scored, x="equipment_category", y="health_score", color="equipment_category")
    fig2 = ui.format_chart(fig2)
    fig2.update_layout(height=280, showlegend=False)
    st.plotly_chart(fig2, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Filter & Table ────────────────────────────────────────────────────────
ui.section_header("Cari & Filter Equipment")
with st.expander("Filter Data", expanded=True):
    fc1, fc2, fc3 = st.columns(3)
    categories = fc1.multiselect("Kategori", sorted(health_scores["equipment_category"].unique()))
    statuses   = fc2.multiselect("Health Status", ["HEALTHY", "MONITOR", "REVIEW", "CRITICAL", "INSUFFICIENT_DATA"])
    search_id  = fc3.text_input("Cari Equipment ID")

filtered = health_scores.copy()
if categories: filtered = filtered[filtered["equipment_category"].isin(categories)]
if statuses:   filtered = filtered[filtered["health_status"].isin(statuses)]
if search_id:  filtered = filtered[filtered["equipment_id"].str.contains(search_id, case=False, na=False)]

st.dataframe(
    filtered[[
        "equipment_id", "equipment_category", "health_score", "health_status",
        "average_daily_fuel", "anomaly_count", "critical_anomaly_count",
        "trend_percentage", "data_completeness", "recommended_action",
    ]].sort_values("health_score"),
    use_container_width=True, hide_index=True,
)

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Radar Chart ───────────────────────────────────────────────────────────
ui.section_header("Breakdown Komponen Penalti")
st.caption("Pilih satu equipment — makin besar area grafik = makin banyak penalti.")

eq_choice   = st.selectbox("Pilih Equipment", sorted(scored["equipment_id"].unique()), label_visibility="collapsed")
row         = scored[scored["equipment_id"] == eq_choice].iloc[0]
penalty_cols = list(config.HEALTH_SCORE_WEIGHTS.keys())

fig3 = go.Figure()
fig3.add_trace(go.Scatterpolar(
    r=[row[c] for c in penalty_cols],
    theta=penalty_cols,
    fill="toself",
    name=eq_choice,
    line_color="#D94C4C",
    fillcolor="rgba(217, 76, 76, 0.15)",
))
fig3.update_layout(
    polar=dict(
        radialaxis=dict(visible=True, range=[0, 100], gridcolor="#EAF0F6"),
        angularaxis=dict(gridcolor="#EAF0F6"),
    ),
    height=400,
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(fig3, use_container_width=True)

st.info(
    f"**Health score {eq_choice}:** {row['health_score']:.1f} ({row['health_status']})  \n"
    f"**Rekomendasi:** {row['recommended_action']}"
)

st.download_button(
    "Download Equipment Health Score (CSV)",
    health_scores.to_csv(index=False),
    "equipment_health_score.csv", "text/csv",
)
