"""pages/executive_overview.py -- Ringkasan level eksekutif (Lengkap & Terrestorasi)."""
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
from src import ui

ui.inject_global_css()

# Page Header
ui.page_header(
    title="Executive Overview",
    description="Ringkasan menyeluruh operasional solar JICT -- semua angka dihitung ulang dari data unit harian, bukan diambil mentah dari total workbook.",
)

cleaning = get_cleaning_result()
forecast = get_forecast_monitoring()
anomaly_df = get_anomalies()
health_scores = get_health_scores()
dq = get_data_quality()
saving = get_saving_scenarios()

valid = cleaning["cleaned_fuel_data"][cleaning["cleaned_fuel_data"]["data_status"] != "INVALID_DATE"]
total_fuel = valid["fuel_liter"].sum()
daily_actual = forecast["daily_actual"]

# =============================================================================
# METRIC CARDS (8 METRICS TOTAL)
# =============================================================================
c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.metric_card("Total Solar 2025 (Hitung Ulang)", format_liter(total_fuel), "Data terverifikasi", "info")
with c2:
    f7_val = format_liter(daily_actual.iloc[-7:].sum()) if len(daily_actual) >= 7 else "-"
    ui.metric_card("Forecast 7 Hari", f7_val, "Proyeksi 1 minggu", "info")
with c3:
    f30_val = format_liter(daily_actual.iloc[-30:].sum()) if len(daily_actual) >= 30 else "-"
    ui.metric_card("Forecast 30 Hari", f30_val, "Proyeksi 1 bulan", "info")
with c4:
    ui.metric_card("WAPE Model Forecast", format_percentage(forecast["summary"].wape), "Weighted error", "info")

st.markdown("<div style='height: 0.75rem;'></div>", unsafe_allow_html=True)

c5, c6, c7, c8 = st.columns(4)
n_anomaly = (anomaly_df["severity"].isin(["MEDIUM", "HIGH", "CRITICAL"])).sum()
n_critical_units = health_scores[health_scores["critical_anomaly_count"] > 0]["equipment_id"].nunique()
target_saving_val = saving["scenarios"].set_index("scenario").loc["Target (Sesuai Input)", "saving_liter"] if "Target (Sesuai Input)" in saving["scenarios"]["scenario"].values else saving["scenarios"]["saving_liter"].iloc[-1]

with c5:
    status_col = "danger" if n_anomaly > 10 else "warning" if n_anomaly > 0 else "success"
    ui.metric_card("Jumlah Anomali (Medium+)", format_number(n_anomaly), "Perlu perhatian", status_col)
with c6:
    ui.metric_card("Unit Anomali Kritis", format_number(n_critical_units), "Tindakan segera", "danger" if n_critical_units > 0 else "success")
with c7:
    ui.metric_card("Data Completeness", format_percentage(dq["kpis"].data_completeness_percentage), "Kelengkapan data", "success")
with c8:
    ui.metric_card("Proyeksi Penghematan", format_liter(target_saving_val), "Skenario target", "success")

if forecast["is_placeholder"]:
    st.warning(
        "Forecast di atas memakai model PLACEHOLDER (seasonal-naive) karena hasil model forecasting asli belum diintegrasikan. Lihat halaman Forecast Monitoring untuk detail.",
        icon="⚠️",
    )

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# PEMAKAIAN SOLAR PER KATEGORI ALAT
# =============================================================================
ui.section_header("⛽ Pemakaian Solar per Kategori Alat")

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
        color_discrete_sequence=['#3977C8', '#5B9BE6', '#1E4D7A', '#8FC3F0', '#22A06B', '#E8A317', '#D94C4C', '#BFDDF8']
    )
    fig_cat_bar.update_traces(textposition="outside")
    fig_cat_bar = ui.format_chart(fig_cat_bar)
    fig_cat_bar.update_layout(height=360, yaxis=dict(autorange="reversed"), showlegend=False)
    st.plotly_chart(fig_cat_bar, use_container_width=True)

with cat_col2:
    st.markdown("**Ringkasan per Kategori**")
    display_table = by_cat_stats[["equipment_category", "total_liter", "share_pct",
                                   "n_equipment", "rata_rata_per_equipment"]].rename(columns={
        "equipment_category": "Kategori", "total_liter": "Total (L)", "share_pct": "% Total",
        "n_equipment": "Jml Alat", "rata_rata_per_equipment": "Rata-rata/Alat (L)",
    })
    st.dataframe(display_table, use_container_width=True, hide_index=True)

st.markdown("<br>", unsafe_allow_html=True)

# Tren Bulanan per Kategori
st.markdown("**Tren Bulanan per Kategori**")
valid_periods = valid.copy()
valid_periods["year_month"] = valid_periods["date"].dt.to_period("M")
monthly_by_cat = valid_periods.groupby(["year_month", "equipment_category"])["fuel_liter"].sum().reset_index()
monthly_by_cat["period_label"] = monthly_by_cat["year_month"].astype(str)
monthly_by_cat = monthly_by_cat.sort_values("year_month")

fig_cat_monthly = px.bar(
    monthly_by_cat, x="period_label", y="fuel_liter", color="equipment_category",
    labels={"fuel_liter": "Liter", "period_label": "", "equipment_category": "Kategori"},
    color_discrete_sequence=['#3977C8', '#5B9BE6', '#1E4D7A', '#8FC3F0', '#22A06B', '#E8A317', '#D94C4C', '#BFDDF8']
)
fig_cat_monthly = ui.format_chart(fig_cat_monthly)
fig_cat_monthly.update_layout(height=380, barmode='stack')
st.plotly_chart(fig_cat_monthly, use_container_width=True)

