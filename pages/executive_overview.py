"""pages/executive_overview.py -- Ringkasan level eksekutif."""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import (
    get_cleaning_result, get_forecast_monitoring, get_anomalies,
    get_health_scores, get_data_quality, get_saving_scenarios,
)
from src.formatting import format_liter, format_percentage, format_number, health_status_color

st.title("Executive Overview")
st.caption("Ringkasan menyeluruh operasional solar JICT tahun 2025 -- semua angka dihitung ulang dari data unit harian, bukan diambil mentah dari total workbook.")

cleaning = get_cleaning_result()
forecast = get_forecast_monitoring()
anomaly_df = get_anomalies()
health_scores = get_health_scores()
dq = get_data_quality()
saving = get_saving_scenarios()

valid = cleaning["cleaned_fuel_data"][cleaning["cleaned_fuel_data"]["data_status"] != "INVALID_DATE"]
total_fuel = valid["fuel_liter"].sum()
daily_actual = forecast["daily_actual"]

# --- Baris KPI utama ---------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Solar 2025 (hitung ulang)", format_liter(total_fuel))
c2.metric("Forecast 7 Hari", format_liter(daily_actual.iloc[-7:].sum()) if len(daily_actual) >= 7 else "-")
c3.metric("Forecast 30 Hari", format_liter(daily_actual.iloc[-30:].sum()) if len(daily_actual) >= 30 else "-")
c4.metric("WAPE Model Forecast", format_percentage(forecast["summary"].wape),
          help="Weighted Absolute Percentage Error dari model forecasting yang dipantau.")

c5, c6, c7, c8 = st.columns(4)
n_anomaly = (anomaly_df["severity"].isin(["MEDIUM", "HIGH", "CRITICAL"])).sum()
n_critical_units = health_scores[health_scores["critical_anomaly_count"] > 0]["equipment_id"].nunique()
c5.metric("Jumlah Anomali (Medium+)", format_number(n_anomaly))
c6.metric("Unit dengan Anomali Kritis", format_number(n_critical_units))
c7.metric("Data Completeness", format_percentage(dq["kpis"].data_completeness_percentage))
c8.metric("Proyeksi Penghematan (Target)", format_liter(
    saving["scenarios"].set_index("scenario").loc["Target Saving", "saving_liter"]))

if forecast["is_placeholder"]:
    st.warning(
        "Forecast di atas memakai model PLACEHOLDER (seasonal-naive) karena hasil model "
        "forecasting asli belum diintegrasikan. Lihat halaman Forecast Monitoring untuk detail.",
        icon="⚠️",
    )

st.divider()

# =============================================================================
# PEMAKAIAN SOLAR PER KATEGORI ALAT
# =============================================================================
st.subheader("⛽ Pemakaian Solar per Kategori Alat")

by_cat_stats = valid.groupby("equipment_category").agg(
    total_liter=("fuel_liter", "sum"),
    n_equipment=("equipment_id", "nunique"),
    n_transaksi=("fuel_liter", "count"),
).reset_index().sort_values("total_liter", ascending=False)
by_cat_stats["share_pct"] = (by_cat_stats["total_liter"] / by_cat_stats["total_liter"].sum() * 100).round(1)
by_cat_stats["rata_rata_per_equipment"] = (by_cat_stats["total_liter"] / by_cat_stats["n_equipment"]).round(1)

cat_col1, cat_col2 = st.columns([3, 2])
with cat_col1:
    fig_cat_bar = px.bar(
        by_cat_stats, x="total_liter", y="equipment_category", orientation="h",
        text=by_cat_stats["share_pct"].map(lambda v: f"{v:.1f}%"),
        labels={"total_liter": "Total Liter", "equipment_category": ""},
        color="equipment_category",
    )
    fig_cat_bar.update_traces(textposition="outside")
    fig_cat_bar.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                               yaxis=dict(autorange="reversed"), showlegend=False)
    st.plotly_chart(fig_cat_bar, width="stretch")

with cat_col2:
    st.markdown("**Ringkasan per Kategori**")
    display_table = by_cat_stats[["equipment_category", "total_liter", "share_pct",
                                   "n_equipment", "rata_rata_per_equipment"]].rename(columns={
        "equipment_category": "Kategori", "total_liter": "Total (L)", "share_pct": "% Total",
        "n_equipment": "Jml Alat", "rata_rata_per_equipment": "Rata-rata/Alat (L)",
    })
    st.dataframe(display_table, width="stretch", hide_index=True)

