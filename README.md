# JICT Fuel Intelligence

Sistem pendukung keputusan untuk operasional solar JICT: forecasting monitoring,
anomaly detection, fuel consumption health scoring, equipment segmentation,
saving simulation, dan recommendation engine — dibungkus dalam dashboard
Streamlit.

> **Status: PROTOTYPE — Data Analytics / Decision Support.**
> Seluruh anomali, health score, dan rekomendasi adalah **indikasi untuk
> pemeriksaan**, bukan diagnosis kerusakan mesin atau vonis pemborosan. Baca
> bagian "Keterbatasan" di bawah sebelum memakai angka apa pun untuk keputusan
> operasional/anggaran.

## Instalasi

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Letakkan file workbook solar (`.xls`/`.xlsx`) di `data/raw/` -- **boleh lebih
dari satu file** (mis. laporan 2025 dan 2026 terpisah file). Semua file
`.xls`/`.xlsx` di folder tersebut otomatis dimuat & digabung, dengan tahun
dibaca otomatis dari nama tiap sheet ("JANUARI 2026", dst) -- bukan
diasumsikan satu tahun tetap.

## Menjalankan Dashboard

```bash
streamlit run app.py
```

Buka `http://localhost:8501`. Delapan halaman tersedia lewat sidebar: Executive
Overview, **Konsumsi Detail** (drill-down: total/per kategori/per unit,
bulanan/harian, dengan dropdown & rentang tanggal), Forecast Monitoring,
Fuel Anomaly, Equipment Health, Data Quality, Saving Simulator, Recommendations.

## Menjalankan Pipeline Tanpa Dashboard (CLI)

Tiap modul di `src/` bisa dijalankan langsung untuk debugging/CI:

```bash
python -m src.data_loader          # parsing mentah
python -m src.data_cleaning        # cleaning + reconciliation
python -m src.data_quality         # KPI & zero-consumption streak
python -m src.forecast_integration # monitoring forecast (placeholder jika belum ada model asli)
python -m src.anomaly_detection
python -m src.change_point
python -m src.health_score
python -m src.clustering
python -m src.saving_simulator
python -m src.recommendation_engine
```

Semua output tersimpan ke `data/processed/`.

## Menjalankan Test

```bash
pytest tests/ -v
```

82 test mencakup parsing, cleaning, reconciliation, data quality, forecast
integration, anomaly detection, change-point detection, health score,
clustering, saving simulator, recommendation engine, dan smoke test tiap
halaman dashboard (`streamlit.testing.v1.AppTest`).

## Model Forecasting: Data Training vs Data Validasi

Secara default, **Forecast Explorer** dan **perbandingan model (backtest)** di
halaman Forecast Monitoring HANYA dilatih dari data sampai
`config.FORECAST_TRAINING_CUTOFF` (default: `2025-12-31`). Data sesudah
cutoff ini (mis. tahun 2026) TIDAK ikut jadi dasar training -- supaya
prediksi ke tanggal setelahnya betul-betul out-of-sample, bukan diam-diam
"mengintip" data yang sudah terjadi. Ubah `FORECAST_TRAINING_CUTOFF` di
`config.py` kalau ingin memasukkan data tahun berikutnya ke training di
kemudian hari.

Data sesudah cutoff tetap dipakai PENUH di semua bagian lain (Executive
Overview, Konsumsi Detail, Fuel Anomaly, Equipment Health, dst) dan khusus
di section **"Validasi Lintas Tahun"** (yang justru sengaja membandingkan
forecast dengan data aktual sesudah cutoff, sebagai uji out-of-sample).

## Mengintegrasikan Model Forecasting Asli

Model forecasting **tidak dibangun ulang** oleh sistem ini. Simpan hasil
model Anda sebagai `data/processed/forecast_results.csv` dengan skema:

```
date, actual_fuel, forecast_fuel, lower_interval, upper_interval, model_name
```

Selama file ini belum ada, `load_forecast_results()` otomatis memakai
**placeholder** (seasonal-naive 7 hari, `model_name` diberi label jelas)
supaya seluruh pipeline monitoring tetap bisa diuji. Ganti file ini kapan pun
— dashboard akan otomatis memakainya di run berikutnya.

## Struktur Proyek

```
jict_fuel_intelligence/
├── app.py                     # entry point dashboard
├── config.py                  # SEMUA path & threshold (parameter simulasi)
├── requirements.txt
├── data/
│   ├── raw/                   # workbook .xls asli
│   ├── processed/             # seluruh output pipeline (csv)
│   └── outputs/
├── src/                       # modul-modul pipeline (lihat daftar di atas)
├── pages/                     # 7 halaman dashboard
└── tests/                     # 82 unit/smoke test
```

## Keterbatasan & Data Tambahan yang Direkomendasikan

- **Angka liter = catatan PENGISIAN solar (refueling), bukan konsumsi mesin
  real-time.** Tidak ada operating hour, idle time, trip, jarak tempuh, RTG
  move, atau data TEU dalam workbook sumber.
- **Reconciliation & forecast_error penalty di health score adalah PROKSI
  level kategori/armada**, bukan per unit — workbook tidak menyediakan
  subtotal per equipment individual.
- **L/TEU hanya valid jika throughput TEU aktual dimasukkan** — tanpa itu,
  semua angka L/TEU adalah proyeksi berbasis asumsi target.
- **Harga solar per liter (`config.DEFAULT_FUEL_PRICE_PER_LITER`) adalah
  placeholder** — wajib diverifikasi sebelum dipakai untuk keputusan anggaran.
- **77 pasangan equipment ID** terdeteksi kemungkinan varian penulisan yang
  sama (lihat `equipment_master.csv`, kolom `id_variants`) — belum
  digabungkan otomatis, perlu verifikasi manual.
- Data tambahan yang akan MENINGKATKAN AKURASI signifikan: operating hour,
  idle hour, trip IHT, jarak tempuh, RTG move, jumlah alat aktif, breakdown
  log, delay, produktivitas QC, dan throughput TEU aktual per hari.
