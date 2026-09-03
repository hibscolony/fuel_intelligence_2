"""Operator-facing source upload, validation, activation, and audit trail."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from src import ui
from src.data_update import (
    activate_excel, activate_ujb, load_update_log, validate_excel, validate_ujb,
)
from src.persistent_cache import invalidate_all


@st.cache_data(show_spinner=False)
def _validate_upload(kind: str, filename: str, content: bytes) -> dict:
    return validate_excel(content, filename) if kind == "Excel" else validate_ujb(content, filename)


ui.inject_global_css()
ui.page_header(
    "Pembaruan Data",
    "Unggah, periksa, dan aktifkan laporan baru tanpa membuka folder proyek atau GitHub.",
    "File aktif hanya berubah setelah validasi berhasil dan operator memberikan konfirmasi.",
)

st.info(
    "**Alur aman:** pilih sumber → upload → periksa preview → konfirmasi → aktifkan. "
    "File aktif sebelumnya dipindahkan ke arsip dan riwayat pembaruan dicatat.",
    icon="🛡️",
)

ui.section_header("File sumber aktif")
active_files = []
for path in sorted(config.RAW_DATA_DIR.glob("*")):
    if path.is_file() and path.suffix.lower() in {".xls", ".xlsx", ".csv"}:
        active_files.append({
            "File": path.name,
            "Ukuran": f"{path.stat().st_size / 1024:,.0f} KB",
            "Diubah": datetime.fromtimestamp(path.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
        })
st.dataframe(pd.DataFrame(active_files), width="stretch", hide_index=True)

ui.section_header("Upload dan validasi")
kind = st.radio(
    "Jenis sumber",
    ["Excel", "UJB"],
    horizontal=True,
    help="Excel untuk laporan solar bulanan; UJB untuk CSV transformed hasil scraper.",
)
extensions = ["xls", "xlsx"] if kind == "Excel" else ["csv"]
uploaded = st.file_uploader(
    f"Pilih file {kind}",
    type=extensions,
    help=(
        "Workbook harus memiliki sheet bulanan yang dikenali pipeline."
        if kind == "Excel"
        else "CSV harus memiliki skema transformed UJB termasuk source_event_key."
    ),
)

if uploaded is not None:
    content = uploaded.getvalue()
    try:
        with st.spinner("Memeriksa struktur dan isi file..."):
            validation = _validate_upload(kind, uploaded.name, content)
    except ValueError as exc:
        st.error(f"Validasi gagal: {exc}", icon="❌")
    except Exception as exc:
        st.error(f"File tidak dapat divalidasi: {exc}", icon="❌")
    else:
        st.success("Struktur file valid dan siap ditinjau.", icon="✅")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Jumlah baris", f"{validation['rows']:,}")
        c2.metric("Jumlah alat", f"{validation['equipment_count']:,}")
        c3.metric("Tanggal awal", validation["date_min"].strftime("%d/%m/%Y"))
        c4.metric("Tanggal akhir", validation["date_max"].strftime("%d/%m/%Y"))
        st.caption("Tahun terdeteksi: " + ", ".join(map(str, validation["years"])))
        st.dataframe(validation["preview"], width="stretch", hide_index=True)

        if kind == "Excel":
            st.warning(
                "Saat diaktifkan, workbook aktif dengan tahun yang sama akan dipindahkan ke "
                "`data/archive/raw/` agar transaksi tidak terhitung ganda.",
                icon="⚠️",
            )
        else:
            st.warning(
                "Snapshot UJB aktif akan diarsipkan. Event baru digabung ke histori menggunakan "
                "`source_event_key`, sehingga event yang sama tidak digandakan.",
                icon="⚠️",
            )

        confirmed = st.checkbox(
            "Saya sudah memeriksa periode, jumlah baris, dan preview di atas.",
            key=f"confirm_{kind}_{uploaded.name}_{len(content)}",
        )
        if st.button("Aktifkan sebagai data sumber", type="primary", disabled=not confirmed):
            try:
                with st.spinner("Mengarsipkan sumber lama dan mengaktifkan file baru..."):
                    record = (
                        activate_excel(content, validation)
                        if kind == "Excel"
                        else activate_ujb(content, validation)
                    )
                    invalidate_all()
                    st.cache_data.clear()
                    st.session_state.raw_data_fingerprint = config.get_raw_data_fingerprint()
                    st.session_state.last_refreshed_at = datetime.now()
            except Exception as exc:
                st.error(f"Aktivasi dibatalkan: {exc}", icon="❌")
            else:
                st.success(
                    f"{record['filename']} berhasil diaktifkan. Dashboard akan menghitung ulang "
                    "analitik saat halaman operasional dibuka.",
                    icon="✅",
                )

ui.section_header("Riwayat pembaruan")
update_log = load_update_log()
if update_log.empty:
    st.caption("Belum ada pembaruan yang dilakukan melalui halaman ini.")
else:
    display_log = update_log.sort_values("activated_at", ascending=False).rename(columns={
        "activated_at": "Waktu aktivasi",
        "kind": "Sumber",
        "filename": "File aktif",
        "rows": "Baris",
        "date_min": "Tanggal awal",
        "date_max": "Tanggal akhir",
        "archived_files": "File diarsipkan",
        "new_unique_rows": "Event baru",
    })
    st.dataframe(
        display_log[[
            "Waktu aktivasi", "Sumber", "File aktif", "Baris", "Tanggal awal",
            "Tanggal akhir", "File diarsipkan", "Event baru",
        ]],
        width="stretch",
        hide_index=True,
    )
