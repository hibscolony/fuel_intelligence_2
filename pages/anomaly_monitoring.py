"""pages/anomaly_monitoring.py -- Eksplorasi anomali konsumsi solar."""
import sys
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_anomalies
from src.formatting import format_number, severity_color

st.title("Fuel Anomaly")
st.caption(
    "Anomali adalah indikasi PEMERIKSAAN berdasarkan penyimpangan pola historis unit -- "
    "BUKAN bukti kebocoran, pemborosan, atau kerusakan alat."
)

anomaly_df = get_anomalies()
scored = anomaly_df[anomaly_df["severity"] != "INSUFFICIENT_DATA"]

# --- Filter -------------------------------------------------------------
with st.expander("Filter", expanded=True):
    fc1, fc2, fc3, fc4 = st.columns(4)
    categories = fc1.multiselect("Kategori", sorted(scored["equipment_category"].unique()))
    equipment_options = sorted(scored["equipment_id"].unique())
    equipment = fc2.multiselect("Equipment ID", equipment_options)
    severities = fc3.multiselect("Severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                                  default=["MEDIUM", "HIGH", "CRITICAL"])
    date_range = fc4.date_input("Rentang tanggal", value=(scored["date"].min(), scored["date"].max()))

import pandas as pd

filtered = scored.copy()
if categories:
    filtered = filtered[filtered["equipment_category"].isin(categories)]
if equipment:
    filtered = filtered[filtered["equipment_id"].isin(equipment)]
if severities:
    filtered = filtered[filtered["severity"].isin(severities)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    filtered = filtered[(filtered["date"] >= pd.Timestamp(date_range[0]))
                        & (filtered["date"] <= pd.Timestamp(date_range[1]))]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Anomali (Filter)", format_number(len(filtered)))
c2.metric("CRITICAL", format_number((filtered["severity"] == "CRITICAL").sum()))
c3.metric("HIGH", format_number((filtered["severity"] == "HIGH").sum()))
c4.metric("Equipment Terdampak", format_number(filtered["equipment_id"].nunique()))

st.divider()

col1, col2 = st.columns([2, 1])
with col1:
    st.subheader("Time Series dengan Titik Anomali")
    if equipment and len(equipment) == 1:
        eq_data = scored[scored["equipment_id"] == equipment[0]].sort_values("date")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq_data["date"], y=eq_data["fuel_liter"], mode="lines+markers",
                                  name="Fuel Liter", line=dict(color="#0b2545")))
        anomalies_only = eq_data[eq_data["severity"] != "NORMAL"]
        fig.add_trace(go.Scatter(x=anomalies_only["date"], y=anomalies_only["fuel_liter"], mode="markers",
                                  marker=dict(size=10, color=[severity_color(s) for s in anomalies_only["severity"]]),
                                  name="Anomali"))
        fig.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Pilih TEPAT SATU equipment di filter Equipment ID untuk melihat grafik time series.")

with col2:
    st.subheader("Kontribusi Anomali per Kategori")
    contrib = filtered.groupby("equipment_category").size().sort_values(ascending=False)
    fig2 = px.bar(x=contrib.values, y=contrib.index, orientation="h",
                  labels={"x": "Jumlah Anomali", "y": ""})
    fig2.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig2, width="stretch")

st.subheader("Deviasi dari Pola Normal")
if not filtered.empty:
    fig3 = px.scatter(filtered, x="date", y="deviation_percentage", color="severity",
                      color_discrete_map={s: severity_color(s) for s in filtered["severity"].unique()},
                      hover_data=["equipment_id", "equipment_category", "anomaly_reason"])
    fig3.add_hline(y=0, line_color="gray")
    fig3.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig3, width="stretch")

st.subheader("Tabel Anomali")
st.dataframe(
    filtered[["date", "equipment_category", "equipment_id", "fuel_liter", "expected_fuel",
              "deviation_percentage", "severity", "anomaly_type", "anomaly_reason"]]
    .sort_values("date", ascending=False),
    width="stretch", hide_index=True,
)

st.download_button("Download Anomaly Report (CSV)", anomaly_df.to_csv(index=False),
                    "anomaly_report.csv", "text/csv")
