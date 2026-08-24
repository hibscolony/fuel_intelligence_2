"""
forecast_data.py
================
Pembentukan target harian khusus forecasting.

Target yang diprediksi adalah TOTAL LITER PENGISIAN SOLAR TERCATAT per hari,
bukan konsumsi mesin real-time. Modul ini membedakan tiga keadaan kalender:

1. Ada transaksi numerik layak -> jumlahkan liter.
2. Ada baris sumber tetapi tidak ada transaksi numerik layak -> 0 liter
   pengisian tercatat.
3. Tidak ada baris sumber sama sekali di tengah rentang -> coverage gap /
   nilai tidak diketahui; dalam mode strict forecasting dihentikan.

Coverage gap TIDAK pernah diimputasi menjadi 0/interpolasi. Untuk data hybrid
yang memiliki jeda antar-sumber, deret dapat dipecah menjadi segmen kalender
kontigu yang masing-masing aman dipakai untuk lag/rolling.

Deduplication bersifat source-aware:
- Excel: satu cell unit-hari, jadi salinan (date, category, equipment_id)
  direduksi menjadi satu record kanonik.
- UJB: event stream. Beberapa pengisian unit yang sama pada hari yang sama
  adalah sah dan semuanya dijumlahkan. Hanya source_event_key yang benar-benar
  berulang (atau exact event fallback bila key tidak tersedia) yang direduksi.
"""
from __future__ import annotations

import pandas as pd


_EXCEL_DEDUP_KEYS = ["date", "equipment_category", "equipment_id"]
_UJB_EVENT_FALLBACK_KEYS = [
    "date", "event_time", "equipment_category", "equipment_id", "fuel_liter"
]


def _validate_columns(cleaned: pd.DataFrame) -> None:
    required = {"date", "equipment_category", "equipment_id", "fuel_liter", "data_status"}
    missing = sorted(required.difference(cleaned.columns))
    if missing:
        raise ValueError(f"Kolom wajib untuk membangun deret forecast tidak tersedia: {missing}")


