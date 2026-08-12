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

st.title("Saving Simulator")
st.caption("Semua nilai default di bawah adalah PARAMETER SIMULASI awal -- ubah sesuai kondisi aktual JICT.")

st.subheader("Input Simulasi")
c1, c2, c3 = st.columns(3)
fuel_price = c1.number_input("Harga solar per liter (Rp)", min_value=0.0,
                              value=float(config.DEFAULT_FUEL_PRICE_PER_LITER), step=100.0)
saving_target_pct = c2.number_input("Target penghematan (%)", min_value=0.0, max_value=100.0,
                                     value=float(config.DEFAULT_SAVING_TARGET_PCT), step=0.1)
target_throughput = c3.number_input("Target throughput (TEU)", min_value=0.0,
                                     value=float(config.DEFAULT_TARGET_THROUGHPUT_TEU), step=1000.0)

c4, c5 = st.columns(2)
actual_teu_input = c4.number_input("Throughput TEU AKTUAL (opsional, kosongkan jika belum ada)",
                                    min_value=0.0, value=0.0, step=1000.0)
actual_teu = actual_teu_input if actual_teu_input > 0 else None
target_l_per_teu = c5.number_input("Target L/TEU", min_value=0.0,
                                    value=float(config.DEFAULT_TARGET_L_PER_TEU), step=0.01)

report = get_saving_scenarios(
    fuel_price_per_liter=fuel_price, saving_target_percentage=saving_target_pct,
    target_throughput_teu=target_throughput, actual_teu=actual_teu,
)
scenarios = report["scenarios"]
l_per_teu_info = report["l_per_teu_info"]

st.metric("Baseline Total 2025 (hasil hitung ulang)", format_liter(report["baseline_total"]))
st.divider()

st.subheader("Perbandingan 3 Skenario")
cols = st.columns(3)
for col, (_, row) in zip(cols, scenarios.iterrows()):
    with col:
        st.markdown(f"#### {row['scenario']}")
        st.metric("Proyeksi Konsumsi", format_liter(row["projected_consumption"]))
        st.metric("Proyeksi Biaya", format_rupiah(row["projected_cost"]))
        st.metric("Penghematan", format_liter(row["saving_liter"]))
        st.metric("Pencapaian Target", format_percentage(row["target_achievement_percentage"]))

fig = px.bar(scenarios, x="scenario", y="projected_consumption", color="scenario",
             labels={"projected_consumption": "Liter", "scenario": ""},
             color_discrete_sequence=["#999999", "#ff7f0e", "#2ca02c"])
fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
st.plotly_chart(fig, width="stretch")

st.dataframe(scenarios, width="stretch", hide_index=True)

st.divider()
st.subheader("Efisiensi L/TEU")
if l_per_teu_info.get("actual_l_per_teu") is None:
    st.warning(l_per_teu_info.get("warning", "Throughput TEU aktual belum tersedia."), icon="⚠️")
else:
    c1, c2 = st.columns(2)
    c1.metric("L/TEU Aktual", f"{l_per_teu_info['actual_l_per_teu']:.3f}")
    c2.metric("Memenuhi Target?", "Ya" if l_per_teu_info["meets_target"] else "Belum")

st.caption(
    f"Baseline 2026: {config.DEFAULT_CURRENT_L_PER_TEU} L/TEU | Target 2027: "
    f"{config.DEFAULT_TARGET_L_PER_TEU} L/TEU | L/TEU HANYA valid dihitung dengan throughput TEU AKTUAL."
)

st.download_button("Download Scenario Results (CSV)", scenarios.to_csv(index=False),
                    "saving_simulation_result.csv", "text/csv")
