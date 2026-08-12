"""pages/consumption_explorer.py -- Drill-down konsumsi solar: total bulanan,
per jenis alat bulanan, per unit alat bulanan, dan per unit alat harian.
Satu halaman fleksibel dgn dropdown kategori/unit + rentang tanggal +
granularitas, supaya keempat tampilan yang diminta bisa dicapai lewat
kombinasi filter, bukan 4 halaman terpisah.
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_cleaning_result
from src.formatting import format_liter, format_number

st.title("Konsumsi Detail")
st.caption(
    "Telusuri pemakaian solar: total, per jenis alat, atau per unit -- bulanan maupun harian. "
    "Atur lewat dropdown dan rentang tanggal di bawah."
)

cleaning = get_cleaning_result()
valid = cleaning["cleaned_fuel_data"][cleaning["cleaned_fuel_data"]["data_status"] != "INVALID_DATE"].copy()

# =============================================================================
# FILTER
# =============================================================================
st.subheader("Filter")
fc1, fc2, fc3, fc4 = st.columns([2, 2, 3, 2])

category_options = ["Semua Kategori"] + sorted(valid["equipment_category"].unique())
selected_category = fc1.selectbox("Kategori Alat", category_options)

if selected_category == "Semua Kategori":
    equipment_pool = valid
else:
    equipment_pool = valid[valid["equipment_category"] == selected_category]

equipment_options = ["Semua Unit"] + sorted(equipment_pool["equipment_id"].unique())
selected_equipment = fc2.selectbox("Unit Alat (Equipment ID)", equipment_options)

min_date, max_date = valid["date"].min().date(), valid["date"].max().date()
date_range = fc3.date_input("Rentang Tanggal", value=(min_date, max_date),
                             min_value=min_date, max_value=max_date)

granularity = fc4.radio("Granularitas", ["Bulanan", "Harian"], horizontal=True)

# --- Terapkan filter -----------------------------------------------------
filtered = valid.copy()
if selected_category != "Semua Kategori":
    filtered = filtered[filtered["equipment_category"] == selected_category]
if selected_equipment != "Semua Unit":
    filtered = filtered[filtered["equipment_id"] == selected_equipment]
if isinstance(date_range, tuple) and len(date_range) == 2:
    filtered = filtered[(filtered["date"] >= pd.Timestamp(date_range[0]))
                        & (filtered["date"] <= pd.Timestamp(date_range[1]))]

if filtered.empty:
    st.warning("Tidak ada data untuk kombinasi filter ini.")
    st.stop()

st.divider()

# =============================================================================
# RINGKASAN
# =============================================================================
total_liter = filtered["fuel_liter"].sum()
n_equipment = filtered["equipment_id"].nunique()
n_transaksi = len(filtered)
avg_per_transaksi = filtered["fuel_liter"].mean()

s1, s2, s3, s4 = st.columns(4)
s1.metric("Total Solar (Filter)", format_liter(total_liter))
s2.metric("Jumlah Unit Tercakup", format_number(n_equipment))
s3.metric("Jumlah Transaksi", format_number(n_transaksi))
s4.metric("Rata-rata per Transaksi", format_liter(avg_per_transaksi))

label_scope = selected_equipment if selected_equipment != "Semua Unit" else (
    selected_category if selected_category != "Semua Kategori" else "Seluruh Alat")
st.subheader(f"Pemakaian {granularity} -- {label_scope}")

# =============================================================================
# AGREGASI SESUAI GRANULARITAS
# =============================================================================
if granularity == "Bulanan":
    if selected_equipment != "Semua Unit" or selected_category != "Semua Kategori":
        # per unit / per kategori: satu deret garis
        agg = filtered.groupby(filtered["date"].dt.to_period("M"))["fuel_liter"].sum().reset_index()
        agg["period_label"] = agg["date"].astype(str)
        fig = px.bar(agg, x="period_label", y="fuel_liter",
                     labels={"fuel_liter": "Liter", "period_label": "Bulan"})
    else:
        # semua kategori: stacked bar per kategori per bulan
        agg = filtered.groupby([filtered["date"].dt.to_period("M"), "equipment_category"])["fuel_liter"] \
            .sum().reset_index()
        agg["period_label"] = agg["date"].astype(str)
        fig = px.bar(agg, x="period_label", y="fuel_liter", color="equipment_category",
                     labels={"fuel_liter": "Liter", "period_label": "Bulan", "equipment_category": "Kategori"})
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    table = agg.drop(columns=["date"]).rename(columns={"period_label": "Bulan", "fuel_liter": "Total Liter"})
    table["Total Liter"] = table["Total Liter"].round(0)
    st.dataframe(table, width="stretch", hide_index=True)
    download_df = table

else:  # Harian
    if selected_equipment != "Semua Unit" or (selected_category != "Semua Kategori" and n_equipment == 1):
        agg = filtered.groupby("date")["fuel_liter"].sum().reset_index()
        fig = px.line(agg, x="date", y="fuel_liter", markers=True,
                      labels={"fuel_liter": "Liter", "date": "Tanggal"})
    elif selected_category != "Semua Kategori":
        agg = filtered.groupby(["date", "equipment_id"])["fuel_liter"].sum().reset_index()
        fig = px.line(agg, x="date", y="fuel_liter", color="equipment_id",
                      labels={"fuel_liter": "Liter", "date": "Tanggal", "equipment_id": "Unit"})
    else:
        agg = filtered.groupby(["date", "equipment_category"])["fuel_liter"].sum().reset_index()
        fig = px.line(agg, x="date", y="fuel_liter", color="equipment_category",
                      labels={"fuel_liter": "Liter", "date": "Tanggal", "equipment_category": "Kategori"})
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, width="stretch")

    table = filtered.groupby("date")["fuel_liter"].sum().reset_index().rename(
        columns={"date": "Tanggal", "fuel_liter": "Total Liter"})
    table["Total Liter"] = table["Total Liter"].round(1)
    st.dataframe(table.sort_values("Tanggal", ascending=False), width="stretch", hide_index=True)
    download_df = table

st.download_button(
    f"Download Data {granularity} (CSV)", download_df.to_csv(index=False),
    f"konsumsi_{granularity.lower()}_{selected_category}_{selected_equipment}.csv".replace(" ", "_"),
    "text/csv",
)

st.divider()

# =============================================================================
# PERBANDINGAN ANTAR UNIT DALAM SATU KATEGORI -- deteksi fluktuasi/anomali
# X-axis = tiap unit alat dalam kategori terpilih, Y-axis = total liter pada
# periode terpilih (akumulasi jika rentang tanggal, atau nilai hari itu saja
# jika tanggal tunggal dipilih).
# =============================================================================
st.subheader("🔎 Perbandingan Antar Unit dalam Kategori")
st.caption(
    "Bandingkan unit-unit dalam satu kategori berdampingan, supaya fluktuasi atau unit yang "
    "menyimpang kelihatan langsung. Pilih rentang tanggal untuk lihat akumulasi, atau satu "
    "tanggal saja untuk mengecek anomali di hari tertentu."
)

cat_options_no_all = sorted(valid["equipment_category"].unique())
uc1, uc2 = st.columns([2, 3])
compare_category = uc1.selectbox(
    "Kategori Alat", cat_options_no_all,
    index=cat_options_no_all.index(selected_category) if selected_category in cat_options_no_all else 0,
    key="compare_category",
)
date_mode = uc2.radio(
    "Mode Tanggal", ["Rentang Tanggal (Akumulasi)", "Tanggal Tertentu (Cek Anomali Harian)"],
    horizontal=True, key="compare_date_mode",
)

cat_data_all_time = valid[valid["equipment_category"] == compare_category]
all_units_in_category = sorted(cat_data_all_time["equipment_id"].unique())

if date_mode == "Rentang Tanggal (Akumulasi)":
    compare_range = st.date_input(
        "Rentang Tanggal", value=(min_date, max_date), min_value=min_date, max_value=max_date,
        key="compare_range",
    )
    if isinstance(compare_range, tuple) and len(compare_range) == 2:
        start_ts, end_ts = pd.Timestamp(compare_range[0]), pd.Timestamp(compare_range[1])
        scoped = cat_data_all_time[(cat_data_all_time["date"] >= start_ts) & (cat_data_all_time["date"] <= end_ts)]
        period_caption = f"{compare_range[0]} s/d {compare_range[1]} (akumulasi)"
    else:
        scoped = cat_data_all_time
        period_caption = "seluruh rentang data"
else:
    compare_date = st.date_input("Tanggal Tertentu", value=max_date, min_value=min_date,
                                  max_value=max_date, key="compare_single_date")
    scoped = cat_data_all_time[cat_data_all_time["date"] == pd.Timestamp(compare_date)]
    period_caption = f"{compare_date} (satu hari)"

# Sertakan unit yang TIDAK punya transaksi di periode ini sbg batang 0 L --
# ketiadaan pengisian pada unit yang biasanya aktif juga temuan yang relevan.
per_unit = scoped.groupby("equipment_id")["fuel_liter"].sum().reindex(all_units_in_category, fill_value=0)
per_unit = per_unit.sort_values(ascending=False).reset_index()
per_unit.columns = ["equipment_id", "fuel_liter"]

if per_unit["fuel_liter"].sum() == 0:
    st.info(f"Tidak ada transaksi solar untuk kategori {compare_category} pada periode ini ({period_caption}).")
else:
    mean_val = per_unit["fuel_liter"].mean()
    std_val = per_unit["fuel_liter"].std() or 0
    upper_flag = mean_val + 2 * std_val
    lower_flag = max(0, mean_val - 2 * std_val)
    per_unit["status"] = per_unit["fuel_liter"].apply(
        lambda v: "Menyimpang Tinggi" if v > upper_flag else
                  ("Menyimpang Rendah" if v < lower_flag and v > 0 else
                   ("Tidak Ada Transaksi" if v == 0 else "Normal")))

    color_map = {"Normal": "#0b2545", "Menyimpang Tinggi": "#d62728",
                "Menyimpang Rendah": "#ff7f0e", "Tidak Ada Transaksi": "#cccccc"}
    fig_units = px.bar(
        per_unit, x="equipment_id", y="fuel_liter", color="status",
        color_discrete_map=color_map,
        labels={"fuel_liter": "Total Liter", "equipment_id": "Unit Alat", "status": "Status"},
    )
    fig_units.add_hline(y=mean_val, line_dash="dot", line_color="gray",
                        annotation_text=f"Rata-rata: {mean_val:,.0f} L")
    fig_units.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10),
                             xaxis_title="", legend=dict(orientation="h", y=1.12))
    st.plotly_chart(fig_units, width="stretch")
    st.caption(f"Kategori **{compare_category}** -- {len(all_units_in_category)} unit -- periode: {period_caption}. "
               f"'Menyimpang' = di luar \u00b12 standar deviasi dari rata-rata unit dalam kategori ini "
               f"pada periode yang sama (indikasi utk diperiksa, bukan kesimpulan pasti).")

    st.dataframe(per_unit.rename(columns={"equipment_id": "Unit", "fuel_liter": "Total Liter (L)",
                                          "status": "Status"}), width="stretch", hide_index=True)
    st.download_button(
        "Download Perbandingan Antar Unit (CSV)", per_unit.to_csv(index=False),
        f"perbandingan_unit_{compare_category}.csv".replace(" ", "_"), "text/csv",
    )

st.caption(
    "Catatan: angka liter adalah catatan PENGISIAN solar (refueling), bukan pengukuran konsumsi "
    "mesin real-time. Data mencakup tahun yang tersedia di data/raw/ (bisa lebih dari satu tahun)."
)