def _source_aware_deduplicate(numeric: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate numeric refueling rows without collapsing valid UJB events."""
    if numeric.empty:
        return numeric

    df = numeric.copy()
    if "source_system" in df.columns:
        systems = df["source_system"].fillna("EXCEL").astype(str).str.upper()
    else:
        # Backward compatibility for historical/test data created before
        # source provenance existed: old long-form semantics are Excel-like.
        systems = pd.Series("EXCEL", index=df.index)

    frames: list[pd.DataFrame] = []

    excel = df.loc[systems.ne("UJB")].copy()
    if not excel.empty:
        sort_cols = [
            c for c in ["date", "equipment_category", "equipment_id", "source_file", "source_row"]
            if c in excel.columns
        ]
        if sort_cols:
            excel = excel.sort_values(sort_cols)
        excel = excel.drop_duplicates(subset=_EXCEL_DEDUP_KEYS, keep="first")
        frames.append(excel)

    ujb = df.loc[systems.eq("UJB")].copy()
    if not ujb.empty:
        if "source_event_key" in ujb.columns:
            keys = ujb["source_event_key"].astype("string")
            keyed_mask = keys.notna() & keys.str.strip().ne("")
        else:
            keyed_mask = pd.Series(False, index=ujb.index)

        keyed = ujb.loc[keyed_mask].copy()
        if not keyed.empty:
            sort_cols = [
                c for c in ["date", "event_time", "source_event_key", "source_row"]
                if c in keyed.columns
            ]
            if sort_cols:
                keyed = keyed.sort_values(sort_cols)
            keyed = keyed.drop_duplicates(subset=["source_event_key"], keep="first")
            frames.append(keyed)

        unkeyed = ujb.loc[~keyed_mask].copy()
        if not unkeyed.empty:
            # If event_time exists, an exact repeated event can safely be
            # collapsed. Without event identity we conservatively KEEP rows:
            # same-day repeated refuels can be legitimate UJB transactions.
            fallback_keys = [c for c in _UJB_EVENT_FALLBACK_KEYS if c in unkeyed.columns]
            has_event_time = (
                "event_time" in unkeyed.columns
                and unkeyed["event_time"].astype("string").fillna("").str.strip().ne("").any()
            )
            if has_event_time and len(fallback_keys) == len(_UJB_EVENT_FALLBACK_KEYS):
                sort_cols = [c for c in ["date", "event_time", "source_row"] if c in unkeyed.columns]
                if sort_cols:
                    unkeyed = unkeyed.sort_values(sort_cols)
                unkeyed = unkeyed.drop_duplicates(subset=fallback_keys, keep="first")
            frames.append(unkeyed)

    if not frames:
        return df.iloc[0:0].copy()

    result = pd.concat(frames, ignore_index=True, sort=False)
    sort_cols = [
        c for c in ["date", "equipment_category", "equipment_id", "event_time", "source_row"]
        if c in result.columns
    ]
    if sort_cols:
        result = result.sort_values(sort_cols, na_position="last")
    return result.reset_index(drop=True)


def build_daily_refueling_series(cleaned: pd.DataFrame,
                                  strict_source_coverage: bool = True) -> pd.Series:
    """Bangun deret harian total liter pengisian yang aman untuk forecasting.

    ``strict_source_coverage=True`` membuat hari tanpa BARIS SUMBER sama sekali
    di tengah rentang memicu ValueError. Jika False, hari tersebut tetap NaN.
    Jangan drop NaN sebelum membuat lag/rolling karena itu mengubah arti lag
    kalender menjadi lag urutan observasi.
    """
    _validate_columns(cleaned)

    df = cleaned.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    source_rows = df[(df["date"].notna()) & (df["data_status"] != "INVALID_DATE")].copy()
    if source_rows.empty:
        raise ValueError("Tidak ada baris bertanggal valid untuk membangun deret forecast.")

    source_rows["date"] = source_rows["date"].dt.normalize()

    first_date = source_rows["date"].min()
    last_date = source_rows["date"].max()
    full_index = pd.date_range(first_date, last_date, freq="D")

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

    numeric = _source_aware_deduplicate(numeric)

    daily_fuel = numeric.groupby("date")["fuel_liter"].sum()
    series = daily_fuel.reindex(full_index)

    observed_source_day = daily_source_rows.gt(0)
    series.loc[observed_source_day & series.isna()] = 0.0
    series.name = "fuel_liter"
    series.index.name = "date"
    return series.astype(float)


def split_complete_daily_segments(series: pd.Series,
                                  min_days: int = 1) -> list[pd.Series]:
    """Pecah deret kalender menjadi blok kontigu tanpa NaN.

    Fungsi ini sengaja TIDAK memakai ``dropna()`` pada seluruh deret. Tanggal
    gap tetap menjadi batas antar-segmen sehingga lag 7 tetap berarti tujuh
    hari kalender di dalam segmen yang benar-benar tercakup sumber.
    """
    if not isinstance(series, pd.Series):
        raise TypeError("series harus berupa pandas Series.")
    if series.empty:
        return []
    if int(min_days) <= 0:
        raise ValueError("min_days harus >= 1.")

    s = series.copy()
    if not isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index)
    s = s.sort_index().astype(float)
    if s.index.has_duplicates:
        raise ValueError("Series memiliki tanggal duplikat.")

    expected = pd.date_range(s.index.min(), s.index.max(), freq="D")
    if not s.index.equals(expected):
        raise ValueError(
            "Series harus mempertahankan kalender harian penuh termasuk NaN pada coverage gap."
        )

    observed = s.notna()
    block_id = observed.ne(observed.shift(fill_value=False)).cumsum()
    segments: list[pd.Series] = []
    for _, block in s.groupby(block_id):
        if block.notna().all() and len(block) >= int(min_days):
            block = block.copy()
            block.name = s.name
            segments.append(block)
    return segments


def select_complete_daily_segment(series: pd.Series,
                                  containing_date=None,
                                  end_at=None,
                                  latest: bool = False,
                                  min_days: int = 1) -> pd.Series:
    """Pilih satu segmen kontigu untuk kebutuhan model.

    - ``containing_date``: pilih segmen yang benar-benar mencakup tanggal itu.
    - ``end_at``: pilih segmen terakhir yang tersedia sampai tanggal cutoff,
      kemudian clip segmen pada cutoff. Berguna untuk training historis.
    - ``latest=True``: pilih segmen coverage paling baru.

    Tidak pernah melompati coverage gap dan tidak pernah mengimputasi NaN.
    """
    segments = split_complete_daily_segments(series, min_days=1)
    if not segments:
        raise ValueError("Tidak ada segmen coverage harian yang lengkap.")

    selected: pd.Series | None = None

    if containing_date is not None:
        target = pd.Timestamp(containing_date).normalize()
        for seg in segments:
            if seg.index.min() <= target <= seg.index.max():
                selected = seg
                break
        if selected is None:
            raise ValueError(f"Tanggal {target.date()} berada pada coverage gap.")
    elif end_at is not None:
        cutoff = pd.Timestamp(end_at).normalize()
        candidates = [seg for seg in segments if seg.index.min() <= cutoff]
        if not candidates:
            raise ValueError(f"Tidak ada segmen coverage sebelum {cutoff.date()}.")
        selected = candidates[-1].loc[:cutoff]
    elif latest:
        selected = segments[-1]
    else:
        selected = segments[-1]

    if selected is None or len(selected) < int(min_days):
        available = 0 if selected is None else len(selected)
        raise ValueError(
            f"Segmen coverage terlalu pendek untuk kebutuhan model: {available} hari, "
            f"minimal {int(min_days)} hari."
        )
    if selected.isna().any():
        raise ValueError("Segmen terpilih masih memiliki NaN; ini menandakan bug coverage selection.")
    return selected.astype(float)


def build_forecast_calendar_audit(cleaned: pd.DataFrame) -> pd.DataFrame:
    """Ringkasan status kalender harian untuk audit sebelum forecasting."""
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


def build_forecast_coverage_segments(cleaned: pd.DataFrame,
                                     model_ready_min_days: int = 30) -> pd.DataFrame:
    """Ringkas blok coverage kontigu untuk dashboard/model selection."""
    full = build_daily_refueling_series(cleaned, strict_source_coverage=False)
    audit = build_forecast_calendar_audit(cleaned).set_index("date")
    segments = split_complete_daily_segments(full)

    df = cleaned.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    records: list[dict] = []
    for idx, seg in enumerate(segments, start=1):
        start = seg.index.min()
        end = seg.index.max()
        mask = df["date"].between(start, end, inclusive="both")
        if "source_system" in df.columns:
            sources = sorted(
                df.loc[mask, "source_system"].dropna().astype(str).str.upper().unique().tolist()
            )
        else:
            sources = ["EXCEL"]
        records.append({
            "segment_id": idx,
            "start_date": start,
            "end_date": end,
            "n_days": int(len(seg)),
            "total_liter": float(seg.sum()),
            "source_row_count": int(audit.loc[start:end, "source_row_count"].sum()),
            "source_systems": ", ".join(sources),
            "model_ready": bool(len(seg) >= int(model_ready_min_days)),
            "is_latest": bool(idx == len(segments)),
        })

    return pd.DataFrame(records, columns=[
        "segment_id", "start_date", "end_date", "n_days", "total_liter",
        "source_row_count", "source_systems", "model_ready", "is_latest",
    ])
