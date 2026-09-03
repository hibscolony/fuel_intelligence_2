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
    get_health_scores, get_data_quality, get_executive_forecast,
    get_forecast_coverage, get_saving_scenarios, get_recommendations,
    get_data_freshness,
)
from src.formatting import (
    format_date_label, format_liter, format_number, format_percentage,
    format_recommendation_evidence, format_recommendation_role, health_status_color,
)
from src.reporting import available_reporting_years, default_reporting_year, select_reporting_period
from src import ui

ui.inject_global_css()

# Page Header
ui.page_header(
    title="Kontrol Harian",
    description="Ringkasan kondisi konsumsi solar, kualitas data, dan prioritas operasional JICT.",
)

cleaning = get_cleaning_result()
cleaned = cleaning["cleaned_fuel_data"]
freshness = get_data_freshness()
ui.data_freshness_banner(freshness)
reporting_years = available_reporting_years(cleaned)
default_year = default_reporting_year(cleaned)
reporting_year = st.selectbox(
    "Periode laporan",
    options=reporting_years,
    index=reporting_years.index(default_year),
    format_func=lambda year: select_reporting_period(cleaned, year).label,
    help="Seluruh total, grafik konsumsi, anomali, dan simulasi saving di halaman ini dibatasi ke tahun yang dipilih.",
)
reporting_period = select_reporting_period(cleaned, reporting_year)

forecast = get_forecast_monitoring()
executive_forecast = get_executive_forecast()
forecast_coverage = get_forecast_coverage()
anomaly_df = get_anomalies()
health_scores = get_health_scores()
dq = get_data_quality()
saving = get_saving_scenarios(reporting_year=reporting_year)
recommendations = get_recommendations()

valid = reporting_period.data
total_fuel = reporting_period.total_liter
period_anomalies = anomaly_df[
    pd.to_datetime(anomaly_df["date"], errors="coerce").dt.year.eq(reporting_year)
].copy()

# =============================================================================
# METRIC CARDS (8 METRICS TOTAL)
# =============================================================================
c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.metric_card(
        f"Total Solar {reporting_period.label}",
        format_liter(total_fuel),
        f"{reporting_period.start_date:%d %b}–{reporting_period.end_date:%d %b %Y}",
        "info",
    )
with c2:
    f7_val = format_liter(executive_forecast["totals"][7])
    ui.metric_card("Forecast 7 Hari ke Depan", f7_val,
                   f"Baseline · per {executive_forecast['as_of_date']:%d %b %Y}", "info")
with c3:
    f30_val = format_liter(executive_forecast["totals"][30])
    ui.metric_card("Forecast 30 Hari ke Depan", f30_val,
                   f"Baseline · per {executive_forecast['as_of_date']:%d %b %Y}", "info")
with c4:
    if forecast["is_placeholder"]:
        ui.metric_card("WAPE Model Produksi", "Belum Tersedia", "Model produksi belum terintegrasi", "warning")
    else:
        ui.metric_card("WAPE Model Produksi", format_percentage(forecast["summary"].wape),
                       "Weighted error", "info")

st.html("<div style='height: 0.75rem;'></div>")

c5, c6, c7, c8 = st.columns(4)
n_anomaly = period_anomalies["severity"].isin(["MEDIUM", "HIGH", "CRITICAL"]).sum()
n_critical_units = period_anomalies.loc[
    period_anomalies["severity"].eq("CRITICAL"), "equipment_id"
].nunique()
target_saving_val = saving["scenarios"].set_index("scenario").loc["Target (Sesuai Input)", "saving_liter"] if "Target (Sesuai Input)" in saving["scenarios"]["scenario"].values else saving["scenarios"]["saving_liter"].iloc[-1]

with c5:
    status_col = "danger" if n_anomaly > 10 else "warning" if n_anomaly > 0 else "success"
    ui.metric_card("Jumlah Anomali (Medium+)", format_number(n_anomaly), "Perlu perhatian", status_col)
