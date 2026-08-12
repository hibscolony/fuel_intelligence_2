"""pages/recommendations.py -- Rekomendasi tindak lanjut berbasis rule engine."""
import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.analytics import get_recommendations
from src.formatting import format_number, priority_color

st.title("Recommendations")
st.caption("Rekomendasi berbasis pola data -- SARAN tindak lanjut, bukan kesimpulan teknis final.")

recommendations = get_recommendations()

if recommendations.empty:
    st.success("Tidak ada rekomendasi yang terpicu saat ini berdasarkan ambang konfigurasi.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Total Rekomendasi", format_number(len(recommendations)))
c2.metric("Prioritas HIGH", format_number((recommendations["priority"] == "HIGH").sum()))
c3.metric("Equipment Terdampak", format_number(recommendations["equipment_id"].nunique()))

st.divider()

col1, col2 = st.columns(2)
with col1:
    st.subheader("Distribusi Prioritas")
    counts = recommendations["priority"].value_counts()
    fig = px.bar(x=counts.index, y=counts.values, color=counts.index,
                 color_discrete_map={p: priority_color(p) for p in counts.index},
                 labels={"x": "", "y": "Jumlah"})
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
    st.plotly_chart(fig, width="stretch")

with col2:
    st.subheader("Distribusi per Peran Penanggung Jawab")
    by_role = recommendations["responsible_role"].value_counts()
    fig2 = px.pie(values=by_role.values, names=by_role.index, hole=0.4)
    fig2.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig2, width="stretch")

st.divider()
st.subheader("Filter Rekomendasi")
fc1, fc2, fc3 = st.columns(3)
priorities = fc1.multiselect("Priority", ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM", "LOW"])
roles = fc2.multiselect("Responsible Role", sorted(recommendations["responsible_role"].unique()))
statuses = fc3.multiselect("Status", sorted(recommendations["status"].unique()))

filtered = recommendations.copy()
if priorities:
    filtered = filtered[filtered["priority"].isin(priorities)]
if roles:
    filtered = filtered[filtered["responsible_role"].isin(roles)]
if statuses:
    filtered = filtered[filtered["status"].isin(statuses)]

st.dataframe(
    filtered[["priority", "equipment_id", "equipment_category", "finding", "evidence",
              "recommended_action", "responsible_role", "target_date", "status"]],
    width="stretch", hide_index=True,
)

st.download_button("Download Recommendation Report (CSV)", recommendations.to_csv(index=False),
                    "recommendation_report.csv", "text/csv")
