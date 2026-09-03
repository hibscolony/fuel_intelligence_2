"""pages/anomaly_monitoring.py -- Eksplorasi anomali konsumsi solar."""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_anomalies
from src.formatting import format_number
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Alert Anomali",
    description="Pantau penyimpangan pola konsumsi historis unit.",
    context="Indikasi untuk pemeriksaan, bukan bukti pasti kerusakan.",
)

anomaly_df = get_anomalies()
scored = anomaly_df[anomaly_df["severity"] != "INSUFFICIENT_DATA"]

# ── Filters ───────────────────────────────────────────────────────────────
with st.expander("Filter Anomali", expanded=True):
    fc1, fc2, fc3, fc4 = st.columns(4)
    categories = fc1.multiselect("Kategori", sorted(scored["equipment_category"].unique()))
    equipment  = fc2.multiselect("Equipment ID", sorted(scored["equipment_id"].unique()))
    severities = fc3.multiselect(
        "Severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        default=["MEDIUM", "HIGH", "CRITICAL"],
    )
    date_range = fc4.date_input("Rentang tanggal", value=(scored["date"].min(), scored["date"].max()))

filtered = scored.copy()
if categories:
    filtered = filtered[filtered["equipment_category"].isin(categories)]
if equipment:
    filtered = filtered[filtered["equipment_id"].isin(equipment)]
if severities:
    filtered = filtered[filtered["severity"].isin(severities)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    filtered = filtered[
        (filtered["date"] >= pd.Timestamp(date_range[0]))
        & (filtered["date"] <= pd.Timestamp(date_range[1]))
    ]

st.markdown("<br>", unsafe_allow_html=True)

# ── KPI Summary ───────────────────────────────────────────────────────────
ui.section_header("Ringkasan Anomali")
c1, c2, c3, c4 = st.columns(4)
with c1: ui.metric_card("Detected",          format_number(len(filtered)))
with c2: ui.metric_card("Critical Severity", format_number((filtered["severity"] == "CRITICAL").sum()), "", "danger")
with c3: ui.metric_card("High / Medium",     format_number((filtered["severity"].isin(["HIGH", "MEDIUM"])).sum()), "", "warning")
with c4: ui.metric_card("Affected Equipment",format_number(filtered["equipment_id"].nunique()))

st.markdown("<br><br>", unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────────────
col1, col2 = st.columns([2, 1])

sev_colors = {
    "CRITICAL": "#D94C4C", "HIGH": "#E8A317",
    "MEDIUM": "#F5CC80", "LOW": "#8FC3F0", "NORMAL": "#EAF0F6",
}

with col1:
    ui.section_header("Tren Waktu dan Titik Anomali")
    if equipment and len(equipment) == 1:
        eq_data = scored[scored["equipment_id"] == equipment[0]].sort_values("date")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=eq_data["date"], y=eq_data["fuel_liter"],
            mode="lines+markers", name="Fuel Liter",
            line=dict(color="#3977C8"),
        ))
        anomalies_only = eq_data[eq_data["severity"] != "NORMAL"]
        marker_colors  = [sev_colors.get(s, "#3977C8") for s in anomalies_only["severity"]]
        fig.add_trace(go.Scatter(
            x=anomalies_only["date"], y=anomalies_only["fuel_liter"],
            mode="markers",
            marker=dict(size=12, color=marker_colors, line=dict(width=1, color="white")),
            name="Anomali",
        ))
        fig = ui.format_chart(fig)
        fig.update_layout(height=380)
        st.plotly_chart(fig, width='stretch')
    else:
        st.info("Pilih **tepat satu** equipment di filter Equipment ID untuk melihat grafik time series.")

with col2:
    ui.section_header("Kontribusi per Kategori")
    contrib = filtered.groupby("equipment_category").size().sort_values(ascending=True)
    fig2 = px.bar(
        x=contrib.values, y=contrib.index, orientation="h",
        labels={"x": "Jumlah Anomali", "y": ""},
    )
    fig2 = ui.format_chart(fig2)
    fig2.update_traces(marker_color="#1E4D7A")
    fig2.update_layout(height=380, showlegend=False)
    st.plotly_chart(fig2, width='stretch')

st.markdown("<br>", unsafe_allow_html=True)

ui.section_header("Deviasi dari Pola Normal")
if not filtered.empty:
    fig3 = px.scatter(
        filtered, x="date", y="deviation_percentage", color="severity",
        color_discrete_map=sev_colors,
        hover_data=["equipment_id", "equipment_category", "anomaly_reason"],
    )
    fig3.add_hline(y=0, line_color="#94A3B8", line_dash="dash")
    fig3 = ui.format_chart(fig3)
    fig3.update_layout(height=360)
    st.plotly_chart(fig3, width='stretch')

st.markdown("<br>", unsafe_allow_html=True)

ui.section_header("Daftar Anomali")
st.dataframe(
    filtered[[
        "date", "equipment_category", "equipment_id", "fuel_liter", "expected_fuel",
        "deviation_percentage", "severity", "anomaly_type", "anomaly_reason",
    ]].sort_values("date", ascending=False),
    width="stretch", hide_index=True,
)

st.download_button(
    "Download Anomaly Report (CSV)",
    anomaly_df.to_csv(index=False),
    "anomaly_report.csv", "text/csv",
)