st.markdown("**Tren Bulanan per Kategori**")
valid_periods = valid.copy()
valid_periods["year_month"] = valid_periods["date"].dt.to_period("M")
monthly_by_cat = valid_periods.groupby(["year_month", "equipment_category"])["fuel_liter"].sum().reset_index()
monthly_by_cat["period_label"] = monthly_by_cat["year_month"].astype(str)
monthly_by_cat = monthly_by_cat.sort_values("year_month")
fig_cat_monthly = px.bar(
    monthly_by_cat, x="period_label", y="fuel_liter", color="equipment_category",
    labels={"fuel_liter": "Liter", "period_label": "", "equipment_category": "Kategori"},
)
fig_cat_monthly.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12))
st.plotly_chart(fig_cat_monthly, width="stretch")

st.markdown("**Tabel Pemakaian Solar per Bulan per Kategori (Liter)**")
pivot_monthly_cat = monthly_by_cat.pivot(index="period_label", columns="equipment_category",
                                          values="fuel_liter").fillna(0)
pivot_monthly_cat["TOTAL"] = pivot_monthly_cat.sum(axis=1)
pivot_monthly_cat = pivot_monthly_cat.round(0)
st.dataframe(pivot_monthly_cat, width="stretch")
st.download_button("Download Pemakaian per Bulan per Kategori (CSV)", pivot_monthly_cat.to_csv(),
                    "consumption_by_month_category.csv", "text/csv")

rtgc_iht_share = by_cat_stats.set_index("equipment_category")["share_pct"].reindex(
    ["RTGC", "HEAD_TRUCK"]).sum()
date_span = f"{valid['date'].min().strftime('%b %Y')} - {valid['date'].max().strftime('%b %Y')}"
st.caption(f"RTGC + Head Truck (IHT) menyumbang **{rtgc_iht_share:.1f}%** dari total pemakaian solar "
           f"periode {date_span}.")

st.download_button("Download Ringkasan per Kategori (CSV)", by_cat_stats.to_csv(index=False),
                    "consumption_by_category_summary.csv", "text/csv")

st.divider()

# --- Grafik: aktual vs forecast ---------------------------------------------
left, right = st.columns([2, 1])
with left:
    st.subheader("Aktual vs Forecast (60 Hari Terakhir)")
    plot_df = forecast["forecast_df"].tail(60)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["actual_fuel"], name="Aktual",
                              line=dict(color="#0b2545", width=2)))
    fig.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["forecast_fuel"], name="Forecast",
                              line=dict(color="#13c2c2", width=2, dash="dash")))
    fig.add_trace(go.Scatter(
        x=pd.concat([plot_df["date"], plot_df["date"][::-1]]),
        y=pd.concat([plot_df["upper_interval"], plot_df["lower_interval"][::-1]]),
        fill="toself", fillcolor="rgba(19,194,194,0.15)", line=dict(color="rgba(0,0,0,0)"),
        name="Prediction Interval", showlegend=True,
    ))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Konsumsi per Kategori")
    by_cat = valid.groupby("equipment_category")["fuel_liter"].sum().sort_values(ascending=False)
    fig2 = px.pie(values=by_cat.values, names=by_cat.index, hole=0.45,
                  color_discrete_sequence=px.colors.sequential.Teal_r)
    fig2.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig2, width="stretch")

st.divider()

col_a, col_b = st.columns(2)
with col_a:
    st.subheader("Top 10 Equipment - Total Solar")
    top10 = (valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].sum()
             .sort_values(ascending=False).head(10).reset_index())
    top10["label"] = top10["equipment_category"] + " - " + top10["equipment_id"]
    fig3 = px.bar(top10, x="fuel_liter", y="label", orientation="h", color="equipment_category",
                  labels={"fuel_liter": "Liter", "label": ""})
    fig3.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig3, width="stretch")

with col_b:
    st.subheader("Distribusi Health Status")
    status_counts = health_scores["health_status"].value_counts()
    colors = [health_status_color(s) for s in status_counts.index]
    fig4 = go.Figure(go.Bar(x=status_counts.index, y=status_counts.values, marker_color=colors))
    fig4.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="", yaxis_title="Jumlah Equipment")
    st.plotly_chart(fig4, width="stretch")

st.subheader("Tren Konsumsi Bulanan (Total Seluruh Alat)")
monthly_total = valid_periods.groupby("year_month")["fuel_liter"].sum().reset_index()
monthly_total["period_label"] = monthly_total["year_month"].astype(str)
monthly_total = monthly_total.sort_values("year_month")
fig5 = px.bar(monthly_total, x="period_label", y="fuel_liter", labels={"fuel_liter": "Liter", "period_label": ""},
              color_discrete_sequence=["#0b2545"])
fig5.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig5, width="stretch")

st.caption(
    "Catatan: seluruh angka konsumsi adalah CATATAN PENGISIAN SOLAR (refueling), bukan pengukuran "
    "konsumsi mesin real-time. Anomali & health score adalah indikasi untuk pemeriksaan, bukan diagnosis kerusakan."
)
