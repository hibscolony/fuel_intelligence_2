"""
forecast_data.py
================
Pembentukan target harian khusus forecasting.

Target yang diprediksi adalah TOTAL LITER PENGISIAN SOLAR TERCATAT per hari,
bukan konsumsi mesin real-time. Modul ini sengaja memisahkan tiga keadaan:

1. Ada transaksi numerik layak pada hari tsb -> jumlahkan liter.
2. Ada baris sumber pada hari tsb tetapi tidak ada transaksi numerik layak
   (mis. hanya STATUS_ONLY) -> 0 liter pengisian tercatat.
3. Tidak ada baris sumber SAMA SEKALI pada suatu hari di tengah rentang data ->
   coverage gap / data tidak diketahui. Dalam mode strict, kondisi ini
   menimbulkan ValueError agar tidak diam-diam diubah menjadi 0 atau di-drop,
   karena itu akan menggeser arti lag kalender (lag_7 harus benar-benar 7 hari).

Baris INVALID_DATE dan nilai negatif tidak dipakai. Baris yang ditandai
DUPLICATE direduksi menjadi satu record per (tanggal, kategori, equipment_id),
sesuai asumsi struktur workbook bahwa satu unit mempunyai satu sel pengisian
per hari.
"""
from __future__ import annotations

import pandas as pd


_FORECAST_DEDUP_KEYS = ["date", "equipment_category", "equipment_id"]


def _validate_columns(cleaned: pd.DataFrame) -> None:
    required = {"date", "equipment_category", "equipment_id", "fuel_liter", "data_status"}
    missing = sorted(required.difference(cleaned.columns))
    if missing:
        raise ValueError(f"Kolom wajib untuk membangun deret forecast tidak tersedia: {missing}")


def build_daily_refueling_series(cleaned: pd.DataFrame,
                                  strict_source_coverage: bool = True) -> pd.Series:
    """Bangun deret harian total liter pengisian yang aman untuk forecasting.

    Parameters
    ----------
    cleaned:
        Output ``cleaned_fuel_data`` dari pipeline cleaning.
    strict_source_coverage:
        Jika True (default), hari tanpa BARIS SUMBER sama sekali di tengah
        rentang data dianggap unresolved coverage gap dan memicu ValueError.
        Jika False, hari tersebut dipertahankan sebagai NaN. Jangan ``dropna``
        sebelum membuat lag/rolling karena akan mengubah lag kalender menjadi
        lag berbasis urutan observasi.

    Returns
    -------
    pd.Series
        DatetimeIndex harian reguler. Hari yang memiliki baris sumber tetapi
        tidak memiliki liter numerik layak diisi 0.0. Hari tanpa coverage
        sumber tetap NaN bila ``strict_source_coverage=False``.
    """
    _validate_columns(cleaned)

    df = cleaned.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    source_rows = df[(df["date"].notna()) & (df["data_status"] != "INVALID_DATE")].copy()
    if source_rows.empty:
        raise ValueError("Tidak ada baris bertanggal valid untuk membangun deret forecast.")

    # Forecast bekerja pada kalender HARIAN; timestamp intraday dari sumber
    # berbeda harus terlebih dulu dipetakan ke tanggal kalender yang sama.
    source_rows["date"] = source_rows["date"].dt.normalize()

    first_date = source_rows["date"].min()
    last_date = source_rows["date"].max()
    full_index = pd.date_range(first_date, last_date, freq="D")

    # Coverage sumber: keberadaan baris apa pun pada hari tersebut. Ini berbeda
    # dari keberadaan transaksi liter numerik.
    daily_source_rows = source_rows.groupby("date").size()
    daily_source_rows = daily_source_rows.reindex(full_index, fill_value=0)
    source_gap_dates = daily_source_rows.index[daily_source_rows.eq(0)]

    if strict_source_coverage and len(source_gap_dates) > 0:
        sample = ", ".join(d.strftime("%Y-%m-%d") for d in source_gap_dates[:5])
        suffix = "..." if len(source_gap_dates) > 5 else ""
        raise ValueError(
            f"Ditemukan {len(source_gap_dates)} hari tanpa baris sumber di tengah rentang data "
            f"({sample}{suffix}). Forecast dihentikan agar hari yang datanya tidak diketahui "
            f"tidak dianggap 0 dan lag kalender tidak bergeser. Tinjau data quality/source coverage dulu."
        )

    numeric = source_rows.copy()
    numeric["fuel_liter"] = pd.to_numeric(numeric["fuel_liter"], errors="coerce")
    numeric = numeric[
        numeric["fuel_liter"].notna()
        & (numeric["fuel_liter"] >= 0)
        & ~numeric["data_status"].isin(["NEGATIVE_VALUE"])
    ].copy()

    # Cleaning menandai seluruh anggota duplicate group sebagai DUPLICATE
    # (keep=False). Untuk target forecast kita butuh satu nilai kanonik, bukan
    # menjumlahkan semua salinan dan bukan pula membuang seluruh grup.
    sort_cols = [c for c in ["date", "equipment_category", "equipment_id", "source_file", "source_row"]
                 if c in numeric.columns]
    numeric = numeric.sort_values(sort_cols)
    numeric = numeric.drop_duplicates(subset=_FORECAST_DEDUP_KEYS, keep="first")

    daily_fuel = numeric.groupby("date")["fuel_liter"].sum()
    series = daily_fuel.reindex(full_index)

    # Bila sumber hadir tetapi tak ada transaksi numerik layak, target yang
    # teramati adalah 0 liter pengisian tercatat. Hanya source gap yang tetap NaN.
    observed_source_day = daily_source_rows.gt(0)
    series.loc[observed_source_day & series.isna()] = 0.0
    series.name = "fuel_liter"
    series.index.name = "date"
    return series.astype(float)


def build_forecast_calendar_audit(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Ringkasan status kalender harian untuk audit sebelum forecasting.

    ``OBSERVED_REFUEL``: ada >=1 transaksi numerik layak.
    ``OBSERVED_ZERO_REFUEL``: sumber hadir, tetapi tidak ada liter numerik layak.
    ``SOURCE_COVERAGE_GAP``: tidak ada baris sumber sama sekali; nilai tidak diketahui.
    """
    series = build_daily_refueling_series(cleaned, strict_source_coverage=False)

    df = cleaned.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    source_rows = df[(df["date"].notna()) & (df["data_status"] != "INVALID_DATE")].copy()
    source_rows["date"] = source_rows["date"].dt.normalize()
    counts = source_rows.groupby("date").size().reindex(series.index, fill_value=0)

    status = pd.Series("OBSERVED_REFUEL", index=series.index, dtype="object")
    status.loc[series.eq(0) & counts.gt(0)] = "OBSERVED_ZERO_REFUEL"
    status.loc[counts.eq(0)] = "SOURCE_COVERAGE_GAP"

    return pd.DataFrame({
        "date": series.index,
        "fuel_liter": series.values,
        "source_row_count": counts.values,
        "calendar_status": status.values,
    })
