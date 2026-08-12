"""
app.py
======
Entry point JICT Fuel Intelligence Dashboard. Jalankan dengan:

    streamlit run app.py

Navigasi 7 halaman didefinisikan eksplisit lewat st.navigation/st.Page
(bukan auto-discovery folder pages/) supaya urutan & label tab terkontrol
penuh terlepas dari nama file atau versi Streamlit yang dipakai.
"""
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config

st.set_page_config(
    page_title="JICT Fuel Intelligence Dashboard",
    page_icon="\u26fd",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
:root {
    --jict-navy: #0b2545;
    --jict-cyan: #13c2c2;
    --jict-green: #2ca02c;
    --jict-orange: #ff7f0e;
    --jict-red: #d62728;
}
[data-testid="stSidebar"] { background-color: var(--jict-navy); }
[data-testid="stSidebar"] * { color: #f0f4f8 !important; }
.jict-header {
    background: linear-gradient(90deg, var(--jict-navy) 0%, #123a63 100%);
    padding: 1.25rem 1.5rem; border-radius: 10px; margin-bottom: 1rem;
    display: flex; align-items: center; justify-content: space-between;
}
.jict-header h1 { color: white; margin: 0; font-size: 1.6rem; }
.jict-badge {
    background: var(--jict-cyan); color: #062b2b; font-weight: 700;
    padding: 0.3rem 0.75rem; border-radius: 999px; font-size: 0.75rem;
    letter-spacing: 0.03em;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
st.markdown(
    """
    <div class="jict-header">
        <h1>&#9878;&#65039; JICT Fuel Intelligence Dashboard</h1>
        <span class="jict-badge">PROTOTYPE &mdash; DATA ANALYTICS / DECISION SUPPORT</span>
    </div>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# Auto-refresh: dashboard mendeteksi sendiri kapan file sumber (laporan
# solar di data/raw/) diperbarui, lalu meng-invalidasi cache & memuat ulang
# pipeline secara otomatis -- tanpa perlu restart Streamlit manual.
#
# Deteksi memakai fingerprint (nama file + waktu modifikasi + ukuran), BUKAN
# polling isi file penuh, supaya cek berkala ini murah walau pipeline di
# baliknya berat (parsing Excel + cleaning + anomaly + forecast + clustering
# dst). Pipeline berat hanya dijalankan ulang saat fingerprint benar-benar
# berubah, bukan tiap interval.
# =============================================================================
if "raw_data_fingerprint" not in st.session_state:
    st.session_state.raw_data_fingerprint = config.get_raw_data_fingerprint()
if "last_refreshed_at" not in st.session_state:
    st.session_state.last_refreshed_at = datetime.now()
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True

with st.sidebar:
    st.divider()
    st.toggle(
        "Auto-refresh data",
        key="auto_refresh_enabled",
        help="Cek otomatis tiap 20 detik apakah file laporan solar di data/raw/ berubah. "
             "Jika berubah, seluruh dashboard dihitung ulang otomatis.",
    )
    if st.button("\U0001F504 Refresh Sekarang", use_container_width=True):
        st.cache_data.clear()
        st.session_state.raw_data_fingerprint = config.get_raw_data_fingerprint()
        st.session_state.last_refreshed_at = datetime.now()
        st.rerun()
    st.caption(f"Data terakhir dimuat: {st.session_state.last_refreshed_at.strftime('%H:%M:%S')}")


@st.fragment(run_every="20s")
def _watch_raw_data_changes():
    """Fragment ringan yang berjalan sendiri tiap 20 detik, terpisah dari
    interaksi pengguna. Hanya melakukan file-stat check yang murah -- kalau
    fingerprint tidak berubah, tidak melakukan apa-apa lagi."""
    if not st.session_state.auto_refresh_enabled:
        return
    current_fp = config.get_raw_data_fingerprint()
    if current_fp != st.session_state.raw_data_fingerprint:
        st.session_state.raw_data_fingerprint = current_fp
        st.session_state.last_refreshed_at = datetime.now()
        st.cache_data.clear()
        st.toast("Data sumber baru terdeteksi -- dashboard diperbarui otomatis.", icon="\U0001F504")
        st.rerun()  # trigger full-app rerun supaya seluruh halaman ikut refresh


_watch_raw_data_changes()

pages = [
    st.Page("pages/executive_overview.py", title="Executive Overview", icon="\U0001F4CA", default=True),
    st.Page("pages/consumption_explorer.py", title="Konsumsi Detail", icon="\U0001F50D"),
    st.Page("pages/forecast_monitoring.py", title="Forecast Monitoring", icon="\U0001F4C8"),
    st.Page("pages/anomaly_monitoring.py", title="Fuel Anomaly", icon="\u26A0\uFE0F"),
    st.Page("pages/equipment_health.py", title="Equipment Health", icon="\U0001FA7A"),
    st.Page("pages/data_quality.py", title="Data Quality", icon="\U0001F50E"),
    st.Page("pages/saving_simulator.py", title="Saving Simulator", icon="\U0001F4B0"),
    st.Page("pages/recommendations.py", title="Recommendations", icon="\u2705"),
]

nav = st.navigation(pages)
nav.run()
