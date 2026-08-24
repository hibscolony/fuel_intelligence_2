"""Robust date-range handling for the UJB dashboard scraper.

The UJB report UI can expose dates through native inputs, paired text inputs,
or the common jQuery daterangepicker plugin. UJB JICT is confirmed to use
``YYYY/MM/DD`` for its visible date format; legacy MDY/DMY handling is kept as
fallback so the scraper remains defensive if the vendor UI changes.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import pandas as pd

_YMD_SLASH_TOKEN_RE = re.compile(r"(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})")
_DATE_TOKEN_RE = re.compile(r"(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})")
_DATE_META_RE = re.compile(r"date|tanggal|range|period|periode|from|to|start|end|awal|akhir", re.I)
_ACTION_RE = re.compile(r"filter|apply|search|cari|tampil|proses|submit|go", re.I)


def infer_slash_date_order(current_value: str, reference_date: date | None = None) -> str:
    """Infer ``YMD``, ``MDY`` or ``DMY`` from an existing UI value.

    UJB JICT is confirmed to use YMD slash (for example ``2026/08/24``).
    MDY/DMY inference is retained as a fallback for vendor UI changes.
    """
    current = current_value or ""
    if _YMD_SLASH_TOKEN_RE.search(current):
        return "YMD"

    tokens = list(_DATE_TOKEN_RE.finditer(current))
    if not tokens:
        return "YMD"

    for match in tokens:
        a = int(match.group("a"))
        b = int(match.group("b"))
        if a > 12 and b <= 12:
            return "DMY"
        if b > 12 and a <= 12:
            return "MDY"

    if reference_date is not None:
        best: tuple[int, str] | None = None
        for order in ("MDY", "DMY"):
            for match in tokens:
                a, b, y = int(match.group("a")), int(match.group("b")), int(match.group("y"))
                try:
                    candidate = date(y, a, b) if order == "MDY" else date(y, b, a)
                except ValueError:
                    continue
                distance = abs((candidate - reference_date).days)
                if best is None or distance < best[0]:
                    best = (distance, order)
        if best is not None:
            return best[1]

    return "YMD"


def format_single_date_like_current(current_value: str, requested_date: str) -> str:
    """Format satu tanggal mengikuti style field UJB yang sedang tampil."""
    current = (current_value or "").strip()
    ts = pd.Timestamp(requested_date)

    if _YMD_SLASH_TOKEN_RE.search(current):
        return ts.strftime("%Y/%m/%d")
    if _DATE_TOKEN_RE.search(current):
        order = infer_slash_date_order(current, reference_date=ts.date())
        if order == "DMY":
            return ts.strftime("%d/%m/%Y")
        if order == "MDY":
            return ts.strftime("%m/%d/%Y")
        return ts.strftime("%Y/%m/%d")
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}", current):
        return ts.strftime("%Y-%m-%d")

    # Confirmed UJB JICT fallback.
    return ts.strftime("%Y/%m/%d")


def format_range_like_current(
    current_value: str,
    date_from: str,
    date_to: str,
    reference_date: date | None = None,
) -> str:
    """Format requested range using the UI's current style where possible."""
    current = (current_value or "").strip()
    start = pd.Timestamp(date_from)
    end = pd.Timestamp(date_to)

    if _YMD_SLASH_TOKEN_RE.search(current):
        left, right = start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")
    elif _DATE_TOKEN_RE.search(current):
        order = infer_slash_date_order(current, reference_date=reference_date)
        if order == "DMY":
            left, right = start.strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y")
        elif order == "MDY":
            left, right = start.strftime("%m/%d/%Y"), end.strftime("%m/%d/%Y")
        else:
            left, right = start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")
    elif re.search(r"\d{4}-\d{1,2}-\d{1,2}", current):
        left, right = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    else:
        # Confirmed UJB JICT fallback.
        left, right = start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")

    if re.search(r"\bto\b", current, re.I):
        separator = " to "
    elif "–" in current:
        separator = " – "
    else:
        separator = " - "
    return f"{left}{separator}{right}"


def _metadata(locator: Any) -> str:
    parts: list[str] = []
    for attr in ("type", "name", "id", "placeholder", "aria-label", "class"):
        try:
            value = locator.get_attribute(attr)
        except Exception:
            value = None
        if value:
            parts.append(f"{attr}={value}")
    return " ".join(parts)


