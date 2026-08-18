"""pages/saving_simulator.py -- Simulator anggaran & target penghematan (interaktif)."""
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from src.analytics import get_saving_scenarios
from src.formatting import format_liter, format_rupiah, format_percentage
from src import ui

ui.inject_global_css()

ui.page_header(
    title="Saving Simulator",
    description="Proyeksi anggaran dan simulasi skenario penghematan solar JICT.",
    context="Semua nilai default di bawah adalah parameter awal — dapat disesuaikan.",
)

# ── Input Parameter ───────────────────────────────────────────────────────
with st.expander("Parameter Simulasi", expanded=True):
    ui.section_header("Variabel Utama")
    c1, c2, c3 = st.columns(3)
    fuel_price = c1.number_input(
        "Harga solar per liter (Rp)", min_value=0.0,
        value=float(config.DEFAULT_FUEL_PRICE_PER_LITER), step=100.0,
    )
    saving_target_pct = c2.number_input(
        "Target penghematan (%)", min_value=0.0, max_value=100.0,
        value=float(config.DEFAULT_SAVING_TARGET_PCT), step=0.1,
    )
    target_throughput = c3.number_input(
        "Target throughput (TEU)", min_value=0.0,
        value=float(config.DEFAULT_TARGET_THROUGHPUT_TEU), step=1000.0,
    )

    ui.section_header("Variabel Evaluasi (Opsional)")
    c4, c5 = st.columns(2)
    actual_teu_input = c4.number_input(
        "Throughput TEU aktual (kosongkan jika belum ada)",
        min_value=0.0, value=0.0, step=1000.0,
    )
    actual_teu = actual_teu_input if actual_teu_input > 0 else None
    target_l_per_teu = c5.number_input(
        "Target L/TEU", min_value=0.0,
        value=float(config.DEFAULT_TARGET_L_PER_TEU), step=0.01,
    )

report     = get_saving_scenarios(
    fuel_price_per_liter=fuel_price,
    saving_target_percentage=saving_target_pct,
    target_throughput_teu=target_throughput,
    actual_teu=actual_teu,
)
scenarios    = report["scenarios"]
l_per_teu_info = report["l_per_teu_info"]

st.markdown("<br>", unsafe_allow_html=True)

# ── KPI Summary ───────────────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    ui.metric_card(
        "Baseline 2025 (Ref)",
        format_liter(report["baseline_total"]),
        "Total konsumsi berdasarkan data historis yang divalidasi.",
        "info",
    )
with c2:
    if l_per_teu_info.get("actual_l_per_teu") is None:
        ui.metric_card(
            "Efisiensi L/TEU",
            "Menunggu Data",
            l_per_teu_info.get("warning", "Throughput TEU aktual belum tersedia."),
            "warning",
        )
    else:
        status_type = "success" if l_per_teu_info["meets_target"] else "danger"
        status_text = "Memenuhi Target" if l_per_teu_info["meets_target"] else "Di Bawah Target"
        ui.metric_card(
            "Efisiensi L/TEU Aktual",
            f"{l_per_teu_info['actual_l_per_teu']:.3f} L/TEU",
            status_text,
            status_type,
        )

st.caption(
    f"Baseline 2026: {config.DEFAULT_CURRENT_L_PER_TEU} L/TEU | "
    f"Target 2027: {config.DEFAULT_TARGET_L_PER_TEU} L/TEU"
)

st.markdown("<br>", unsafe_allow_html=True)

# ── Scenarios ─────────────────────────────────────────────────────────────
ui.section_header("Perbandingan Skenario Penghematan")
cols = st.columns(3)
for col, (_, row) in zip(cols, scenarios.iterrows()):
    with col:
        st.markdown(f"**{row['scenario']}**")
        ui.metric_card("Proyeksi Konsumsi", format_liter(row["projected_consumption"]))
        ui.metric_card("Proyeksi Biaya",    format_rupiah(row["projected_cost"]))
        ui.metric_card("Total Penghematan", format_liter(row["saving_liter"]))
        pct    = row["target_achievement_percentage"]
        status = "success" if pct >= 100 else "warning" if pct >= 50 else "danger"
        ui.metric_card("Pencapaian Target", format_percentage(pct), "", status)

st.markdown("<br>", unsafe_allow_html=True)

# ── Chart & Table ─────────────────────────────────────────────────────────
col1, col2 = st.columns([1, 1])
with col1:
    fig = px.bar(
        scenarios, x="scenario", y="projected_consumption", color="scenario",
        labels={"projected_consumption": "Liter", "scenario": ""},
        color_discrete_map={
            "Baseline (0% Saving)":      "#BFDDF8",
            "Target (Sesuai Input)":     "#3977C8",
            "Optimis (Model Forecast)":  "#22A06B",
        },
    )
    fig = ui.format_chart(fig)
    fig.update_layout(height=340, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.dataframe(scenarios, use_container_width=True, hide_index=True)

st.download_button(
    "Download Scenario Results (CSV)",
    scenarios.to_csv(index=False),
    "saving_simulation_result.csv", "text/csv",
)