with c6:
    ui.metric_card("Unit Anomali Kritis", format_number(n_critical_units), "Tindakan segera", "danger" if n_critical_units > 0 else "success")
with c7:
    dq_status = dq["kpis"].overall_status
    dq_color = "success" if dq_status == "PASS" else "warning" if dq_status == "REVIEW" else "danger"
    ui.metric_card("Data Completeness", format_percentage(dq["kpis"].data_completeness_percentage),
                   f"Status keseluruhan: {dq_status}", dq_color)
with c8:
    ui.metric_card("Proyeksi Penghematan", format_liter(target_saving_val), "Skenario target", "success")

if dq["kpis"].overall_status != "PASS":
    st.error(
        f"Data Quality berstatus **{dq['kpis'].overall_status}**. Angka pada halaman ini boleh dipakai "
        "untuk eksplorasi internal, tetapi harus diverifikasi sebelum keputusan operasional atau anggaran.",
        icon="🛑",
    )

if forecast_coverage["gap_days"]:
    st.warning(
        f"Terdapat **{forecast_coverage['gap_days']} hari coverage gap** "
        f"({forecast_coverage['gap_start']:%d %b %Y}–{forecast_coverage['gap_end']:%d %b %Y}). "
        "Gap dipertahankan sebagai data tidak diketahui, bukan dianggap nol.",
        icon="⚠️",
    )

if forecast["is_placeholder"]:
    st.warning(
        "Model produksi belum terintegrasi. Kartu forecast 7/30 hari memakai baseline "
        "**Seasonal Naive 7 hari** dan diberi label Baseline; WAPE produksi tidak ditampilkan.",
        icon="⚠️",
    )

if executive_forecast["source"] == "training_segment_fallback":
    st.warning(
        "Segmen operasional terbaru belum memiliki minimal tujuh hari. Forecast baseline sementara "
        "dibangun dari segmen training historis.",
        icon="⚠️",
    )

if executive_forecast.get("readiness_status") == "LIMITED":
    st.warning(executive_forecast["readiness_warning"], icon="⚠️")

# =============================================================================
# OPERATIONAL FOLLOW-UP
# =============================================================================
ui.section_header("Perlu Ditindaklanjuti")

open_recommendations = recommendations[
    recommendations["status"].eq("OPEN")
].copy() if not recommendations.empty else recommendations.copy()
top_actions = (
    open_recommendations.drop_duplicates(subset=["equipment_id"], keep="first").head(5)
    if not open_recommendations.empty else open_recommendations
)

if top_actions.empty:
    st.success("Tidak ada tindak lanjut yang terpicu berdasarkan ambang rule engine saat ini.")
