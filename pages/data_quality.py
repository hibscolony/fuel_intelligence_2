"""pages/data_quality.py -- Audit kualitas data & rekonsiliasi."""
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_cleaning_result, get_data_quality
from src.formatting import format_number, format_percentage

st.title("Data Quality")
st.caption("Audit otomatis kelengkapan & konsistensi data pengisian solar, termasuk rekonsiliasi terhadap total workbook.")

cleaning = get_cleaning_result()
dq = get_data_quality()
kpis = dq["kpis"]

status_color = {"PASS": "green", "REVIEW": "orange", "FAILED": "red"}.get(kpis.overall_status, "gray")
st.markdown(f"## Status Keseluruhan: :{status_color}[{kpis.overall_status}]")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Data Completeness", format_percentage(kpis.data_completeness_percentage),
          help="% equipment TANPA jeda tak wajar panjang di tengah masa aktifnya")
c2.metric("Valid Transaction %", format_percentage(kpis.valid_transaction_percentage))
c3.metric("Duplicate Count", format_number(kpis.duplicate_count))
c4.metric("Invalid Value Count", format_number(kpis.invalid_value_count))

c5, c6, c7, c8 = st.columns(4)
c5.metric("Unusually High Count", format_number(kpis.unusually_high_count))
c6.metric("Invalid Date Count", format_number(kpis.invalid_date_count))
c7.metric("Bulan Selisih Signifikan", format_number(kpis.n_months_major_reconciliation_issue))
c8.metric("Status Rekonsiliasi", kpis.reconciliation_status)

st.divider()

st.subheader("Workbook Total vs Recalculated Total (Bulanan)")
monthly = cleaning["monthly_reconciliation"]
fig = px.bar(monthly, x="month_name", y=["workbook_reported_total", "recalculated_total"],
             barmode="group", labels={"value": "Liter", "month_name": ""})
fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend_title="")
st.plotly_chart(fig, width="stretch")
st.dataframe(monthly, width="stretch", hide_index=True)

st.subheader("Rekonsiliasi per Kategori per Bulan")
st.dataframe(
    cleaning["category_monthly_reconciliation"].sort_values(
        ["validation_status"], key=lambda s: s.map(
            {"REQUIRES REVIEW": 0, "MAJOR DIFFERENCE": 1, "MINOR DIFFERENCE": 2, "MATCH": 3, "NO_WORKBOOK_VALUE": 4})),
    width="stretch", hide_index=True,
)

st.divider()

col1, col2 = st.columns(2)
with col1:
    st.subheader("Ringkasan Isu Data (data_quality_report)")
    st.dataframe(cleaning["data_quality_report"], width="stretch", hide_index=True)

with col2:
    st.subheader("Equipment dengan Jeda Panjang (Zero-Consumption Streak)")
    st.dataframe(
        dq["zero_streaks"][["equipment_category", "equipment_id", "longest_gap_days",
                            "longest_gap_start", "longest_gap_end"]].head(20),
        width="stretch", hide_index=True,
    )

st.download_button("Download Data Quality Report (CSV)",
                    cleaning["data_quality_report"].to_csv(index=False),
                    "data_quality_report.csv", "text/csv")
