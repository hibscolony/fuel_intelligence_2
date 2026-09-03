"""
Konfigurasi terpusat untuk JICT Fuel Intelligence.

Semua threshold di sini adalah PARAMETER SIMULASI awal -- bukan angka baku
teknik/operasional -- dan dirancang agar bisa diubah pengguna (lewat dashboard
Streamlit di tahap selanjutnya) tanpa mengubah kode.

Tidak ada absolute path yang di-hardcode: semua path diturunkan dari lokasi
file ini (Path(__file__).resolve().parent), sehingga proyek tetap portabel
antar komputer/lingkungan.
"""
from pathlib import Path
import os

# ---------------------------------------------------------------------------
# Path proyek
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
# Sumber data pipeline:
# - "hybrid" (DEFAULT): gabungan Excel + UJB. WAJIB dipakai kalau ada
#   kategori alat yang TIDAK lewat sistem dispenser UJB sama sekali (mis.
#   RTGC & genset -- diisi manual pakai truk tangki keliling, bukan drive-in
#   ke pompa, sehingga secara operasional memang tidak akan pernah tercatat
#   di UJB apa pun yang terjadi). Excel tetap jadi satu-satunya sumber untuk
#   kategori itu, UJB jadi sumber untuk kategori yang memang lewat dispenser
#   (mis. Head Truck, Bus).
# - "ujb": murni dari hasil scrape UJB saja (RTGC/genset akan HILANG dari
#   dashboard kalau dipakai -- hanya cocok kalau semua alat memang lewat UJB).
# - "excel": mode lama, murni workbook manual (untuk debug/perbandingan).
DATA_SOURCE_MODE = os.environ.get("FUEL_DATA_SOURCE", "hybrid")

RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

# Lokasi CSV hasil scrape dashboard.ujbgroup.com (lihat ujb_dashboard_scraper.py).
# Diperlakukan sebagai sumber data TAMBAHAN di samping workbook Excel manual --
# bukan menggantikan. Kalau file ini belum ada (scraper belum pernah dijalankan),
# pipeline tetap jalan normal cuma pakai data Excel seperti biasa.
UJB_SCRAPE_PATH = RAW_DATA_DIR / "ujb_scraped_latest.csv"
OUTPUT_DATA_DIR = DATA_DIR / "outputs"
MODELS_DIR = BASE_DIR / "models"