else:
    st.caption(
        f"Menampilkan {len(top_actions)} entitas prioritas dari {len(open_recommendations)} sinyal tindak lanjut. "
        "Urutan mengikuti prioritas rule engine dan memakai seluruh data terbaru yang tersedia, "
        "bukan hanya periode laporan yang dipilih."
    )
    for _, action in top_actions.iterrows():
        ui.action_card(
            priority=action["priority"],
            equipment_id=action["equipment_id"],
            equipment_category=action["equipment_category"],
            finding=action["finding"],
            recommended_action=action["recommended_action"],
            responsible_role=format_recommendation_role(action["responsible_role"]),
            target_date=format_date_label(action["target_date"]),
            evidence=format_recommendation_evidence(action["evidence"]),
        )

    st.caption(
        "Rekomendasi adalah indikasi berbasis pola pencatatan pengisian solar. "
        "Validasi data dan kondisi lapangan tetap diperlukan sebelum tindakan teknis."
    )
    st.link_button(
        "Buka semua tindak lanjut",
        "/recommendations",
        icon="✅",
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
    st.plotly_chart(fig_cat_bar, width='stretch')

with cat_col2:
    st.markdown("**Ringkasan per Kategori**")
    display_table = by_cat_stats[["equipment_category", "total_liter", "share_pct",
                                   "n_equipment", "rata_rata_per_equipment"]].rename(columns={
        "equipment_category": "Kategori", "total_liter": "Total (L)", "share_pct": "% Total",
        "n_equipment": "Jml Alat", "rata_rata_per_equipment": "Rata-rata/Alat (L)",
    })
    st.dataframe(display_table, width="stretch", hide_index=True)

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
st.plotly_chart(fig_cat_monthly, width='stretch')

# Pivot Table Bulanan per Kategori
st.markdown("**Tabel Pemakaian Solar per Bulan per Kategori (Liter)**")
pivot_monthly_cat = monthly_by_cat.pivot(index="period_label", columns="equipment_category",
                                          values="fuel_liter").fillna(0)
pivot_monthly_cat["TOTAL"] = pivot_monthly_cat.sum(axis=1)
pivot_monthly_cat = pivot_monthly_cat.round(0)
st.dataframe(pivot_monthly_cat, width="stretch")

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
    ui.section_header("Aktual vs Prediksi (60 Hari Terakhir)")
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
    st.plotly_chart(fig_f, width='stretch')

with right:
    ui.section_header("Proporsi Konsumsi Kategori")
    by_cat_pie = valid.groupby("equipment_category")["fuel_liter"].sum().sort_values(ascending=False)
    fig_pie = px.pie(values=by_cat_pie.values, names=by_cat_pie.index, hole=0.45,
                     color_discrete_sequence=['#1E4D7A', '#3977C8', '#5B9BE6', '#8FC3F0', '#BFDDF8'])
    fig_pie = ui.format_chart(fig_pie)
    fig_pie.update_layout(height=360)
    st.plotly_chart(fig_pie, width='stretch')

st.markdown("<br>", unsafe_allow_html=True)

# =============================================================================
# TOP 10 EQUIPMENT & DISTRIBUSI HEALTH STATUS
# =============================================================================
col_a, col_b = st.columns(2)
with col_a:
    ui.section_header("10 Alat dengan Pengisian Solar Tertinggi")
    top10 = (valid.groupby(["equipment_category", "equipment_id"])["fuel_liter"].sum()
             .sort_values(ascending=False).head(10).reset_index())
    top10["label"] = top10["equipment_category"] + " - " + top10["equipment_id"]
    fig_top10 = px.bar(top10, x="fuel_liter", y="label", orientation="h", color="equipment_category",
                       labels={"fuel_liter": "Liter", "label": ""},
                       color_discrete_sequence=['#1E4D7A', '#3977C8', '#5B9BE6', '#8FC3F0'])
    fig_top10 = ui.format_chart(fig_top10)
    fig_top10.update_layout(height=360, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_top10, width='stretch')

with col_b:
    ui.section_header("Sebaran Status Kesehatan Alat")
    status_counts = health_scores["health_status"].value_counts()
    sev_colors = {"CRITICAL": "#D94C4C", "REVIEW": "#E8A317", "MONITOR": "#F5CC80", "HEALTHY": "#22A06B", "INSUFFICIENT_DATA": "#94A3B8"}
    colors = [sev_colors.get(s, "#3977C8") for s in status_counts.index]
    fig_hs = go.Figure(go.Bar(x=status_counts.index, y=status_counts.values, marker_color=colors))
    fig_hs = ui.format_chart(fig_hs)
    fig_hs.update_layout(height=360, xaxis_title="", yaxis_title="Jumlah Equipment")
    st.plotly_chart(fig_hs, width='stretch')

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
st.plotly_chart(fig_monthly_tot, width='stretch')

st.caption(
    "Catatan: seluruh angka konsumsi adalah CATATAN PENGISIAN SOLAR (refueling), bukan pengukuran "
    "konsumsi mesin real-time. Anomali & health score adalah indikasi untuk pemeriksaan, bukan diagnosis kerusakan."
)