def _set_value(locator: Any, value: str) -> None:
    """Set value and dispatch events, including hidden/text plugin inputs."""
    locator.evaluate(
        """(el, value) => {
            el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        value,
    )


def _wait(page: Any) -> None:
    page.wait_for_timeout(800)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


def _trigger_filter(page: Any, anchor: Any | None = None) -> str:
    buttons = page.get_by_role("button", name=_ACTION_RE)
    for i in range(buttons.count()):
        btn = buttons.nth(i)
        try:
            if btn.is_visible() and not btn.is_disabled():
                btn.click()
                _wait(page)
                return "button"
        except Exception:
            continue

    submits = page.locator("input[type=submit]")
    for i in range(submits.count()):
        submit = submits.nth(i)
        try:
            if submit.is_visible():
                submit.click()
                _wait(page)
                return "submit_input"
        except Exception:
            continue

    if anchor is not None:
        try:
            anchor.evaluate(
                """el => {
                    const form = el.closest('form');
                    if (form) {
                        if (form.requestSubmit) form.requestSubmit();
                        else form.submit();
                        return true;
                    }
                    return false;
                }"""
            )
            _wait(page)
            return "form_submit"
        except Exception:
            try:
                anchor.press("Enter")
                _wait(page)
                return "enter"
            except Exception:
                pass
    return "none"


def _try_jquery_daterangepicker(page: Any, date_from: str, date_to: str) -> str | None:
    """Drive the jQuery daterangepicker instance directly when present."""
    try:
        result = page.evaluate(
            """({start, end}) => {
                if (!window.jQuery) return null;
                const $ = window.jQuery;
                const inputs = Array.from(document.querySelectorAll('input'));
                for (const el of inputs) {
                    let picker = null;
                    try { picker = $(el).data('daterangepicker'); } catch (_) {}
                    if (!picker) continue;
                    try {
                        const fmt = picker.locale && picker.locale.format ? picker.locale.format : 'YYYY/MM/DD';
                        const separator = picker.locale && picker.locale.separator ? picker.locale.separator : ' - ';
                        const startMoment = window.moment ? window.moment(start, 'YYYY-MM-DD', true) : start;
                        const endMoment = window.moment ? window.moment(end, 'YYYY-MM-DD', true) : end;

                        picker.setStartDate(startMoment);
                        picker.setEndDate(endMoment);

                        if (window.moment) {
                            el.value = startMoment.format(fmt) + separator + endMoment.format(fmt);
                        } else {
                            el.value = start.replaceAll('-', '/') + separator + end.replaceAll('-', '/');
                        }
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        $(el).trigger('apply.daterangepicker', picker);
                        return {
                            name: el.name || '', id: el.id || '', value: el.value || '',
                            format: fmt
                        };
                    } catch (err) {
                        continue;
                    }
                }
                return null;
            }""",
            {"start": date_from, "end": date_to},
        )
    except Exception:
        return None

    if result:
        _trigger_filter(page)
        return f"jquery_daterangepicker:{result}"
    return None


def apply_date_range_robust(page: Any, date_from: str, date_to: str) -> str:
    """Apply a requested window using the most deterministic available method."""
    # 1) Native date inputs require ISO YYYY-MM-DD by HTML specification.
    native = page.locator('input[type="date"]')
    if native.count() >= 2:
        native.nth(0).fill(date_from)
        native.nth(1).fill(date_to)
        trigger = _trigger_filter(page, native.nth(1))
        return f"two_native_dates:{trigger}"

    # 2) Common jQuery daterangepicker plugin. Drive plugin state directly.
    plugin_strategy = _try_jquery_daterangepicker(page, date_from, date_to)
    if plugin_strategy:
        return plugin_strategy

    # 3) Named start/end inputs, including hidden fields.
    all_inputs = page.locator("input")
    start_input = None
    end_input = None
    start_re = re.compile(r"date.?from|from.?date|start.?date|date.?start|tanggal.?awal|(^|[_-])from([_-]|$)|(^|[_-])start([_-]|$)", re.I)
    end_re = re.compile(r"date.?to|to.?date|end.?date|date.?end|tanggal.?akhir|(^|[_-])to([_-]|$)|(^|[_-])end([_-]|$)", re.I)
    for i in range(all_inputs.count()):
        loc = all_inputs.nth(i)
        meta = _metadata(loc)
        if start_input is None and start_re.search(meta):
            start_input = loc
        if end_input is None and end_re.search(meta):
            end_input = loc
    if start_input is not None and end_input is not None:
        try:
            current_start = start_input.input_value()
        except Exception:
            current_start = ""
        try:
            current_end = end_input.input_value()
        except Exception:
            current_end = ""
        _set_value(start_input, format_single_date_like_current(current_start, date_from))
        _set_value(end_input, format_single_date_like_current(current_end, date_to))
        trigger = _trigger_filter(page, end_input)
        return f"named_pair:{trigger}"

    # 4) Visible single range input. UJB is YYYY/MM/DD; other orders remain fallback.
    ref = pd.Timestamp(date_to).date()
    for i in range(all_inputs.count()):
        loc = all_inputs.nth(i)
        try:
            if not loc.is_visible():
                continue
            typ = (loc.get_attribute("type") or "text").lower()
            if typ in {"hidden", "password", "submit", "button", "checkbox", "radio"}:
                continue
            meta = _metadata(loc)
            current = loc.input_value()
            if not (
                _DATE_META_RE.search(meta)
                or _YMD_SLASH_TOKEN_RE.search(current or "")
                or _DATE_TOKEN_RE.search(current or "")
            ):
                continue
            requested = format_range_like_current(
                current, date_from, date_to, reference_date=ref
            )
            _set_value(loc, requested)
            trigger = _trigger_filter(page, loc)
            return f"single_range:{requested}:{trigger}"
        except Exception:
            continue

    return "not_found"


def collect_filter_diagnostics(page: Any) -> dict[str, Any]:
    """Collect non-sensitive UI metadata to diagnose future date-filter failures."""
    inputs: list[dict[str, Any]] = []
    locators = page.locator("input")
    for i in range(min(locators.count(), 30)):
        loc = locators.nth(i)
        meta = _metadata(loc)
        typ = (loc.get_attribute("type") or "text").lower()
        if re.search(r"password|user|token|csrf|secret", meta, re.I):
            value = "<redacted>"
        elif typ == "hidden":
            value = "<hidden>"
        else:
            try:
                value = loc.input_value()
            except Exception:
                value = ""
        try:
            visible = bool(loc.is_visible())
        except Exception:
            visible = False
        inputs.append({"meta": meta, "value": value, "visible": visible})

    buttons: list[str] = []
    btns = page.locator("button")
    for i in range(min(btns.count(), 20)):
        try:
            text = " ".join(btns.nth(i).inner_text().split())
        except Exception:
            text = ""
        if text:
            buttons.append(text[:120])

    return {
        "url": page.url,
        "inputs": inputs,
        "buttons": buttons,
    }