for _d in (RAW_DATA_DIR, PROCESSED_DATA_DIR, OUTPUT_DATA_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Nama file sumber dicari secara fleksibel: prioritas nama resmi, fallback ke
# nama lain yang ditemukan di folder raw/ (mis. varian "(2)", underscore, dst)
# supaya proyek tidak patah hanya karena perbedaan penamaan file upload.
PREFERRED_RAW_FILENAMES = [
    "Laporan Bulanan Pemakian Solar 2025.xls",
    "Laporan Bulanan Pemakian Solar 2025(2).xls",
    "Laporan_Bulanan_Pemakian_Solar_2025.xls",
]


def resolve_raw_workbook_paths() -> list[Path]:
    """Cari SEMUA file workbook solar (.xls/.xlsx) di data/raw/ -- mendukung
    beberapa file sekaligus (mis. laporan 2025 dan 2026 terpisah file).
    Diurutkan berdasarkan nama file supaya urutan parsing konsisten.
    """
    candidates = sorted(RAW_DATA_DIR.glob("*.xls*"))
    if not candidates:
        raise FileNotFoundError(
            f"Tidak ditemukan file workbook (.xls/.xlsx) di {RAW_DATA_DIR}. "
            f"Letakkan file laporan solar di folder tersebut."
        )
    return candidates


def resolve_raw_workbook_path() -> Path:
    """Kompatibilitas mundur: kembalikan SATU file (yang pertama ditemukan
    sesuai PREFERRED_RAW_FILENAMES, atau file .xls/.xlsx pertama secara umum).
    Untuk memuat SEMUA file sekaligus, pakai resolve_raw_workbook_paths().
    """
    for name in PREFERRED_RAW_FILENAMES:
        candidate = RAW_DATA_DIR / name
        if candidate.exists():
            return candidate
    return resolve_raw_workbook_paths()[0]


def get_raw_data_fingerprint() -> str:
    """Hash ringkas (nama file + waktu modifikasi + ukuran) dari SELURUH
    workbook di data/raw/ -- dipakai untuk mendeteksi otomatis kapan file
    sumber baru saja diperbarui (mis. laporan bulanan di-overwrite dengan
    data terbaru), TANPA perlu membaca ulang isi filenya.

    Dipakai oleh app.py untuk auto-refresh: selama fingerprint ini tidak
    berubah, cache pipeline tetap dipakai (murah). Begitu berubah, cache
    di-clear dan seluruh pipeline dihitung ulang dari file yang baru.

    Return "no-raw-files" (bukan exception) jika folder raw/ kosong, supaya
    fungsi ini aman dipanggil terus-menerus oleh fragment auto-refresh tanpa
    membuat dashboard crash saat folder sedang kosong sesaat (mis. di
    tengah proses upload file baru).
    """
    import hashlib
    try:
        paths = resolve_raw_workbook_paths()
    except FileNotFoundError:
        return "no-raw-files"

    parts = []
    for p in sorted(paths):
        try:
            stat = p.stat()
            parts.append(f"{p.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except FileNotFoundError:
            # File sempat terdeteksi glob tapi hilang saat di-stat (race
            # condition saat file sedang di-overwrite) -- lewati, bukan crash.
            continue

    # Ikutkan seluruh state UJB yang memengaruhi selection/coverage supaya
    # perubahan history atau manifest juga membatalkan cache dashboard.
    ujb_state_paths = [
        UJB_SCRAPE_PATH,
        RAW_DATA_DIR / "ujb_history.csv",
        RAW_DATA_DIR / "ujb_coverage_manifest.csv",
        RAW_DATA_DIR / "ujb_filter_diagnostics.json",
    ]
    for ujb_path in ujb_state_paths:
        if not ujb_path.exists():
            continue
        try:
            stat = ujb_path.stat()
            parts.append(f"{ujb_path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except FileNotFoundError:
            pass

    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Parameter simulasi: parsing & data quality
# ---------------------------------------------------------------------------
STATUS_TOKENS = {"FULL", "PM", "GRAHA", "SCRUB", "LELANG", "KURAS"}

# Ambang batas "nilai liter tidak wajar tinggi" per kategori, dihitung dari
# Q3 + OUTLIER_IQR_MULTIPLIER * IQR pada data historis -- bukan angka mutlak.
OUTLIER_IQR_MULTIPLIER = 3.0

# Tahun data yang dianggap valid (dipakai utk memvalidasi tanggal di luar cakupan)
VALID_YEAR = 2025

# Ambang klasifikasi selisih rekonsiliasi (workbook vs hasil hitung ulang)
RECONCILIATION_MATCH_PCT = 0.5          # < ini%  -> MATCH
RECONCILIATION_MINOR_PCT = 3.0          # < ini%  -> MINOR DIFFERENCE
RECONCILIATION_MAJOR_PCT = 10.0         # < ini%  -> MAJOR DIFFERENCE
                                          # >= ini% -> REQUIRES REVIEW

# ---------------------------------------------------------------------------
# Parameter simulasi: forecast monitoring (Tahap 4)
# ---------------------------------------------------------------------------
FORECAST_WAPE_HEALTHY_MAX = 10.0    # %  WAPE <= ini -> HEALTHY
FORECAST_WAPE_MONITOR_MAX = 15.0    # %  WAPE <= ini -> MONITOR, else RETRAIN
FORECAST_ROLLING_WINDOWS = [7, 14, 30]
FORECAST_RESULTS_FILENAME = "forecast_results.csv"  # dicari di data/processed/
# Nama model placeholder dipakai HANYA jika file forecast asli belum tersedia,
# supaya jelas dibedakan dari model forecasting asli pengguna.
FORECAST_PLACEHOLDER_MODEL_NAME = "PLACEHOLDER_SEASONAL_NAIVE_7"

# ---------------------------------------------------------------------------
# Parameter simulasi: anomaly detection (Tahap 5)
# ---------------------------------------------------------------------------
ANOMALY_ROBUST_Z_THRESHOLDS = {"LOW": 2.0, "MEDIUM": 3.0, "HIGH": 4.0, "CRITICAL": 6.0}
ANOMALY_ISOLATION_FOREST_CONTAMINATION = 0.03
ANOMALY_MIN_OBSERVATIONS = 14  # minimum data poin equipment sebelum dianggap layak dianalisis
ANOMALY_ROLLING_MIN_PERIODS = 5  # minimum transaksi historis sebelum rolling median/MAD dipakai
# Lantai (floor) untuk rolling MAD, sbg % dari rolling mean -- mencegah MAD yang
# kebetulan sangat kecil (histori sempit & kebetulan mirip) menghasilkan
# robust z-score yang meledak tak wajar untuk deviasi kecil.
ANOMALY_MAD_FLOOR_PCT = 0.10
ANOMALY_CATEGORY_PEER_Z_THRESHOLD = 3.0  # ambang deviasi terhadap median kategori (robust z)
ANOMALY_ROLLING_WINDOW = 7

# ---------------------------------------------------------------------------
# Parameter simulasi: change-point detection (Tahap 6)
# ---------------------------------------------------------------------------
CHANGE_POINT_MIN_OBSERVATIONS = 30
CHANGE_POINT_PENALTY = 5  # parameter PELT (ruptures)

# ---------------------------------------------------------------------------
# Parameter simulasi: fuel consumption health score (Tahap 7)
# ---------------------------------------------------------------------------
HEALTH_SCORE_WEIGHTS = {
    "anomaly_penalty": 0.20,
    "volatility_penalty": 0.15,
    "trend_penalty": 0.15,
    "change_point_penalty": 0.15,
    "missing_data_penalty": 0.10,
    "reconciliation_penalty": 0.10,
    "forecast_error_penalty": 0.05,
    "repeated_critical_penalty": 0.10,
}
HEALTH_SCORE_BANDS = {"HEALTHY": (85, 100), "MONITOR": (70, 84), "REVIEW": (50, 69), "CRITICAL": (0, 49)}
# Berapa kejadian CRITICAL anomaly (dari Tahap 5) sebelum dianggap "berulang" (bukan insiden tunggal)
HEALTH_REPEATED_CRITICAL_THRESHOLD = 3
# Batas atas trend (% perubahan dari paruh pertama ke paruh kedua masa aktif) yang dianggap
# penalty maksimum (100) -- di atas ini penalty di-cap, bukan terus membesar tanpa batas
HEALTH_TREND_PENALTY_CAP_PCT = 50.0
HEALTH_MIN_OBSERVATIONS = 10  # equipment di bawah ini -> health_status = INSUFFICIENT_DATA

# ---------------------------------------------------------------------------
# Parameter simulasi: equipment segmentation / clustering (Tahap 8)
# ---------------------------------------------------------------------------
CLUSTERING_MIN_OBSERVATIONS = 14   # equipment di bawah ini dikeluarkan dari clustering
CLUSTERING_MIN_EQUIPMENT_PER_CATEGORY = 4  # kategori dgn equipment kurang dari ini -> tidak di-cluster (dilabeli manual)
CLUSTERING_MAX_K = 6
CLUSTERING_INTERMITTENT_ACTIVE_RATIO_MAX = 0.15  # active_day_ratio <= ini -> kandidat INTERMITTENT
CLUSTERING_HIGH_GROWTH_THRESHOLD_PCT = 20.0       # monthly_growth >= ini -> kandidat INCREASING_TREND

# ---------------------------------------------------------------------------
# Parameter simulasi: recommendation engine (Tahap 10)
# ---------------------------------------------------------------------------
REC_LOW_COMPLETENESS_THRESHOLD_PCT = 50.0     # data_completeness di bawah ini -> "validasi data dulu"
REC_HIGH_ANOMALY_COUNT_THRESHOLD = 5
REC_CATEGORY_MEDIAN_EXCESS_RATIO = 1.5        # average_daily_fuel >= median kategori * ini -> layak dibandingkan
REC_TARGET_DATE_OFFSET_DAYS = {"HIGH": 7, "MEDIUM": 14, "LOW": 30}

# ---------------------------------------------------------------------------
# Parameter simulasi: Forecast Explorer -- model selectable & prediksi ke
# tanggal manapun (Tahap 11 lanjutan)
# ---------------------------------------------------------------------------
FORECAST_MODEL_CHOICES = {
    "naive": "Naive (nilai hari terakhir)",
    "seasonal_naive_7": "Seasonal Naive (7 hari)",
    "moving_average_7": "Moving Average 7 Hari",
    "moving_average_30": "Moving Average 30 Hari",
    "holt_winters": "Holt-Winters (Exponential Smoothing)",
    "random_forest": "Random Forest",
    "hist_gradient_boosting": "HistGradientBoosting",
}
# Horizon (hari sejak data terakhir) di mana forecast rekursif MASIH dianggap
# cukup punya dasar. Di atas ini, sistem beralih ke fallback klimatologi
# (rata-rata historis hari-dalam-tahun yang sama) dengan interval jauh lebih
# lebar dan peringatan eksplisit -- karena data historis cuma 1 tahun,
# sehingga pola musiman ANTAR TAHUN belum bisa benar-benar dipelajari.
FORECAST_RELIABLE_HORIZON_DAYS = 180
FORECAST_CLIMATOLOGY_WINDOW_DAYS = 7   # +/- hari di sekitar hari-dalam-tahun yang sama, utk rata-rata klimatologi
FORECAST_BACKTEST_DAYS = 60            # jumlah hari terakhir dipakai utk membangun residual quantile interval
FORECAST_CALIBRATION_FRACTION = 0.60   # origin awal untuk kalibrasi interval; sisanya holdout evaluasi
FORECAST_INTERVAL_LOWER_Q = 0.10       # empirical 80% prediction interval
FORECAST_INTERVAL_UPPER_Q = 0.90
FORECAST_MIN_CALIBRATION_ORIGINS = 5
FORECAST_MIN_EVALUATION_ORIGINS = 3
FORECAST_MIN_MODEL_SELECTION_ORIGINS = 5
FORECAST_MAX_INTERVAL_COVERAGE_GAP_PCT = 20.0
FORECAST_DRIFT_DETERIORATION_RATIO = 1.20
FORECAST_PRODUCTION_MAX_STALENESS_DAYS = 30
FORECAST_PRODUCTION_MIN_LATEST_SEGMENT_DAYS = 60

# Data yang dipakai utk MELATIH/menghitung forecast (Forecast Explorer & backtest
# perbandingan model) dibatasi HANYA sampai tanggal ini -- data sesudahnya (mis.
# tahun 2026) TIDAK ikut jadi "histori" model, supaya prediksi ke tanggal 2026
# betul-betul out-of-sample, bukan diam-diam memakai data 2026 sbg training.
# Data setelah cutoff ini TETAP dipakai penuh di semua analisis LAIN (Executive
# Overview, Konsumsi Detail, Anomaly, Health Score, dst) dan di section
# "Validasi Lintas Tahun" (yang justru sengaja membandingkan forecast dgn
# aktual sesudah cutoff ini).
FORECAST_TRAINING_CUTOFF = "2025-12-31"

# Titik "saturasi" tiap komponen penalty -- nilai mentah >= titik ini dianggap
# penalty maksimum (100). Semua dapat dikalibrasi ulang begitu ada masukan
# operasional; nilai awal diturunkan dari distribusi data 2025 itu sendiri.
HEALTH_ANOMALY_SEVERITY_WEIGHTS = {"LOW": 1, "MEDIUM": 2, "HIGH": 4, "CRITICAL": 8}
HEALTH_ANOMALY_PENALTY_SATURATION = 2.0     # rata-rata bobot anomali per transaksi
HEALTH_REPEATED_CRITICAL_THRESHOLD = 3       # >= ini critical anomaly -> bonus penalty
HEALTH_REPEATED_CRITICAL_BONUS = 15
HEALTH_VOLATILITY_CV_SATURATION = 1.2        # coefficient of variation
HEALTH_TREND_PCT_SATURATION = 50.0           # % kenaikan mean akhir vs awal
HEALTH_CHANGE_POINT_SATURATION = 100.0       # skor mentah change-point (lihat health_score.py)
HEALTH_MISSING_DATA_STREAK_SATURATION_DAYS = 60
HEALTH_RECONCILIATION_PCT_SATURATION = 20.0  # % selisih kategori-bulan

# ---------------------------------------------------------------------------
# Parameter simulasi: saving simulator (Tahap 9) -- default prototipe, bisa diganti user
# ---------------------------------------------------------------------------
DEFAULT_FUEL_PRICE_PER_LITER = 6800.0  # placeholder -- WAJIB diverifikasi ke harga solar industri JICT saat ini
DEFAULT_SAVING_TARGET_LITER = 918_810
DEFAULT_SAVING_TARGET_PCT = 13.44
DEFAULT_CURRENT_L_PER_TEU = 3.05
DEFAULT_TARGET_L_PER_TEU = 2.64
DEFAULT_TARGET_THROUGHPUT_TEU = 2_241_000

# ---------------------------------------------------------------------------
# Parameter simulasi: data quality & reconciliation engine (Tahap 3)
# ---------------------------------------------------------------------------
# Berapa hari berturut-turut tanpa transaksi solar (bukan status FULL/PM/dst)
# sebelum sebuah equipment ditandai "zero consumption terlalu lama"
ZERO_CONSUMPTION_STREAK_DAYS = 14

# Ambang KPI keseluruhan untuk status data quality PASS/REVIEW/FAILED
DQ_VALID_PCT_PASS_MIN = 97.0     # valid_transaction_percentage >= ini -> kandidat PASS
DQ_VALID_PCT_REVIEW_MIN = 90.0   # >= ini (tapi < PASS)                -> kandidat REVIEW, di bawahnya FAILED
DQ_COMPLETENESS_PASS_MIN = 85.0  # % equipment TANPA zero-consumption streak panjang >= ini -> kandidat PASS
DQ_COMPLETENESS_REVIEW_MIN = 65.0
DQ_MAX_MAJOR_RECONCILIATION_MONTHS_FOR_PASS = 0   # jumlah bulan REQUIRES REVIEW yang masih ditoleransi utk PASS

# ---------------------------------------------------------------------------
# Parameter simulasi: equipment segmentation / clustering (Tahap 8)
# ---------------------------------------------------------------------------
CLUSTERING_MIN_OBSERVATIONS = 14   # sama dgn ambang anomaly detection -- konsisten
CLUSTERING_K_RANGE = range(2, 9)   # rentang k yang dicoba utk silhouette scan
CLUSTERING_INTERMITTENT_ACTIVE_RATIO = 0.25   # active_day_ratio di bawah ini -> INTERMITTENT
CLUSTERING_GROWTH_THRESHOLD_PCT = 20.0        # monthly_growth rata-rata cluster di atas ini -> INCREASING_TREND

RANDOM_STATE = 42

