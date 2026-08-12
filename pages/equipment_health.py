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
from src.analytics import get_health_scores, get_clusters
from src.formatting import format_number, format_percentage, health_status_color

st.title("Equipment Health")
st.caption("Fuel Consumption Health Score (0-100) -- alat bantu PRIORITASI pemeriksaan, bukan diagnosis kerusakan mesin.")

health_scores = get_health_scores()
clusters = get_clusters()

c1, c2, c3, c4 = st.columns(4)
scored = health_scores[health_scores["health_status"] != "INSUFFICIENT_DATA"]
c1.metric("Rata-rata Health Score", f"{scored['health_score'].mean():.1f}")
c2.metric("Equipment CRITICAL", format_number((health_scores["health_status"] == "CRITICAL").sum()))
c3.metric("Equipment REVIEW", format_number((health_scores["health_status"] == "REVIEW").sum()))
c4.metric("Insufficient Data", format_number((health_scores["health_status"] == "INSUFFICIENT_DATA").sum()))

st.divider()

col1, col2 = st.columns([1, 2])
with col1:
    st.subheader("Distribusi Status")
    counts = health_scores["health_status"].value_counts()
    fig = go.Figure(go.Bar(x=counts.values, y=counts.index, orientation="h",
                            marker_color=[health_status_color(s) for s in counts.index]))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

with col2:
    st.subheader("Health Score per Kategori")
    fig2 = px.box(scored, x="equipment_category", y="health_score", color="equipment_category")
    fig2.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    st.plotly_chart(fig2, width="stretch")

st.subheader("Cari & Filter Equipment")
fc1, fc2, fc3 = st.columns(3)
categories = fc1.multiselect("Kategori", sorted(health_scores["equipment_category"].unique()))
statuses = fc2.multiselect("Health Status", ["HEALTHY", "MONITOR", "REVIEW", "CRITICAL", "INSUFFICIENT_DATA"])
search_id = fc3.text_input("Cari Equipment ID")

filtered = health_scores.copy()
if categories:
    filtered = filtered[filtered["equipment_category"].isin(categories)]
if statuses:
    filtered = filtered[filtered["health_status"].isin(statuses)]
if search_id:
    filtered = filtered[filtered["equipment_id"].str.contains(search_id, case=False, na=False)]

st.dataframe(
    filtered[["equipment_id", "equipment_category", "health_score", "health_status",
              "average_daily_fuel", "anomaly_count", "critical_anomaly_count",
              "trend_percentage", "data_completeness", "recommended_action"]]
    .sort_values("health_score"),
    width="stretch", hide_index=True,
)

st.divider()
st.subheader("Breakdown Komponen (Radar) -- Pilih Satu Equipment")
eq_choice = st.selectbox("Equipment", sorted(scored["equipment_id"].unique()))
row = scored[scored["equipment_id"] == eq_choice].iloc[0]
penalty_cols = list(config.HEALTH_SCORE_WEIGHTS.keys())
fig3 = go.Figure()
fig3.add_trace(go.Scatterpolar(r=[row[c] for c in penalty_cols], theta=penalty_cols, fill="toself",
                                name=eq_choice, line_color="#d62728"))
fig3.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 100])), height=420,
                    margin=dict(l=10, r=10, t=30, b=10))
st.plotly_chart(fig3, width="stretch")
st.caption(f"Health score {eq_choice}: **{row['health_score']:.1f}** ({row['health_status']}). "
           f"Rekomendasi: {row['recommended_action']}")

st.download_button("Download Equipment Health Score (CSV)", health_scores.to_csv(index=False),
                    "equipment_health_score.csv", "text/csv")