# Pivot Table Bulanan per Kategori
st.markdown("**Tabel Pemakaian Solar per Bulan per Kategori (Liter)**")
pivot_monthly_cat = monthly_by_cat.pivot(index="period_label", columns="equipment_category",
                                          values="fuel_liter").fillna(0)
pivot_monthly_cat["TOTAL"] = pivot_monthly_cat.sum(axis=1)
pivot_monthly_cat = pivot_monthly_cat.round(0)
st.dataframe(pivot_monthly_cat, use_container_width=True)

btn_c1, btn_c2 = st.columns(2)
with btn_c1:
    st.download_button("Download Pemakaian per Bulan per Kategori (CSV)", pivot_monthly_cat.to_csv(),
                        "consumption_by_month_category.csv", "text/csv")
with btn_c2:
    st.download_button("Download Ringkasan per Kategori (CSV)", by_cat_stats.to_csv(index=False),
                        "consumption_by_category_summary.csv", "text/csv")

rtgc_iht_share = by_cat_stats.set_index("equipment_category")["share_pct"].reindex(
    ["RTGC", "HEAD_TRUCK"]).sum()
date_span = f"{valid['date'].min().strftime('%b %Y')} - {valid['date'].max().strftime('%b %Y')}"
st.caption(f"RTGC + Head Truck (IHT) menyumbang **{rtgc_iht_share:.1f}%** dari total pemakaian solar periode {date_span}.")

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# AKTUAL vs FORECAST & KONSUMSI PER KATEGORI (PIE)
# =============================================================================
left, right = st.columns([2, 1])
with left:
    ui.section_header("Aktual vs Forecast (60 Hari Terakhir)")
    plot_df = forecast["forecast_df"].tail(60)
    fig_f = go.Figure()
    fig_f.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["actual_fuel"], name="Aktual",
                               line=dict(color="#1E4D7A", width=2)))
    fig_f.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["forecast_fuel"], name="Forecast",
                               line=dict(color="#5B9BE6", width=2, dash="dash")))
    fig_f.add_trace(go.Scatter(
        x=pd.concat([plot_df["date"], plot_df["date"][::-1]]),
        y=pd.concat([plot_df["upper_interval"], plot_df["lower_interval"][::-1]]),
        fill="toself", fillcolor="rgba(91,155,230,0.15)", line=dict(color="rgba(0,0,0,0)"),
        name="Prediction Interval", showlegend=True,
    ))
    fig_f = ui.format_chart(fig_f)
    fig_f.update_layout(height=360)
    st.plotly_chart(fig_f, use_container_width=True)

with right:
    ui.section_header("Proporsi Konsumsi Kategori")
    by_cat_pie = valid.groupby("equipment_category")["fuel_liter"].sum().sort_values(ascending=False)
    fig_pie = px.pie(values=by_cat_pie.values, names=by_cat_pie.index, hole=0.45,
                     color_discrete_sequence=['#1E4D7A', '#3977C8', '#5B9BE6', '#8FC3F0', '#BFDDF8'])
    fig_pie = ui.format_chart(fig_pie)
    fig_pie.update_layout(height=360)
    st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# TOP 10 EQUIPMENT & DISTRIBUSI HEALTH STATUS
# =============================================================================
col_a, col_b = st.columns(2)
with col_a:
    ui.section_header("Top 10 Equipment - Total Solar")
    top10 = (valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].sum()
             .sort_values(ascending=False).head(10).reset_index())
    top10["label"] = top10["equipment_category"] + " - " + top10["equipment_id"]
    fig_top10 = px.bar(top10, x="fuel_liter", y="label", orientation="h", color="equipment_category",
                       labels={"fuel_liter": "Liter", "label": ""},
                       color_discrete_sequence=['#1E4D7A', '#3977C8', '#5B9BE6', '#8FC3F0'])
    fig_top10 = ui.format_chart(fig_top10)
    fig_top10.update_layout(height=360, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_top10, use_container_width=True)

with col_b:
    ui.section_header("Distribusi Health Status")
    status_counts = health_scores["health_status"].value_counts()
    sev_colors = {"CRITICAL": "#D94C4C", "REVIEW": "#E8A317", "MONITOR": "#F5CC80", "HEALTHY": "#22A06B", "INSUFFICIENT_DATA": "#94A3B8"}
    colors = [sev_colors.get(s, "#3977C8") for s in status_counts.index]
    fig_hs = go.Figure(go.Bar(x=status_counts.index, y=status_counts.values, marker_color=colors))
    fig_hs = ui.format_chart(fig_hs)
    fig_hs.update_layout(height=360, xaxis_title="", yaxis_title="Jumlah Equipment")
    st.plotly_chart(fig_hs, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# TREN KONSUMSI BULANAN (TOTAL SELURUH ALAT)
# =============================================================================
ui.section_header("Tren Konsumsi Bulanan (Total Seluruh Alat)")
monthly_total = valid_periods.groupby("year_month")["fuel_liter"].sum().reset_index()
monthly_total["period_label"] = monthly_total["year_month"].astype(str)
monthly_total = monthly_total.sort_values("year_month")
fig_monthly_tot = px.bar(monthly_total, x="period_label", y="fuel_liter",
                         labels={"fuel_liter": "Liter", "period_label": ""},
                         color_discrete_sequence=["#1E4D7A"])
fig_monthly_tot = ui.format_chart(fig_monthly_tot)
fig_monthly_tot.update_layout(height=320)
st.plotly_chart(fig_monthly_tot, use_container_width=True)

st.caption(
    "Catatan: seluruh angka konsumsi adalah CATATAN PENGISIAN SOLAR (refueling), bukan pengukuran "
    "konsumsi mesin real-time. Anomali & health score adalah indikasi untuk pemeriksaan, bukan diagnosis kerusakan."
)
