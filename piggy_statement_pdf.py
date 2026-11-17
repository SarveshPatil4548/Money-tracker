"""Piggy bank statement PDF - warm, modern and printable (ReportLab).

Page 1 is a self-contained overview: header, hero + stat tiles, cumulative
savings chart, contributor split, daily calendar, month summary and a slim
verification footer strip.

When the overview and a compact one-row-per-date records table fit on a single
A4 page (with no text below 7pt), both are placed on page 1. Otherwise page 1
is stretched to fill the page and the records continue on page 2+ with a
repeating header row and no split rows.

All assets/fonts are local; the caller hashes the final PDF bytes.
"""

from __future__ import annotations

import calendar
import io
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.sax.saxutils import escape

BASE_DIR = Path(__file__).resolve().parent

# ----------------------------- design tokens ------------------------------ #

SPACE = 8  # 8pt grid base (points)
MARGIN_MM = 18

FONT_REGULAR = "NotoSans"
FONT_BOLD = "NotoSans-Bold"

# 4 / 8 / 12 / 16 / 24 spacing scale (points) used by the page layout.
GAP_XS = 4
GAP_SM = 8
GAP_MD = 12
GAP_LG = 16
GAP_XL = 24

# Millimetre spacing scale for the page-1 grid: 4 / 8 / 12 mm.
MM_XS = 4.0
MM_SM = 8.0
MM_MD = 12.0

# Every glyph on page 1 must be at least this size.
MIN_TEXT_PT = 7.0


@dataclass(frozen=True)
class Theme:
    """Colour tokens shared by the web app theme and the PDF."""

    name: str
    accent: str
    accent_soft: str
    accent_dark: str
    text: str
    muted: str
    faint: str
    border: str
    border_soft: str
    surface: str
    surface_tint: str
    deposit: str
    withdrawal: str
    zebra: str
    grid: str = "#eef0f4"
    saved_light: str = "#e9f9f0"
    saved_mid: str = "#c9f2dc"
    saved_deep: str = "#9fe8c4"
    saved_text: str = "#065f46"
    missed_soft: str = "#fef4f4"
    missed_marker: str = "#ef4444"
    future_border: str = "#d5dae2"
    chart_fill_top: str = "#e6e9ff"
    chart_fill_bottom: str = "#f8f9ff"
    neutral_bar: str = "#e9ecf2"
    amber: str = "#b45309"


THEMES = {
    "color": Theme(
        name="color",
        accent="#4f46e5",
        accent_soft="#eef2ff",
        accent_dark="#3730a3",
        text="#101828",
        muted="#667085",
        faint="#98a2b3",
        border="#e4e7ec",
        border_soft="#f0f2f5",
        surface="#ffffff",
        surface_tint="#f8fafc",
        deposit="#047857",
        withdrawal="#b91c1c",
        zebra="#fbfbfd",
        grid="#eef0f4",
        saved_light="#e9f9f0",
        saved_mid="#c9f2dc",
        saved_deep="#9fe8c4",
        saved_text="#065f46",
        missed_soft="#fef4f4",
        missed_marker="#ef4444",
        future_border="#d5dae2",
        chart_fill_top="#e6e9ff",
        chart_fill_bottom="#f8f9ff",
        neutral_bar="#e9ecf2",
    ),
    "bw": Theme(
        name="bw",
        accent="#111111",
        accent_soft="#f2f2f2",
        accent_dark="#111111",
        text="#111111",
        muted="#444444",
        faint="#666666",
        border="#c9c9c9",
        border_soft="#e6e6e6",
        surface="#ffffff",
        surface_tint="#f5f5f5",
        deposit="#111111",
        withdrawal="#111111",
        zebra="#fafafa",
        grid="#e8e8e8",
        saved_light="#f2f2f2",
        saved_mid="#dedede",
        saved_deep="#c4c4c4",
        saved_text="#111111",
        missed_soft="#f7f7f7",
        missed_marker="#111111",
        future_border="#bbbbbb",
        chart_fill_top="#ededed",
        chart_fill_bottom="#fafafa",
        neutral_bar="#e6e6e6",
        amber="#111111",
    ),
}


def get_theme(name: str | None) -> Theme:
    key = (name or "color").strip().lower()
    if key in ("bw", "black", "black_white", "black-and-white", "print", "mono", "grayscale"):
        return THEMES["bw"]
    return THEMES["color"]


def _q2(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _group_int(value: int) -> str:
    s = str(abs(int(value)))
    if len(s) <= 3:
        return s
    grouped = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        grouped = s[-2:] + "," + grouped
        s = s[:-2]
    if s:
        grouped = s + "," + grouped
    return ("-" if int(value) < 0 else "") + grouped


def inr_grouping(amount) -> str:
    value = _q2(amount)
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    frac = f"{(value - Decimal(whole)):.2f}"[2:]
    return f"{sign}{_group_int(whole)}.{frac}"


def money(value, symbol: str = "₹", signed: bool = False) -> str:
    dec = _q2(value)
    sign = ""
    if signed:
        if dec > 0:
            sign = "+"
        elif dec < 0:
            sign = "-"
    elif dec < 0:
        sign = "-"
    sym = symbol or "Rs"
    return f"{sign}{sym} {inr_grouping(abs(dec))}"


def money_short(value, symbol: str = "₹", signed: bool = False) -> str:
    """Compact money for charts / tiles: drops a trailing .00."""
    dec = _q2(value)
    sign = ""
    if signed:
        if dec > 0:
            sign = "+"
        elif dec < 0:
            sign = "-"
    elif dec < 0:
        sign = "-"
    body = inr_grouping(abs(dec))
    if body.endswith(".00"):
        body = body[:-3]
    return f"{sign}{symbol or 'Rs'} {body}"


def short_hash(value: str, head: int = 12, tail: int = 8) -> str:
    v = (value or "").strip()
    if len(v) <= head + tail + 3:
        return v
    return f"{v[:head]}…{v[-tail:]}"


def _short_name(name: str, limit: int = 10) -> str:
    parts = [p for p in str(name or "").strip().split() if p]
    if not parts:
        return "?"
    out = parts[-1] if len(parts) > 1 else parts[0]
    return out[:limit]


_FONT_READY = False
_FONT_REG = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_HAS_RUPEE = False


def register_fonts():
    """Register a local font with tabular digits and a real rupee glyph."""
    global _FONT_READY, _FONT_REG, _FONT_BOLD, _HAS_RUPEE
    if _FONT_READY:
        return _FONT_REG, _FONT_BOLD, _HAS_RUPEE
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except Exception:
        _FONT_READY = True
        return _FONT_REG, _FONT_BOLD, _HAS_RUPEE

    candidates = [
        (
            BASE_DIR / "static" / "fonts" / "NotoSans-Regular.ttf",
            BASE_DIR / "static" / "fonts" / "NotoSans-Bold.ttf",
            "NotoSans",
            "NotoSans-Bold",
        ),
        (
            BASE_DIR / "static" / "fonts" / "DejaVuSans.ttf",
            BASE_DIR / "static" / "fonts" / "DejaVuSans-Bold.ttf",
            "DejaVuSans",
            "DejaVuSans-Bold",
        ),
        (
            Path(r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\segoeuib.ttf"),
            "AppSans",
            "AppSans-Bold",
        ),
        (
            Path(r"C:\Windows\Fonts\arial.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf"),
            "AppSans",
            "AppSans-Bold",
        ),
        (
            BASE_DIR / "static" / "fonts" / "Inter-Variable.ttf",
            BASE_DIR / "static" / "fonts" / "Inter-Variable.ttf",
            "InterVar",
            "InterVar",
        ),
        (
            BASE_DIR / "static" / "fonts" / "Inter-Regular.ttf",
            BASE_DIR / "static" / "fonts" / "Inter-Bold.ttf",
            "Inter",
            "Inter-Bold",
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            "DejaVuSans",
            "DejaVuSans-Bold",
        ),
    ]
    for reg_path, bold_path, reg_name, bold_name in candidates:
        try:
            if not Path(reg_path).exists():
                continue
            face = TTFont(reg_name, str(reg_path))
            pdfmetrics.registerFont(face)
            reg_has_rupee = 0x20B9 in getattr(face.face, "charToGlyph", {})
            bname = reg_name
            if bold_path and Path(bold_path).exists():
                bold_face = TTFont(bold_name, str(bold_path))
                bold_has_rupee = 0x20B9 in getattr(bold_face.face, "charToGlyph", {})
                if not reg_has_rupee or bold_has_rupee:
                    pdfmetrics.registerFont(bold_face)
                    bname = bold_name
                else:
                    bname = reg_name
            pdfmetrics.registerFontFamily(
                reg_name, normal=reg_name, bold=bname, italic=reg_name, boldItalic=bname
            )
            _FONT_REG, _FONT_BOLD, _HAS_RUPEE = reg_name, bname, reg_has_rupee
            _FONT_READY = True
            return _FONT_REG, _FONT_BOLD, _HAS_RUPEE
        except Exception:
            continue
    _FONT_READY = True
    return _FONT_REG, _FONT_BOLD, _HAS_RUPEE


def _hx(color: str):
    from reportlab.lib import colors

    return colors.HexColor(color)


def _blend(c1, c2, t: float):
    from reportlab.lib import colors

    t = max(0.0, min(1.0, float(t)))
    a = _hx(c1) if isinstance(c1, str) else c1
    b = _hx(c2) if isinstance(c2, str) else c2
    return colors.Color(
        a.red + (b.red - a.red) * t,
        a.green + (b.green - a.green) * t,
        a.blue + (b.blue - a.blue) * t,
    )


def _parse_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _fmt_date(d) -> str:
    if not d:
        return "-"
    try:
        return d.strftime("%d %b %Y")
    except Exception:
        return str(d)


def _entry_rows(entries, opening):
    bal = _q2(opening)
    out = []
    for e in sorted(entries, key=lambda x: (str(x.get("entry_date", "")), str(x.get("id", "")))):
        amt = _q2(e.get("amount"))
        if e.get("entry_type") == "withdrawal":
            bal -= amt
        else:
            bal += amt
        e2 = dict(e)
        e2["_balance"] = _q2(bal)
        out.append(e2)
    return out


def _qr_image(data: str):
    try:
        import qrcode
        from reportlab.lib.utils import ImageReader

        qr = qrcode.QRCode(border=1, box_size=6)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)
    except Exception:
        return None


@dataclass(frozen=True)
class LayoutConfig:
    chart_mm: float
    hero_mm: float
    tiles_mm: float
    cal_min_mm: float
    header_mm: float = 14.5
    gap_header_mm: float = MM_SM
    gap_hero_mm: float = MM_XS
    gap_tiles_mm: float = MM_SM
    gap_middle_mm: float = MM_XS
    compact_hero: bool = False
    small_fs: float = MIN_TEXT_PT
    one_page: bool = False


def build_statement_pdf(
    *,
    bank: dict,
    period_label: str,
    period_start,
    period_end,
    statement_no: str,
    generated_at,
    opening_balance,
    closing_balance,
    total_deposits,
    total_withdrawals,
    entries: list[dict],
    include_entries: bool = True,
    include_calendar: bool = True,
    highlight_missed: bool = True,
    daily_target=None,
    detail: str = "detailed",
    theme_name: str = "color",
    logo_path=None,
    goal: dict | None = None,
    contributor_filter: str | None = None,
    statement_file_hash: str = "",
    debug_layout: dict | None = None,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas as pdfcanvas

    theme = get_theme(theme_name)
    font_reg, font_bold, has_rupee = register_fonts()
    symbol = "₹" if has_rupee else "Rs"
    margin = MARGIN_MM * mm
    page_w, page_h = A4
    content_w = page_w - 2 * margin
    content_h = page_h - 2 * margin

    psd = _parse_date(period_start) or date.today()
    ped = _parse_date(period_end) or psd
    if ped < psd:
        psd, ped = ped, psd
    today = date.today()
    if isinstance(generated_at, datetime):
        generated_str = generated_at.strftime("%d %b %Y, %H:%M")
    elif isinstance(generated_at, date):
        generated_str = generated_at.strftime("%d %b %Y")
    else:
        generated_str = str(generated_at)

    entries = list(entries or [])
    display_entries = entries
    if contributor_filter and contributor_filter not in ("", "all"):
        display_entries = [e for e in entries if e.get("contributor") == contributor_filter]

    opening = _q2(opening_balance)
    closing = _q2(closing_balance)
    tdep_passed = _q2(total_deposits)
    twd_passed = _q2(total_withdrawals)
    try:
        target_amt = _q2(daily_target) if daily_target not in (None, "") else Decimal("0.00")
    except Exception:
        target_amt = Decimal("0.00")

    contributor_keys = ("me", "her")
    contributor_names = {
        "me": str(bank.get("contributor_me") or "Me").strip() or "Me",
        "her": str(bank.get("contributor_her") or "Her").strip() or "Her",
    }
    contributor_short = {k: _short_name(v, 9) for k, v in contributor_names.items()}

    # ----------------------------- data model ------------------------------ #

    def _group_days(rows):
        days = {}
        for e in rows:
            d = _parse_date(e.get("entry_date"))
            if not d:
                continue
            rec = days.setdefault(
                d,
                {
                    "dep": Decimal("0.00"),
                    "wd": Decimal("0.00"),
                    "by_dep": defaultdict(lambda: Decimal("0.00")),
                    "by_net": defaultdict(lambda: Decimal("0.00")),
                    "notes": [],
                },
            )
            amt = _q2(e.get("amount"))
            who = e.get("contributor") or "me"
            if who not in contributor_keys:
                who = "me"
            if e.get("entry_type") == "withdrawal":
                rec["wd"] += amt
                rec["by_net"][who] -= amt
            else:
                rec["dep"] += amt
                rec["by_dep"][who] += amt
                rec["by_net"][who] += amt
            note = str(e.get("note") or "").strip()
            if note:
                rec["notes"].append(note)
        return days

    day_map = _group_days(display_entries)

    def _months_in_period(start_d, end_d):
        curm = date(start_d.year, start_d.month, 1)
        lastm = date(end_d.year, end_d.month, 1)
        out = []
        while curm <= lastm:
            out.append((curm.year, curm.month))
            if curm.month == 12:
                curm = date(curm.year + 1, 1, 1)
            else:
                curm = date(curm.year, curm.month + 1, 1)
        return out

    def _month_end(y, m):
        return date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)

    def _weeks(y, m):
        dim = calendar.monthrange(y, m)[1]
        first_wd = date(y, m, 1).weekday()  # Mon=0 ... Sun=6
        return int(math.ceil((first_wd + dim) / 7.0))

    months = _months_in_period(psd, ped)

    def _month_stats(y, m):
        m_start = date(y, m, 1)
        m_end = _month_end(y, m)
        eff_start = max(m_start, psd)
        eff_end = min(m_end, ped)
        count_end = min(eff_end, today)
        total = Decimal("0.00")
        deposits = Decimal("0.00")
        withdrawals = Decimal("0.00")
        days_saved_m = 0
        missed = 0
        best = Decimal("0.00")
        best_day = None
        d = eff_start
        while d <= eff_end:
            rec = day_map.get(d)
            if rec:
                total += rec["dep"] - rec["wd"]
                deposits += rec["dep"]
                withdrawals += rec["wd"]
                if rec["dep"] > 0:
                    days_saved_m += 1
                    if rec["dep"] > best:
                        best = rec["dep"]
                        best_day = d
                elif rec["wd"] > 0:
                    pass
                elif d <= count_end and highlight_missed:
                    missed += 1
            elif d <= count_end and highlight_missed:
                missed += 1
            d += timedelta(days=1)
        return {
            "total": _q2(total),
            "deposits": _q2(deposits),
            "withdrawals": _q2(withdrawals),
            "days_saved": days_saved_m,
            "missed": missed,
            "best_amt": _q2(best),
            "best_day": best_day,
        }

    month_stats = {(y, m): _month_stats(y, m) for (y, m) in months}
    period_total = _q2(sum((s["total"] for s in month_stats.values()), Decimal("0.00")))
    period_deposits = _q2(sum((s["deposits"] for s in month_stats.values()), Decimal("0.00")))
    period_withdrawals = _q2(sum((s["withdrawals"] for s in month_stats.values()), Decimal("0.00")))
    days_saved = sum(s["days_saved"] for s in month_stats.values())
    missed_days = sum(s["missed"] for s in month_stats.values())
    best_amt = max((s["best_amt"] for s in month_stats.values()), default=Decimal("0.00"))
    best_day = next(
        (s["best_day"] for s in month_stats.values() if s["best_amt"] == best_amt and s["best_day"]),
        None,
    )

    dep_dates = sorted({d for d, rec in day_map.items() if rec["dep"] > 0 and psd <= d <= ped})
    longest_streak = cur_run = 0
    if dep_dates:
        longest_streak = cur_run = 1
        for i in range(1, len(dep_dates)):
            if (dep_dates[i] - dep_dates[i - 1]).days == 1:
                cur_run += 1
            else:
                longest_streak = max(longest_streak, cur_run)
                cur_run = 1
        longest_streak = max(longest_streak, cur_run)

    if psd <= today <= ped:
        anchor = today
    elif today > ped:
        anchor = ped
    else:
        anchor = psd - timedelta(days=1)
    deposits_up_to_anchor = [d for d in dep_dates if d <= anchor]
    current_streak = 0
    if deposits_up_to_anchor and (anchor - deposits_up_to_anchor[-1]).days <= 1:
        current_streak = 1
        idx = len(deposits_up_to_anchor) - 1
        while idx > 0 and (deposits_up_to_anchor[idx] - deposits_up_to_anchor[idx - 1]).days == 1:
            current_streak += 1
            idx -= 1

    if psd > today:
        elapsed_days = 0
    else:
        elapsed_days = (min(ped, today) - psd).days + 1
    elapsed_days = max(0, elapsed_days)
    avg_per_day = _q2(period_total / Decimal(elapsed_days)) if elapsed_days else Decimal("0.00")

    # Contributor totals (deposits only) for "Who saved what".
    contributor_totals = {k: Decimal("0.00") for k in contributor_keys}
    for rec in day_map.values():
        for k in contributor_keys:
            contributor_totals[k] += rec["by_dep"].get(k, Decimal("0.00"))
    contributor_totals = {k: _q2(v) for k, v in contributor_totals.items()}

    # Daily cumulative series for the chart (period net, starts at zero).
    chart_series = []
    running_cum = Decimal("0.00")
    d = psd
    while d <= ped:
        rec = day_map.get(d)
        if rec:
            running_cum += rec["dep"] - rec["wd"]
        chart_series.append((d, _q2(running_cum)))
        d += timedelta(days=1)
    chart_last = chart_series[-1][1] if chart_series else Decimal("0.00")

    # One row per date for the records table.
    record_rows = []
    running_bal = opening
    for d in sorted(day_map):
        if d < psd or d > ped:
            continue
        rec = day_map[d]
        net = _q2(rec["dep"] - rec["wd"])
        running_bal = _q2(running_bal + net)
        record_rows.append(
            {
                "date": d,
                "by": {k: _q2(rec["by_net"].get(k, Decimal("0.00"))) for k in contributor_keys},
                "total": net,
                "balance": _q2(running_bal),
                "note": "; ".join(rec["notes"]),
                "month": (d.year, d.month),
            }
        )
    records_total = _q2(sum((r["total"] for r in record_rows), Decimal("0.00")))

    month_subtotals = {}
    for (y, m) in months:
        rows_m = [r for r in record_rows if r["month"] == (y, m)]
        month_subtotals[(y, m)] = {
            "by": {
                k: _q2(sum((r["by"][k] for r in rows_m), Decimal("0.00")))
                for k in contributor_keys
            },
            "total": _q2(sum((r["total"] for r in rows_m), Decimal("0.00"))),
            "balance": rows_m[-1]["balance"] if rows_m else None,
            "count": len(rows_m),
        }

    if debug_layout is not None:
        debug_layout.setdefault(
            "content_rect", (margin, margin, page_w - margin, page_h - margin)
        )
        debug_layout.setdefault("calendar_cells", [])
        debug_layout.setdefault("hero_tiles", [])
        debug_layout.setdefault("calendar_grid_zones", [])
        debug_layout.setdefault("totals", {})
        debug_layout["totals"].update(
            {
                "period_total": str(period_total),
                "chart_last": str(chart_last),
                "records_total": str(records_total),
                "month_subtotal_sum": str(
                    _q2(sum((m["total"] for m in month_subtotals.values()), Decimal("0.00")))
                ),
                "closing": str(closing),
                "days_saved": days_saved,
                "missed_days": missed_days,
                "elapsed_days": elapsed_days,
            }
        )

    # ----------------------------- drawing utils --------------------------- #

    def _tw(text, font, size):
        return pdfmetrics.stringWidth(str(text), font, size)

    def _fit_size(text, font, max_w, start, min_size):
        if max_w <= 0:
            return min_size
        w = _tw(text, font, start)
        if w <= max_w:
            return start
        return max(min_size, start * (max_w / w))

    def _draw_text(c, text, x, y, font, size, color, align="left"):
        c.setFont(font, size)
        c.setFillColor(_hx(color))
        if align == "center":
            c.drawCentredString(x, y, str(text))
        elif align == "right":
            c.drawRightString(x, y, str(text))
        else:
            c.drawString(x, y, str(text))

    def _draw_fit(c, text, x, y, font, start, min_size, color, max_w, align="left"):
        size = _fit_size(text, font, max_w, start, min_size)
        _draw_text(c, text, x, y, font, size, color, align=align)
        return size

    def _round_rect(c, x, y, w, h, r=6, fill=None, stroke=None, lw=0.6, dash=None, alpha=None):
        c.saveState()
        if dash:
            c.setDash(dash, 0)
        if fill:
            c.setFillColor(_hx(fill))
            if alpha is not None:
                c.setFillAlpha(alpha)
        if stroke:
            c.setStrokeColor(_hx(stroke))
            c.setLineWidth(lw)
        c.roundRect(x, y, w, h, r, stroke=1 if stroke else 0, fill=1 if fill else 0)
        c.restoreState()

    def _line(c, x1, y1, x2, y2, color, lw=0.5, dash=None):
        c.saveState()
        c.setStrokeColor(_hx(color))
        c.setLineWidth(lw)
        if dash:
            c.setDash(dash, 0)
        c.line(x1, y1, x2, y2)
        c.restoreState()

    def _abs_rect(c, x, y, w, h):
        try:
            x0, y0 = c.absolutePosition(x, y)
            x1, y1 = c.absolutePosition(x + w, y + h)
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        except Exception:
            return (x, y, x + w, y + h)

    def _record_debug_rect(c, key, x, y, w, h, meta=None):
        if debug_layout is None:
            return
        item = {"page": c.getPageNumber(), "rect": _abs_rect(c, x, y, w, h)}
        if meta:
            item.update(meta)
        debug_layout.setdefault(key, []).append(item)

    def _text_baseline(top, size):
        return top - size * 0.86

    def _line_height(font, size):
        return pdfmetrics.getAscent(font, size) - pdfmetrics.getDescent(font, size)

    def _baseline_from_top(box_top, font, size):
        return box_top - pdfmetrics.getAscent(font, size)

    def _baseline_centered(center_y, font, size):
        return center_y - (
            pdfmetrics.getAscent(font, size) + pdfmetrics.getDescent(font, size)
        ) / 2.0

    def _clip_text(text, font, size, max_w):
        value = str(text or "")
        if max_w <= 0:
            return ""
        if _tw(value, font, size) <= max_w:
            return value
        ell = "…"
        if _tw(ell, font, size) > max_w:
            return ""
        while value and _tw(value + ell, font, size) > max_w:
            value = value[:-1]
        return (value.rstrip() + ell) if value else ell

    def _draw_fit_clip(c, text, x, y, font, start, min_size, color, max_w, align="left"):
        size = _fit_size(text, font, max_w, start, min_size)
        value = _clip_text(text, font, size, max_w)
        _draw_text(c, value, x, y, font, size, color, align=align)
        return size

    def _wrap_lines(text, font, size, max_w, max_lines):
        words = str(text or "").strip().split()
        if not words:
            return [""]
        lines = []
        current = ""
        idx = 0
        while idx < len(words) and len(lines) < max_lines:
            word = words[idx]
            candidate = f"{current} {word}".strip()
            if not current or _tw(candidate, font, size) <= max_w:
                current = candidate
                idx += 1
            else:
                lines.append(current)
                current = ""
        if idx < len(words):
            rest = " ".join(([current] if current else []) + words[idx:])
            if len(lines) < max_lines:
                lines.append(_clip_text(rest, font, size, max_w))
            elif lines:
                lines[-1] = _clip_text(lines[-1] + " " + rest, font, size, max_w)
        elif current:
            lines.append(current)
        return (lines or [""])[:max_lines]

    def _segment_hits_rect(p1, p2, rect):
        x0, y0, x1, y1 = rect
        ax, ay = p1
        bx, by = p2

        def inside(px, py):
            return x0 <= px <= x1 and y0 <= py <= y1

        if inside(ax, ay) or inside(bx, by):
            return True
        edges = (
            ((x0, y0), (x1, y0)),
            ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)),
            ((x0, y1), (x0, y0)),
        )

        def orient(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

        def on_segment(p, q, r):
            return (
                min(p[0], r[0]) - 1e-6 <= q[0] <= max(p[0], r[0]) + 1e-6
                and min(p[1], r[1]) - 1e-6 <= q[1] <= max(p[1], r[1]) + 1e-6
            )

        a, b = (ax, ay), (bx, by)
        for e1, e2 in edges:
            o1 = orient(a, b, e1)
            o2 = orient(a, b, e2)
            o3 = orient(e1, e2, a)
            o4 = orient(e1, e2, b)
            if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0):
                return True
            if abs(o1) < 1e-6 and on_segment(a, e1, b):
                return True
            if abs(o2) < 1e-6 and on_segment(a, e2, b):
                return True
            if abs(o3) < 1e-6 and on_segment(e1, a, e2):
                return True
            if abs(o4) < 1e-6 and on_segment(e1, b, e2):
                return True
        return False

    def _draw_cross(c, cx, cy, s, color, lw=1.4):
        c.saveState()
        c.setStrokeColor(_hx(color))
        c.setLineWidth(lw)
        c.setLineCap(1)
        c.line(cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)
        c.line(cx - s / 2, cy + s / 2, cx + s / 2, cy - s / 2)
        c.restoreState()

    def _draw_minus(c, cx, cy, s, color, lw=1.6):
        c.saveState()
        c.setStrokeColor(_hx(color))
        c.setLineWidth(lw)
        c.setLineCap(1)
        c.line(cx - s / 2, cy, cx + s / 2, cy)
        c.restoreState()

    def _draw_tick(c, cx, cy, s, color, lw=1.6):
        c.saveState()
        c.setStrokeColor(_hx(color))
        c.setLineWidth(lw)
        c.setLineCap(1)
        c.setLineJoin(1)
        c.line(cx - s / 2, cy, cx - s / 8, cy - s / 2)
        c.line(cx - s / 8, cy - s / 2, cx + s / 2, cy + s / 2)
        c.restoreState()

    def _draw_piggy(c, x, y, w, h):
        ink = theme.accent if theme.name == "color" else theme.text
        soft = theme.accent_soft if theme.name == "color" else theme.surface_tint
        c.saveState()
        c.setFillColor(_hx(soft))
        c.setStrokeColor(_hx(ink))
        c.setLineWidth(1.3)
        c.ellipse(x + 0.06 * w, y + 0.10 * h, x + 0.78 * w, y + 0.84 * h, stroke=1, fill=1)
        c.setFillColor(_hx(ink))
        c.roundRect(x + 0.20 * w, y + 0.02 * h, 0.10 * w, 0.14 * h, 0.04 * w, stroke=0, fill=1)
        c.roundRect(x + 0.54 * w, y + 0.02 * h, 0.10 * w, 0.14 * h, 0.04 * w, stroke=0, fill=1)
        p = c.beginPath()
        p.moveTo(x + 0.32 * w, y + 0.78 * h)
        p.lineTo(x + 0.40 * w, y + 0.98 * h)
        p.lineTo(x + 0.48 * w, y + 0.80 * h)
        p.close()
        c.setFillColor(_hx(ink))
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColor(_hx(soft))
        c.setStrokeColor(_hx(ink))
        c.setLineWidth(1.2)
        c.ellipse(x + 0.68 * w, y + 0.34 * h, x + 0.96 * w, y + 0.60 * h, stroke=1, fill=1)
        c.setFillColor(_hx(ink))
        c.circle(x + 0.79 * w, y + 0.48 * h, 0.022 * w, stroke=0, fill=1)
        c.circle(x + 0.87 * w, y + 0.48 * h, 0.022 * w, stroke=0, fill=1)
        c.circle(x + 0.53 * w, y + 0.62 * h, 0.025 * w, stroke=0, fill=1)
        c.setStrokeColor(_hx(ink))
        c.setLineWidth(1.5)
        c.setLineCap(1)
        c.line(x + 0.26 * w, y + 0.74 * h, x + 0.44 * w, y + 0.74 * h)
        c.setFillColor(_hx(theme.surface))
        c.setStrokeColor(_hx(ink))
        c.setLineWidth(1.1)
        c.circle(x + 0.48 * w, y + 0.90 * h, 0.10 * w, stroke=1, fill=1)
        c.setStrokeColor(_hx(ink))
        c.setLineWidth(max(0.8, 0.028 * w))
        cx = x + 0.48 * w
        cy = y + 0.90 * h
        c.line(cx - 0.045 * w, cy + 0.030 * h, cx + 0.045 * w, cy + 0.030 * h)
        c.line(cx - 0.045 * w, cy - 0.005 * h, cx + 0.045 * w, cy - 0.005 * h)
        c.line(cx - 0.005 * w, cy - 0.055 * h, cx - 0.005 * w, cy + 0.065 * h)
        c.line(cx + 0.030 * w, cy + 0.065 * h, cx - 0.020 * w, cy - 0.055 * h)
        c.restoreState()

    def _draw_swatch(c, x, y, w, h, fill, stroke, dash=None, cross=False, minus=False, tick=False):
        _round_rect(c, x, y, w, h, 2.5, fill=fill, stroke=stroke, lw=0.6, dash=dash)
        cx, cy = x + w / 2, y + h / 2
        if cross:
            _draw_cross(c, cx, cy, min(w, h) * 0.55, theme.missed_marker, 1.1)
        elif minus:
            _draw_minus(c, cx, cy, min(w, h) * 0.55, theme.withdrawal, 1.2)
        elif tick:
            _draw_tick(
                c,
                cx,
                cy,
                min(w, h) * 0.55,
                theme.saved_text if theme.name == "color" else theme.text,
                1.2,
            )

    def _logo_reader():
        candidates = [
            logo_path,
            BASE_DIR / "static" / "img" / "apple-touch-icon.png",
            BASE_DIR / "static" / "img" / "favicon-32x32.png",
        ]
        for cand in candidates:
            if not cand:
                continue
            try:
                p = Path(cand)
                if p.exists() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    if theme.name == "bw":
                        from PIL import Image

                        img = Image.open(p).convert("L").convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="PNG")
                        buf.seek(0)
                        return ImageReader(buf)
                    return ImageReader(str(p))
            except Exception:
                continue
        return None

    logo_reader = _logo_reader()

    def _nice_max(value):
        v = float(value)
        if v <= 0:
            return 100.0
        exp = math.floor(math.log10(v))
        base = 10.0 ** exp
        for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
            if v <= mult * base:
                return mult * base
        return 10.0 * base

    def _goal_info():
        if not goal:
            return None
        target = None
        try:
            target = goal.get("target") or goal.get("target_amount") or goal.get("amount")
        except Exception:
            target = None
        if target in (None, "", 0, "0"):
            return None
        target_dec = _q2(target)
        if target_dec <= 0:
            return None
        pct = max(0.0, min(1.0, float(closing / target_dec))) if target_dec else 0.0
        remaining = max(Decimal("0.00"), _q2(target_dec - closing))
        pace = target_amt if target_amt > 0 else avg_per_day
        est = None
        if remaining <= 0:
            est = "Reached"
        elif pace and pace > 0:
            days_needed = int(math.ceil(float(remaining / pace)))
            if days_needed <= 3650:
                est = (today + timedelta(days=days_needed)).strftime("%d %b %Y")
        return {"target": target_dec, "pct": pct, "remaining": remaining, "est": est}

    goal_info = _goal_info()

    # ----------------------------- layout ---------------------------------- #

    def _calendar_display():
        n = len(months)
        if n <= 0:
            return {"columns": 0, "show_months": [], "rows": 0, "chips": False}
        if n == 1:
            return {
                "columns": 1,
                "show_months": [months[0]],
                "rows": _weeks(*months[0]),
                "chips": False,
            }
        if n == 2:
            return {
                "columns": 2,
                "show_months": months,
                "rows": max(_weeks(*m) for m in months),
                "chips": False,
            }
        return {
            "columns": 1,
            "show_months": [months[-1]],
            "rows": _weeks(*months[-1]),
            "chips": True,
        }

    def _compute_layout(cfg, target_h=None, with_footer=True):
        header_h = cfg.header_mm * mm
        hero_h = cfg.hero_mm * mm
        tiles_h = cfg.tiles_mm * mm
        middle_h = cfg.chart_mm * mm
        footer_h = 18 * mm if with_footer else 0.0
        gap_header = cfg.gap_header_mm * mm
        gap_hero = cfg.gap_hero_mm * mm
        gap_tiles = cfg.gap_tiles_mm * mm
        gap_middle = cfg.gap_middle_mm * mm
        gap_footer = MM_XS * mm
        cal_disp = (
            _calendar_display()
            if include_calendar
            else {"columns": 0, "show_months": [], "rows": 0, "chips": False}
        )
        if include_calendar:
            cal_title_h = 7 * mm
            month_label_h = 5 * mm
            week_h = 4.6 * mm
            summary_h = 13 * mm
            chips_h = 8 * mm if cal_disp["chips"] else 0
            rows = cal_disp["rows"]
            cal_rows_h = rows * cfg.cal_min_mm * mm
        else:
            cal_title_h = month_label_h = week_h = summary_h = chips_h = cal_rows_h = 0
            rows = 0
            middle_h = max(middle_h, 68 * mm)

        def natural_height():
            return (
                header_h
                + gap_header
                + hero_h
                + gap_hero
                + tiles_h
                + gap_tiles
                + middle_h
                + gap_middle
                + cal_title_h
                + month_label_h
                + week_h
                + cal_rows_h
                + chips_h
                + summary_h
                + (gap_footer if include_calendar else 0)
                + footer_h
            )

        natural = natural_height()
        if target_h and target_h > natural:
            extra = target_h - natural
            add_mid = min(extra, 8 * mm)
            middle_h += add_mid
            extra -= add_mid
            add_hero = min(extra, 3 * mm)
            hero_h += add_hero
            extra -= add_hero
            add_tiles = min(extra, 2 * mm)
            tiles_h += add_tiles
            extra -= add_tiles
            if include_calendar and rows:
                cal_rows_h += extra
            else:
                middle_h += extra
            natural = natural_height()
        total = max(natural, target_h or 0)
        sections = {}
        y = 0.0
        sections["header"] = (y, header_h)
        y += header_h + gap_header
        sections["hero"] = (y, hero_h)
        y += hero_h + gap_hero
        sections["tiles"] = (y, tiles_h)
        y += tiles_h + gap_tiles
        sections["middle"] = (y, middle_h)
        y += middle_h + gap_middle
        if include_calendar:
            sections["cal_title"] = (y, cal_title_h)
            y += cal_title_h
            sections["cal_month_label"] = (y, month_label_h)
            y += month_label_h
            sections["cal_week"] = (y, week_h)
            y += week_h
            sections["cal_rows"] = (y, cal_rows_h)
            y += cal_rows_h
            if cal_disp["chips"]:
                sections["cal_chips"] = (y, chips_h)
                y += chips_h
            sections["cal_summary"] = (y, summary_h)
            y += summary_h
            y += gap_footer
        if with_footer:
            sections["footer"] = (y, footer_h)
            y += footer_h
        return {
            "sections": sections,
            "natural": natural,
            "total": total,
            "middle_h": middle_h,
            "hero_h": hero_h,
            "tiles_h": tiles_h,
            "cal_rows_h": cal_rows_h,
            "row_h": (cal_rows_h / rows if rows else 0),
            "cal": cal_disp,
            "gap": gap_middle,
            "gaps": {
                "header": gap_header,
                "hero": gap_hero,
                "tiles": gap_tiles,
                "middle": gap_middle,
                "footer": gap_footer,
            },
            "cfg": cfg,
        }

    # ----------------------------- section drawing ------------------------- #

    def _draw_header(c, x, y, w, h):
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "header"})
        logo_size = min(10 * mm, h - 3 * mm)
        lx = x
        ly = y + (h - logo_size) / 2
        if logo_reader is not None:
            try:
                c.drawImage(logo_reader, lx, ly, logo_size, logo_size, mask="auto")
                lx += logo_size + 3 * mm
            except Exception:
                pass

        brand_size = 15.0
        brand_h = _line_height(font_bold, brand_size)
        brand_baseline = _baseline_centered(y + h / 2, font_bold, brand_size)
        brand_max = max(30, w * 0.38 - (lx - x))
        brand = _clip_text("MoneyWise", font_bold, brand_size, brand_max)
        _draw_text(
            c,
            brand,
            lx,
            brand_baseline,
            font_bold,
            brand_size,
            theme.accent_dark,
        )

        title_size = 14.0
        sub_size = 7.5
        title_h = _line_height(font_bold, title_size)
        sub_h = _line_height(font_reg, sub_size)
        line_gap = 5.0
        block_h = title_h + line_gap + sub_h
        block_top = y + (h - block_h) / 2 + block_h
        title_top = block_top
        title_baseline = _baseline_from_top(title_top, font_bold, title_size)
        sub_top = title_top - title_h - line_gap
        sub_baseline = _baseline_from_top(sub_top, font_reg, sub_size)
        right_x = x + w
        right_max = w * 0.60
        _draw_fit_clip(
            c,
            "Savings Statement",
            right_x,
            title_baseline,
            font_bold,
            title_size,
            MIN_TEXT_PT,
            theme.text,
            right_max,
            align="right",
        )
        sub = f"{statement_no}  ·  Generated {generated_str}"
        _draw_fit_clip(
            c,
            sub,
            right_x,
            sub_baseline,
            font_reg,
            sub_size,
            MIN_TEXT_PT,
            theme.muted,
            right_max,
            align="right",
        )
        _line(c, x, y + 0.5, x + w, y + 0.5, theme.border_soft, 0.5)

    def _draw_tile(c, x, y, w, h, label, value):
        _round_rect(c, x, y, w, h, 5, fill=theme.surface, stroke=theme.border, lw=0.5)
        pad_x = 3 * mm
        pad_y = 2.5 * mm
        inner_w = w - 2 * pad_x
        label_size = MIN_TEXT_PT
        label_h = _line_height(font_reg, label_size)
        value = str(value)
        value_size = _fit_size(value, font_bold, inner_w, 13.0, 8.0)
        value_h = _line_height(font_bold, value_size)
        gap = 2.0
        block_h = label_h + gap + value_h
        block_top = y + (h + block_h) / 2.0
        label_baseline = _baseline_from_top(block_top, font_reg, label_size)
        _draw_text(
            c,
            _clip_text(label.upper(), font_reg, label_size, inner_w),
            x + pad_x,
            label_baseline,
            font_reg,
            label_size,
            theme.muted,
        )
        value_baseline = _baseline_from_top(
            block_top - label_h - gap, font_bold, value_size
        )
        _draw_text(
            c,
            _clip_text(value, font_bold, value_size, inner_w),
            x + pad_x,
            value_baseline,
            font_bold,
            value_size,
            theme.text,
        )
        _record_debug_rect(c, "hero_tiles", x, y, w, h, {"label": label})
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "tile"})

    def _draw_tiles(c, x, y, w, h):
        gap = MM_XS * mm
        tile_w = (w - 3 * gap) / 4.0
        target_label = money_short(target_amt, symbol) if target_amt > 0 else "—"
        avg_label = f"{money_short(avg_per_day, symbol)} / {target_label}"
        days_value = f"{days_saved} day" if days_saved == 1 else f"{days_saved} days"
        current_value = (
            f"{current_streak} day" if current_streak == 1 else f"{current_streak} days"
        )
        longest_value = (
            f"{longest_streak} day" if longest_streak == 1 else f"{longest_streak} days"
        )
        tiles = [
            ("Days saved", days_value),
            ("Current streak", current_value),
            ("Longest streak", longest_value),
            ("Avg per day vs target", avg_label),
        ]
        for i, (label, value) in enumerate(tiles):
            _draw_tile(c, x + i * (tile_w + gap), y, tile_w, h, label, value)

    def _draw_hero(c, x, y, w, h):
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "hero"})
        _round_rect(
            c,
            x,
            y,
            w,
            h,
            9,
            fill=theme.accent_soft if theme.name == "color" else theme.surface_tint,
            stroke=theme.border,
            lw=0.6,
        )
        pad = (6 * mm) if h >= 27 * mm else (4.5 * mm)
        inner_x = x + pad
        inner_y = y + pad
        inner_w = w - 2 * pad
        inner_h = h - 2 * pad
        col_gap = (6 * mm) if inner_w > 400 else (4 * mm)
        left_w = inner_w * 0.35
        centre_w = inner_w * 0.30
        right_w = inner_w - left_w - centre_w - 2 * col_gap
        if right_w < 92:
            left_w = inner_w * 0.32
            centre_w = inner_w * 0.29
            right_w = inner_w - left_w - centre_w - 2 * col_gap
        centre_x = inner_x + left_w + col_gap
        right_x = centre_x + centre_w + col_gap

        # Left zone: icon, bank name (1-2 lines) and period, all in reserved boxes.
        icon_size = min(9 * mm, inner_h * 0.48)
        name_size = 10.0
        name_w = left_w - icon_size - 2.5 * mm
        if name_w < 52:
            name_size = 8.5
        name_lines = _wrap_lines(
            bank.get("name") or "Piggy Bank", font_bold, name_size, name_w, 2
        )
        name_h = _line_height(font_bold, name_size)
        period_size = 7.5
        period_h = _line_height(font_reg, period_size)
        line_gap = 1.5
        period_gap = 3.0
        left_block_h = (
            len(name_lines) * name_h
            + (len(name_lines) - 1) * line_gap
            + period_gap
            + period_h
        )
        left_top = inner_y + (inner_h + left_block_h) / 2.0
        cursor_top = left_top
        for line in name_lines:
            _draw_text(
                c,
                line,
                inner_x + icon_size + 2.5 * mm,
                _baseline_from_top(cursor_top, font_bold, name_size),
                font_bold,
                name_size,
                theme.accent_dark,
            )
            cursor_top -= name_h + line_gap
        period_top = cursor_top + line_gap - period_gap
        period_text = period_label or f"{_fmt_date(psd)} – {_fmt_date(ped)}"
        _draw_fit_clip(
            c,
            period_text,
            inner_x + icon_size + 2.5 * mm,
            _baseline_from_top(period_top, font_reg, period_size),
            font_reg,
            period_size,
            MIN_TEXT_PT,
            theme.muted,
            name_w,
        )
        icon_y = left_top - left_block_h / 2.0 - icon_size / 2.0
        _draw_piggy(c, inner_x, icon_y, icon_size, icon_size)

        # Centre zone: fixed label row plus a fixed height for the amount.
        label_size = 7.5
        label_h = _line_height(font_bold, label_size)
        amount_gap = 6.0
        amount_text = money(closing, symbol)
        amount_size = _fit_size(amount_text, font_bold, centre_w, 24.0, 15.0)
        amount_h = _line_height(font_bold, amount_size)
        centre_block_h = label_h + amount_gap + amount_h
        centre_top = inner_y + (inner_h + centre_block_h) / 2.0
        _draw_text(
            c,
            "TOTAL SAVED",
            centre_x,
            _baseline_from_top(centre_top, font_bold, label_size),
            font_bold,
            label_size,
            theme.muted,
        )
        amount_top = centre_top - label_h - amount_gap
        _draw_text(
            c,
            _clip_text(amount_text, font_bold, amount_size, centre_w),
            centre_x,
            _baseline_from_top(amount_top, font_bold, amount_size),
            font_bold,
            amount_size,
            theme.text,
        )

        # Right zone: progress row, bar, daily target and ahead/behind line.
        target_label = money_short(target_amt, symbol) if target_amt > 0 else "—"
        daily_text = f"Daily target {target_label}"
        pct = 0.0
        status_text = ""
        pct_text = ""
        status_color = theme.muted
        progress_label = ""
        if goal_info:
            pct = float(goal_info["pct"])
            diff = _q2(closing - goal_info["target"])
            ahead = diff >= 0
            status_text = (
                f"{money_short(abs(diff), symbol)} ahead of target"
                if ahead
                else f"{money_short(abs(diff), symbol)} behind target"
            )
            status_color = theme.deposit if ahead else theme.amber
            pct_text = f"{pct * 100:.0f}%"
            progress_label = "Goal progress"
        elif target_amt > 0 and elapsed_days > 0:
            pace_total = _q2(target_amt * Decimal(elapsed_days))
            raw_pct = float(period_total / pace_total) if pace_total > 0 else 0.0
            pct = max(0.0, min(1.0, raw_pct))
            diff = _q2(period_total - pace_total)
            ahead = diff >= 0
            status_text = (
                f"{money_short(abs(diff), symbol)} ahead of target"
                if ahead
                else f"{money_short(abs(diff), symbol)} behind target"
            )
            status_color = theme.deposit if ahead else theme.amber
            pct_text = f"{pct * 100:.0f}%"
            progress_label = "Target progress"
        elif target_amt > 0:
            pct_text = "0%"
            progress_label = "Target progress"
            status_text = "Target has not started yet"
            status_color = theme.muted

        if progress_label:
            row1_size = 7.5
            pct_size = 8.5
            row1_h = max(
                _line_height(font_reg, row1_size), _line_height(font_bold, pct_size)
            )
            daily_size = 7.5
            daily_h = _line_height(font_reg, daily_size)
            status_size = 7.5
            status_h = _line_height(font_bold, status_size)
            bar_h = 5.0
            gap_1 = 3.0
            gap_2 = 6.0
            gap_3 = 2.5
            right_block_h = row1_h + gap_1 + bar_h + gap_2 + daily_h
            if status_text:
                right_block_h += gap_3 + status_h
            if right_block_h > inner_h:
                gap_2 = 3.0
                gap_3 = 1.0
                right_block_h = row1_h + gap_1 + bar_h + gap_2 + daily_h
                if status_text:
                    right_block_h += gap_3 + status_h
            right_top = inner_y + (inner_h + right_block_h) / 2.0
            _draw_text(
                c,
                progress_label,
                right_x,
                _baseline_from_top(right_top, font_reg, row1_size),
                font_reg,
                row1_size,
                theme.muted,
            )
            _draw_text(
                c,
                pct_text,
                right_x + right_w,
                _baseline_from_top(right_top, font_bold, pct_size),
                font_bold,
                pct_size,
                theme.accent_dark,
                align="right",
            )
            bar_top = right_top - row1_h - gap_1
            bar_y = bar_top - bar_h
            _round_rect(
                c,
                right_x,
                bar_y,
                right_w,
                bar_h,
                bar_h / 2,
                fill=theme.surface,
                stroke=theme.border,
                lw=0.5,
            )
            if pct > 0:
                _round_rect(
                    c,
                    right_x,
                    bar_y,
                    max(bar_h, min(right_w, right_w * pct)),
                    bar_h,
                    bar_h / 2,
                    fill=theme.accent,
                    stroke=None,
                )
            daily_top = bar_y - gap_2
            _draw_fit_clip(
                c,
                daily_text,
                right_x,
                _baseline_from_top(daily_top, font_reg, daily_size),
                font_reg,
                daily_size,
                MIN_TEXT_PT,
                theme.muted,
                right_w,
            )
            if status_text:
                status_top = daily_top - daily_h - gap_3
                _draw_fit_clip(
                    c,
                    status_text,
                    right_x,
                    _baseline_from_top(status_top, font_bold, status_size),
                    font_bold,
                    status_size,
                    MIN_TEXT_PT,
                    status_color,
                    right_w,
                )
        else:
            empty_size = 7.5
            _draw_text(
                c,
                "No daily target set",
                right_x,
                _baseline_centered(inner_y + inner_h / 2.0, font_reg, empty_size),
                font_reg,
                empty_size,
                theme.muted,
            )


    def _draw_chart(c, x, y, w, h, cfg):
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "chart"})
        _round_rect(c, x, y, w, h, 9, fill=theme.surface, stroke=theme.border, lw=0.6)
        pad = (4 * mm) if h < 150 else (5 * mm)
        inner_x = x + pad
        inner_y = y + pad
        inner_w = w - 2 * pad
        inner_h = h - 2 * pad
        top = inner_y + inner_h

        title_size = 10.0
        title_h = _line_height(font_bold, title_size)
        title_baseline = _baseline_from_top(top, font_bold, title_size)
        _draw_fit_clip(
            c,
            "Cumulative savings",
            inner_x,
            title_baseline,
            font_bold,
            title_size,
            MIN_TEXT_PT,
            theme.text,
            inner_w * 0.62,
        )
        sub_size = 7.5
        _draw_fit_clip(
            c,
            "This period",
            inner_x + inner_w,
            _baseline_from_top(top, font_reg, sub_size),
            font_reg,
            sub_size,
            MIN_TEXT_PT,
            theme.muted,
            inner_w * 0.32,
            align="right",
        )

        legend_size = MIN_TEXT_PT
        legend_h = _line_height(font_reg, legend_size)
        legend_top = top - title_h - 3.0
        legend_center = legend_top - legend_h / 2.0
        legend_baseline = _baseline_centered(legend_center, font_reg, legend_size)

        last_value = chart_series[-1][1] if chart_series else Decimal("0.00")
        has_value = any(v for _, v in chart_series)
        end_label = money_short(last_value, symbol) if has_value else None
        end_box_w = 0.0
        end_box_h = 0.0
        if end_label:
            end_size = MIN_TEXT_PT
            end_box_w = _tw(end_label, font_bold, end_size) + 8.0
            end_box_h = _line_height(font_bold, end_size) + 4.0
        end_x = inner_x + inner_w - end_box_w
        legend_max_w = max(40.0, end_x - inner_x - 8.0)

        # Legend text, kept in its own measured row.
        lx = inner_x
        _line(c, lx, legend_center, lx + 12, legend_center, theme.accent, 1.6)
        _draw_text(
            c, "Savings", lx + 16, legend_baseline, font_reg, legend_size, theme.muted
        )
        lx2 = lx + 16 + _tw("Savings", font_reg, legend_size) + 12
        c.saveState()
        c.setStrokeColor(_hx(theme.faint))
        c.setLineWidth(0.9)
        c.setDash([2.2, 2.2], 0)
        c.line(lx2, legend_center, lx2 + 12, legend_center)
        c.restoreState()
        _draw_text(
            c,
            "Target pace",
            lx2 + 16,
            legend_baseline,
            font_reg,
            legend_size,
            theme.muted,
        )

        xlabel_size = MIN_TEXT_PT
        xlabel_h = _line_height(font_reg, xlabel_size)
        plot_bottom = inner_y + xlabel_h + 4.0
        plot_top = legend_top - legend_h - 8.0
        plot_h = plot_top - plot_bottom
        if plot_h <= 18 or inner_w <= 40:
            return

        vals = [float(v) for _, v in chart_series] or [0.0]
        vmax_data = max(vals)
        target_max = (
            float(target_amt * Decimal(max(1, len(chart_series)))) if target_amt > 0 else 0.0
        )
        vmax = _nice_max(max(vmax_data, target_max, 1.0))
        vmin_raw = min(vals + [0.0])
        vmin = -_nice_max(abs(vmin_raw)) if vmin_raw < 0 else 0.0
        if vmax <= vmin:
            vmax = vmin + 100

        grid_n = 4
        y_ticks = []
        for i in range(grid_n + 1):
            value = vmin + (vmax - vmin) * i / grid_n
            y_ticks.append((value, money_short(value, symbol)))
        y_label_w = max(_tw(label, font_reg, MIN_TEXT_PT) for _, label in y_ticks)
        y_axis_w = min(max(y_label_w + 5.0, 28.0), inner_w * 0.32)
        plot_x = inner_x + y_axis_w + 3.0
        plot_w = inner_x + inner_w - plot_x
        if plot_w <= 20:
            return

        def map_y(v):
            return plot_bottom + plot_h * ((float(v) - vmin) / (vmax - vmin))

        def map_x(idx):
            if len(chart_series) <= 1:
                return plot_x + plot_w / 2.0
            return plot_x + plot_w * (idx / float(len(chart_series) - 1))

        for value, label in y_ticks:
            yy = map_y(value)
            _line(c, plot_x, yy, plot_x + plot_w, yy, theme.grid, 0.45)
            _draw_fit_clip(
                c,
                label,
                plot_x - 4.0,
                _baseline_centered(yy, font_reg, MIN_TEXT_PT),
                font_reg,
                MIN_TEXT_PT,
                MIN_TEXT_PT,
                theme.faint,
                y_axis_w - 2.0,
                align="right",
            )

        pace_segments = []
        if target_amt > 0 and len(chart_series) > 1:
            pace_p1 = (plot_x, map_y(0))
            pace_p2 = (plot_x + plot_w, map_y(float(target_amt) * (len(chart_series) - 1)))
            pace_segments.append((pace_p1, pace_p2))
            c.saveState()
            c.setStrokeColor(_hx(theme.faint))
            c.setLineWidth(0.9)
            c.setDash([2.2, 2.2], 0)
            c.line(pace_p1[0], pace_p1[1], pace_p2[0], pace_p2[1])
            c.restoreState()

        line_segments = []
        if len(chart_series) > 1:
            base_y = map_y(0)
            p = c.beginPath()
            p.moveTo(map_x(0), base_y)
            for i, (_, v) in enumerate(chart_series):
                p.lineTo(map_x(i), map_y(v))
            p.lineTo(map_x(len(chart_series) - 1), base_y)
            p.close()
            c.saveState()
            c.clipPath(p, stroke=0, fill=0)
            bands = 36
            band_h = plot_h / bands
            for i in range(bands):
                yy = plot_bottom + i * band_h
                col = _blend(
                    theme.chart_fill_bottom,
                    theme.chart_fill_top,
                    i / max(1, bands - 1),
                )
                c.setFillColor(col)
                c.rect(plot_x, yy, plot_w, band_h + 0.6, stroke=0, fill=1)
            c.restoreState()

            c.saveState()
            c.setStrokeColor(_hx(theme.accent))
            c.setLineWidth(1.7)
            c.setLineJoin(1)
            c.setLineCap(1)
            p = c.beginPath()
            p.moveTo(map_x(0), map_y(chart_series[0][1]))
            prev = (map_x(0), map_y(chart_series[0][1]))
            for i, (_, v) in enumerate(chart_series[1:], start=1):
                point = (map_x(i), map_y(v))
                p.lineTo(point[0], point[1])
                line_segments.append((prev, point))
                prev = point
            c.drawPath(p, stroke=1, fill=0)
            c.restoreState()

        if chart_series:
            n_ticks = min(6, max(2, len(chart_series)))
            while n_ticks > 2:
                probe = []
                for i in range(n_ticks):
                    probe.append(
                        int(round(i * (len(chart_series) - 1) / (n_ticks - 1)))
                    )
                probe = sorted(set(probe))
                xs = [map_x(i) for i in probe]
                gaps = [
                    xs[j + 1] - xs[j] for j in range(len(xs) - 1) if xs[j + 1] > xs[j]
                ]
                labels = [
                    f"{chart_series[i][0].day} {chart_series[i][0].strftime('%b')}"
                    for i in probe
                ]
                widest = max(_tw(lbl, font_reg, xlabel_size) for lbl in labels)
                if not gaps or min(gaps) >= widest + 5.0:
                    break
                n_ticks -= 1
            tick_indices = []
            for i in range(n_ticks):
                tick_indices.append(
                    int(round(i * (len(chart_series) - 1) / (n_ticks - 1)))
                )
            tick_indices = sorted(set(tick_indices))
            if len(tick_indices) == 1:
                tick_indices = [0, len(chart_series) - 1]
            for i in tick_indices:
                d, _ = chart_series[i]
                xx = map_x(i)
                lbl = f"{d.day} {d.strftime('%b')}"
                if i == 0:
                    _draw_text(
                        c,
                        lbl,
                        max(plot_x, xx),
                        _baseline_from_top(plot_bottom - 4.0, font_reg, xlabel_size),
                        font_reg,
                        xlabel_size,
                        theme.muted,
                        align="left",
                    )
                elif i == tick_indices[-1]:
                    _draw_text(
                        c,
                        lbl,
                        min(plot_x + plot_w, xx),
                        _baseline_from_top(plot_bottom - 4.0, font_reg, xlabel_size),
                        font_reg,
                        xlabel_size,
                        theme.muted,
                        align="right",
                    )
                else:
                    _draw_text(
                        c,
                        lbl,
                        xx,
                        _baseline_from_top(plot_bottom - 4.0, font_reg, xlabel_size),
                        font_reg,
                        xlabel_size,
                        theme.muted,
                        align="center",
                    )
                _line(c, xx, plot_bottom - 2.0, xx, plot_bottom, theme.grid, 0.5)

        if end_label and chart_series:
            last_idx = len(chart_series) - 1
            px, py = map_x(last_idx), map_y(chart_series[last_idx][1])
            c.saveState()
            c.setFillColor(_hx(theme.surface))
            c.setStrokeColor(_hx(theme.accent))
            c.setLineWidth(1.3)
            c.circle(px, py, 2.6, stroke=1, fill=1)
            c.restoreState()
            candidates = [
                (px - end_box_w / 2.0, py + 6.0),
                (px - end_box_w / 2.0, py - 6.0 - end_box_h),
                (px + 6.0, py - end_box_h / 2.0),
                (px - 6.0 - end_box_w, py - end_box_h / 2.0),
                (plot_x + 2.0, plot_top - end_box_h - 2.0),
                (plot_x + plot_w - end_box_w - 2.0, plot_top - end_box_h - 2.0),
                (plot_x + 2.0, plot_bottom + 2.0),
                (plot_x + plot_w - end_box_w - 2.0, plot_bottom + 2.0),
            ]
            chosen = None
            for bx, by in candidates:
                box = (bx, by, bx + end_box_w, by + end_box_h)
                inside = (
                    bx >= plot_x + 1.0
                    and box[2] <= plot_x + plot_w - 1.0
                    and by >= plot_bottom + 1.0
                    and box[3] <= plot_top - 1.0
                )
                if not inside:
                    continue
                if box[0] <= px <= box[2] and box[1] <= py <= box[3]:
                    continue
                if any(
                    _segment_hits_rect(p1, p2, box)
                    for p1, p2 in line_segments + pace_segments
                ):
                    continue
                chosen = (bx, by)
                break
            if chosen is None and end_label:
                bx = end_x
                by = legend_center - end_box_h / 2.0
                _round_rect(
                    c,
                    bx,
                    by,
                    end_box_w,
                    end_box_h,
                    2.5,
                    fill=theme.surface,
                    stroke=theme.border_soft,
                    lw=0.4,
                )
                _draw_text(
                    c,
                    end_label,
                    bx + end_box_w / 2.0,
                    _baseline_centered(by + end_box_h / 2.0, font_bold, MIN_TEXT_PT),
                    font_bold,
                    MIN_TEXT_PT,
                    theme.accent,
                    align="center",
                )
            elif chosen is not None:
                bx, by = chosen
                _round_rect(
                    c,
                    bx,
                    by,
                    end_box_w,
                    end_box_h,
                    2.5,
                    fill=theme.surface,
                    stroke=theme.border_soft,
                    lw=0.4,
                )
                _draw_text(
                    c,
                    end_label,
                    bx + end_box_w / 2.0,
                    _baseline_centered(by + end_box_h / 2.0, font_bold, MIN_TEXT_PT),
                    font_bold,
                    MIN_TEXT_PT,
                    theme.accent,
                    align="center",
                )

        if not has_value:
            _draw_text(
                c,
                "No savings in this period yet.",
                plot_x + plot_w / 2.0,
                _baseline_centered(plot_bottom + plot_h / 2.0, font_reg, 8.0),
                font_reg,
                8.0,
                theme.faint,
                align="center",
            )

    def _draw_contributors(c, x, y, w, h):
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "contributors"})
        _round_rect(c, x, y, w, h, 9, fill=theme.surface, stroke=theme.border, lw=0.6)
        pad = 4 * mm
        inner_x = x + pad
        inner_y = y + pad
        inner_w = w - 2 * pad
        inner_h = h - 2 * pad
        top = inner_y + inner_h

        title_size = 10.0
        title_h = _line_height(font_bold, title_size)
        _draw_fit_clip(
            c,
            "Who saved what",
            inner_x,
            _baseline_from_top(top, font_bold, title_size),
            font_bold,
            title_size,
            MIN_TEXT_PT,
            theme.text,
            inner_w,
        )
        sub_size = 7.5
        sub_h = _line_height(font_reg, sub_size)
        sub_top = top - title_h - 1.0
        _draw_text(
            c,
            "Deposits this period",
            inner_x,
            _baseline_from_top(sub_top, font_reg, sub_size),
            font_reg,
            sub_size,
            theme.muted,
        )

        caption_size = MIN_TEXT_PT
        caption_h = _line_height(font_reg, caption_size)
        caption_top = inner_y + caption_h
        rows_top = sub_top - sub_h - 3 * mm
        rows_bottom = inner_y + caption_h + 2.0
        available = max(0.0, rows_top - rows_bottom)
        row_gap = MM_XS * mm
        row_h = max(10 * mm, (available - row_gap) / 2.0)

        meta_texts = []
        for key in contributor_keys:
            amount = contributor_totals.get(key, Decimal("0.00"))
            pct = (float(amount / period_deposits) * 100.0) if period_deposits > 0 else 0.0
            meta_texts.append(f"{money_short(amount, symbol)} · {pct:.0f}%")
        meta_size = 7.5
        max_meta_w = max(_tw(t, font_bold, meta_size) for t in meta_texts)
        meta_w = min(max(max_meta_w + 6.0, 60.0), inner_w * 0.45)
        col_gap = 3 * mm
        meta_x = inner_x + inner_w
        name_size = 8.5
        name_h = _line_height(font_bold, name_size)
        dot = 2.2 * mm
        name_text_x = inner_x + dot + 4.0
        name_text_w = meta_x - col_gap - name_text_x

        for idx, key in enumerate(contributor_keys):
            row_top = rows_top - idx * (row_h + row_gap)
            row_bottom = row_top - row_h
            _record_debug_rect(
                c,
                "panels",
                inner_x,
                row_bottom,
                inner_w,
                row_h,
                {"name": "contributor_row", "contributor": key},
            )
            amount = contributor_totals.get(key, Decimal("0.00"))
            pct = (float(amount / period_deposits) * 100.0) if period_deposits > 0 else 0.0
            if theme.name == "bw":
                fill_col = theme.text if idx == 0 else theme.muted
            else:
                fill_col = theme.accent if idx == 0 else theme.deposit

            name_top = row_top - 0.5
            dot_cx = inner_x + dot / 2.0 + 1.0
            dot_cy = name_top - name_h / 2.0
            c.saveState()
            c.setFillColor(_hx(fill_col))
            c.circle(dot_cx, dot_cy, dot / 2.0, stroke=0, fill=1)
            c.restoreState()
            _draw_fit_clip(
                c,
                contributor_names[key],
                name_text_x,
                _baseline_from_top(name_top, font_bold, name_size),
                font_bold,
                name_size,
                MIN_TEXT_PT,
                theme.text,
                name_text_w,
            )
            _draw_fit_clip(
                c,
                meta_texts[idx],
                meta_x,
                _baseline_centered(name_top - name_h / 2.0, font_bold, meta_size),
                font_bold,
                meta_size,
                MIN_TEXT_PT,
                theme.deposit if theme.name == "color" and idx == 1 else theme.text,
                meta_w,
                align="right",
            )

            bar_h = min(3.2 * mm, row_h * 0.30)
            bar_y = row_bottom + 2.0
            bar_w = inner_w
            _round_rect(
                c,
                inner_x,
                bar_y,
                bar_w,
                bar_h,
                bar_h / 2.0,
                fill=theme.neutral_bar,
                stroke=None,
            )
            fill_w = bar_w * max(0.0, min(1.0, pct / 100.0))
            if fill_w > 0:
                _round_rect(
                    c,
                    inner_x,
                    bar_y,
                    max(bar_h, fill_w),
                    bar_h,
                    bar_h / 2.0,
                    fill=fill_col,
                    stroke=None,
                )

        if period_deposits > 0:
            caption = f"{money_short(period_deposits, symbol)} saved together in this period"
        else:
            caption = "No savings recorded in this period yet."
        _draw_fit_clip(
            c,
            caption,
            inner_x,
            _baseline_from_top(caption_top, font_reg, caption_size),
            font_reg,
            caption_size,
            MIN_TEXT_PT,
            theme.muted,
            inner_w,
        )
        _record_debug_rect(c, "contributor_bars", x, y, w, h)

    # ----------------------------- calendar -------------------------------- #

    def _month_label(y, m):
        try:
            return f"{calendar.month_name[m]} {y}"
        except Exception:
            return f"{m:02d}/{y}"

    def _draw_legend(c, x, y, items):
        cur = x
        for label, fill, stroke, mark in items:
            sw = 6.5
            _draw_swatch(
                c,
                cur,
                y - 1.5,
                sw,
                sw,
                fill,
                stroke,
                cross=(mark == "cross"),
                minus=(mark == "minus"),
                tick=(mark == "tick"),
            )
            cur += sw + 3.5
            _draw_text(c, label, cur, y - 1.0, font_reg, MIN_TEXT_PT, theme.muted)
            cur += _tw(label, font_reg, MIN_TEXT_PT) + 9

    def _draw_calendar(c, layout, cfg):
        sec = layout["sections"]
        cal = layout["cal"]
        top = page_h - margin

        def band(name):
            off, h = sec[name]
            return top - off - h, h

        y0, hh = band("cal_title")
        _draw_text(
            c,
            "Daily savings calendar",
            margin,
            _text_baseline(y0 + hh - 2, 10.0),
            font_bold,
            10.0,
            theme.text,
        )
        items = [
            ("Saved", theme.saved_light, theme.saved_mid, "tick"),
            ("Missed", theme.missed_soft, theme.border_soft, "cross"),
            ("Upcoming", theme.surface, theme.future_border, None),
            ("Withdrawal", theme.missed_soft, theme.border_soft, "minus"),
        ]
        total_w = sum(
            6.5 + 3.5 + _tw(lbl, font_reg, MIN_TEXT_PT) + 9 for lbl, *_ in items
        ) - 9
        _draw_legend(c, margin + content_w - total_w, y0 + hh / 2 + 1, items)
        _line(c, margin, y0 - 1, margin + content_w, y0 - 1, theme.border_soft, 0.5)

        cols = cal["columns"]
        months_shown = cal["show_months"]
        if not months_shown:
            return
        gutter = 8 * mm if cols > 1 else 0
        area_w = (content_w - gutter * (cols - 1)) / cols
        areas = []
        for i, (yy_, mm_) in enumerate(months_shown):
            ax = margin + i * (area_w + gutter)
            areas.append((ax, area_w, (yy_, mm_)))

        y0, hh = band("cal_month_label")
        for ax, aw, (yy_, mm_) in areas:
            _draw_text(
                c,
                _month_label(yy_, mm_),
                ax + aw / 2,
                y0 + 4,
                font_bold,
                8.5,
                theme.accent_dark,
                align="center",
            )

        y0, hh = band("cal_week")
        for ax, aw, _ in areas:
            cw = min(aw / 7.0, 68.0)
            gx = ax + (aw - cw * 7) / 2
            for wd, nm in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
                _draw_text(
                    c,
                    nm,
                    gx + wd * cw + cw / 2,
                    y0 + 4,
                    font_reg,
                    MIN_TEXT_PT,
                    theme.muted,
                    align="center",
                )

        y0, hh = band("cal_rows")
        rows = cal["rows"] or 1
        row_h = hh / rows
        for ax, aw, (yy_, mm_) in areas:
            cw = min(aw / 7.0, 68.0)
            gx = ax + (aw - cw * 7) / 2
            dim = calendar.monthrange(yy_, mm_)[1]
            first_wd = date(yy_, mm_, 1).weekday()
            _record_debug_rect(
                c, "calendar_grid_zones", ax, y0, aw, hh, {"month": _month_label(yy_, mm_)}
            )
            for wk in range(rows):
                for wd in range(7):
                    dayn = wk * 7 + wd - first_wd + 1
                    if dayn < 1 or dayn > dim:
                        continue
                    d = date(yy_, mm_, dayn)
                    rx = gx + wd * cw + 1.0
                    ry = y0 + hh - (wk + 1) * row_h + 1.2
                    rw = cw - 2.0
                    rh = row_h - 2.4
                    rec = day_map.get(d)
                    in_period = psd <= d <= ped
                    dep = rec["dep"] if rec else Decimal("0.00")
                    wdv = rec["wd"] if rec else Decimal("0.00")
                    is_saved = dep > 0
                    is_wd = wdv > 0
                    is_missed = (
                        not is_saved
                        and not is_wd
                        and in_period
                        and highlight_missed
                        and d <= min(today, ped)
                    )
                    if is_saved:
                        fill, stroke = theme.saved_light, theme.saved_mid
                    elif is_wd or is_missed:
                        fill, stroke = theme.missed_soft, theme.border_soft
                    else:
                        fill, stroke = theme.surface, theme.future_border
                    lw = 0.5
                    if d == today:
                        stroke, lw = theme.accent, 1.2
                    _round_rect(c, rx, ry, rw, rh, 3, fill=fill, stroke=stroke, lw=lw)
                    day_col = theme.text if in_period else theme.faint
                    _draw_text(c, str(dayn), rx + 3.2, ry + rh - 8.4, font_bold, 7.0, day_col)

                    net = _q2(dep - wdv)
                    if is_saved and rh >= 22:
                        col = theme.withdrawal if net < 0 else theme.saved_text
                        _draw_fit(
                            c,
                            money_short(dep if net >= 0 else net, symbol),
                            rx + rw / 2,
                            ry + rh * 0.36,
                            font_bold,
                            7.6,
                            MIN_TEXT_PT,
                            col,
                            rw - 5,
                            align="center",
                        )
                        if is_wd:
                            _draw_minus(
                                c, rx + rw - 6, ry + rh - 5.5, 4.2, theme.withdrawal, 1.1
                            )
                    elif is_wd and rh >= 20:
                        _draw_minus(
                            c,
                            rx + rw / 2,
                            ry + rh * 0.44,
                            min(rw, rh) * 0.22,
                            theme.withdrawal,
                            1.6,
                        )
                        _draw_fit(
                            c,
                            money_short(wdv, symbol),
                            rx + rw / 2,
                            ry + 3.4,
                            font_reg,
                            MIN_TEXT_PT,
                            MIN_TEXT_PT,
                            theme.withdrawal,
                            rw - 5,
                            align="center",
                        )
                    elif is_missed:
                        _draw_cross(
                            c,
                            rx + rw / 2,
                            ry + rh * 0.40,
                            min(rw, rh) * 0.22,
                            theme.missed_marker,
                            1.3,
                        )

                    if is_saved and not is_wd and rh >= 26:
                        dep_by = rec["by_dep"]
                        both = [k for k in contributor_keys if dep_by.get(k, Decimal("0.00")) > 0]
                        if len(both) > 1:
                            txt = None
                            for names in (contributor_names, contributor_short):
                                candidate = " · ".join(
                                    f"{names[k]} {money_short(dep_by[k], symbol)}"
                                    for k in contributor_keys
                                )
                                if _tw(candidate, font_reg, MIN_TEXT_PT) <= rw - 5:
                                    txt = candidate
                                    break
                            if txt:
                                _draw_text(
                                    c,
                                    txt,
                                    rx + rw / 2,
                                    ry + 2.5,
                                    font_reg,
                                    MIN_TEXT_PT,
                                    theme.muted,
                                    align="center",
                                )

                    _record_debug_rect(
                        c,
                        "calendar_cells",
                        rx,
                        ry,
                        rw,
                        rh,
                        {
                            "day": d.isoformat(),
                            "status": (
                                "saved"
                                if is_saved
                                else "withdrawal"
                                if is_wd
                                else "missed"
                                if is_missed
                                else "upcoming"
                            ),
                        },
                    )

        if "cal_chips" in sec:
            y0, hh = band("cal_chips")
            cx = margin
            for (yy_, mm_) in months[:-1]:
                st = month_stats[(yy_, mm_)]
                label = (
                    f"{calendar.month_abbr[mm_]} {yy_}: "
                    f"{money_short(st['total'], symbol, signed=True)}"
                )
                tw = _tw(label, font_reg, MIN_TEXT_PT)
                chip_w = tw + 14
                _round_rect(
                    c,
                    cx,
                    y0 + 1.5,
                    chip_w,
                    hh - 3,
                    4,
                    fill=theme.surface_tint,
                    stroke=theme.border_soft,
                    lw=0.4,
                )
                _draw_text(
                    c, label, cx + 7, y0 + hh / 2 - 2.4, font_reg, MIN_TEXT_PT, theme.muted
                )
                cx += chip_w + 6

    def _draw_summary(c, layout):
        sec = layout["sections"]
        if "cal_summary" not in sec:
            return
        top = page_h - margin
        off, h = sec["cal_summary"]
        y0 = top - off - h
        _round_rect(
            c, margin, y0, content_w, h, 7, fill=theme.surface_tint, stroke=theme.border, lw=0.5
        )
        pad = 10
        _record_debug_rect(c, "panels", margin, y0, content_w, h, {"name": "summary"})
        metrics = [
            ("Opening", opening, theme.text),
            ("Deposits", period_deposits, theme.deposit),
            ("Withdrawals", period_withdrawals, theme.withdrawal),
            ("Net change", period_total, theme.text),
            ("Closing balance", closing, theme.accent_dark),
        ]
        cw = (content_w - 2 * pad) / len(metrics)
        for i, (label, value, col) in enumerate(metrics):
            x = margin + pad + i * cw
            if i:
                _line(c, x - 6, y0 + pad * 0.5, x - 6, y0 + h - pad * 0.5, theme.border_soft, 0.5)
            _draw_text(
                c,
                label.upper(),
                x,
                y0 + h - pad - 3.5,
                font_bold,
                MIN_TEXT_PT,
                theme.muted,
            )
            _draw_fit(
                c,
                money_short(value, symbol),
                x,
                y0 + pad - 1.0,
                font_bold,
                10.0,
                7.0,
                col,
                cw - 10,
            )

    # ----------------------------- footer ---------------------------------- #

    verify_blob = f"MW|{statement_no}|{statement_file_hash or 'pending'}|{closing}"
    qr_reader = _qr_image(verify_blob)

    def _hash_display():
        if statement_file_hash:
            return short_hash(statement_file_hash, 16, 12)
        return "pending (hash computed after render)"

    def _draw_footer(c, x, y, w, h, page_no):
        _record_debug_rect(c, "panels", x, y, w, h, {"name": "footer"})
        _line(c, x, y + h - 0.5, x + w, y + h - 0.5, theme.border, 0.6)
        pad = 4
        qs = min(h - 2 * pad, 14 * mm)
        if qr_reader is not None:
            try:
                c.drawImage(qr_reader, x, y + (h - qs) / 2, qs, qs, mask="auto")
            except Exception:
                qs = 0
        else:
            qs = 0
        tx = x + (qs + 7 if qs else 0)
        _draw_text(
            c,
            "VERIFIED STATEMENT",
            tx,
            y + h - pad - 5.0,
            font_bold,
            MIN_TEXT_PT,
            theme.muted,
        )
        _draw_fit(
            c,
            f"SHA-256  {_hash_display()}",
            tx,
            y + h - pad - 15.5,
            font_reg,
            7.2,
            MIN_TEXT_PT,
            theme.text,
            w * 0.55,
        )
        _draw_fit(
            c,
            f"{statement_no}  ·  Generated {generated_str}",
            tx,
            y + h - pad - 26.0,
            font_reg,
            MIN_TEXT_PT,
            MIN_TEXT_PT,
            theme.faint,
            w * 0.55,
        )
        sig_w = 42 * mm
        sx = x + w - pad - sig_w
        _draw_text(
            c,
            "Authorised signature",
            sx + sig_w,
            y + h - pad - 5.0,
            font_bold,
            MIN_TEXT_PT,
            theme.muted,
            align="right",
        )
        _line(c, sx, y + h * 0.42, sx + sig_w, y + h * 0.42, theme.faint, 0.6)
        _draw_text(
            c,
            "For MoneyWise",
            sx + sig_w,
            y + h * 0.42 - 7,
            font_reg,
            MIN_TEXT_PT,
            theme.faint,
            align="right",
        )

    def _draw_page_number(c, page_no):
        _draw_text(
            c,
            f"Page {page_no}",
            margin + content_w / 2,
            margin * 0.42,
            font_reg,
            7.0,
            theme.faint,
            align="center",
        )

    # ----------------------------- records table --------------------------- #

    def _record_lines():
        lines = []
        if detail == "none":
            return lines
        if detail == "summary":
            for ym in months:
                if month_subtotals[ym]["count"] > 0:
                    lines.append(("subtotal", ym, month_subtotals[ym]))
            if lines:
                lines.append(("grand", None, None))
            return lines
        last = None
        for r in record_rows:
            if last is not None and r["month"] != last:
                lines.append(("subtotal", last, month_subtotals[last]))
            lines.append(("row", r, None))
            last = r["month"]
        if last is not None:
            lines.append(("subtotal", last, month_subtotals[last]))
            lines.append(("grand", None, None))
        return lines

    def _line_h(kind):
        return 15.0 if kind == "row" else 14.0

    def _table_height(lines):
        return 18.0 + sum(_line_h(k) for k, _, _ in lines) + 6.0

    def _clip(text, font, size, max_w):
        t = str(text or "")
        if _tw(t, font, size) <= max_w:
            return t
        ell = "…"
        while t and _tw(t + ell, font, size) > max_w:
            t = t[:-1]
        return (t + ell) if t else ell

    def _draw_table(c, lines, x, y, w, h):
        top = y + h
        header_h = 18.0
        fracs = [0.13, 0.115, 0.115, 0.13, 0.135, 0.375]
        xs = [x]
        for f in fracs:
            xs.append(xs[-1] + w * f)
        xs[-1] = x + w
        labels = [
            "Date",
            _clip(contributor_names["me"], font_bold, 7.0, w * fracs[1] - 8),
            _clip(contributor_names["her"], font_bold, 7.0, w * fracs[2] - 8),
            "Net",
            "Balance",
            "Note",
        ]
        _round_rect(
            c,
            x,
            top - header_h,
            w,
            header_h,
            4,
            fill=theme.accent_soft,
            stroke=theme.border_soft,
            lw=0.4,
        )
        for i, lab in enumerate(labels):
            align = "right" if i in (1, 2, 3, 4) else "left"
            tx = xs[i + 1] - 4 if align == "right" else xs[i] + 4
            _draw_fit(
                c,
                lab,
                tx,
                top - header_h + 5.5,
                font_bold,
                7.0,
                MIN_TEXT_PT,
                theme.muted,
                w * fracs[i] - 8,
                align=align,
            )
        yy = top - header_h
        idx = 0
        for kind, payload, st in lines:
            rh = _line_h(kind)
            if yy - rh < y - 0.5:
                break
            base = yy - rh + 4.4
            if kind == "row":
                r = payload
                if idx % 2 == 1:
                    c.saveState()
                    c.setFillColor(_hx(theme.zebra))
                    c.rect(x, yy - rh, w, rh, stroke=0, fill=1)
                    c.restoreState()
                _draw_fit(
                    c, _fmt_date(r["date"]), xs[0] + 4, base, font_reg, 7.2, MIN_TEXT_PT, theme.text,
                    w * fracs[0] - 8,
                )
                for ci, k in enumerate(contributor_keys, start=1):
                    v = r["by"][k]
                    col = theme.faint if v == 0 else (theme.deposit if v > 0 else theme.withdrawal)
                    txt = "—" if v == 0 else money_short(v, symbol, signed=True)
                    _draw_fit(
                        c,
                        txt,
                        xs[ci + 1] - 4,
                        base,
                        font_reg,
                        7.2,
                        MIN_TEXT_PT,
                        col,
                        w * fracs[ci] - 8,
                        align="right",
                    )
                net = r["total"]
                ncol = (
                    theme.deposit
                    if net > 0
                    else theme.withdrawal
                    if net < 0
                    else theme.muted
                )
                _draw_fit(
                    c, money_short(net, symbol, signed=True), xs[4] - 4, base, font_bold, 7.2,
                    MIN_TEXT_PT, ncol, w * fracs[3] - 8, align="right",
                )
                _draw_fit(
                    c, money_short(r["balance"], symbol), xs[5] - 4, base, font_reg, 7.2,
                    MIN_TEXT_PT, theme.text, w * fracs[4] - 8, align="right",
                )
                note = _clip(r["note"], font_reg, MIN_TEXT_PT, w * fracs[5] - 10)
                _draw_text(
                    c, note, xs[5] + 4, base, font_reg, MIN_TEXT_PT, theme.muted
                )
            else:
                if kind == "subtotal":
                    yy_, mm_ = payload
                    label = f"{calendar.month_abbr[mm_]} {yy_} total"
                    by = st["by"]
                    total = st["total"]
                    bal = st["balance"]
                else:
                    label = "Period total"
                    by = {
                        k: _q2(sum((r["by"][k] for r in record_rows), Decimal("0.00")))
                        for k in contributor_keys
                    }
                    total = records_total
                    bal = closing
                bg = theme.surface_tint if kind == "grand" else theme.accent_soft
                c.saveState()
                c.setFillColor(_hx(bg))
                c.rect(x, yy - rh, w, rh, stroke=0, fill=1)
                c.restoreState()
                _draw_fit(
                    c,
                    _clip(label, font_bold, 7.0, w * fracs[0] - 8),
                    xs[0] + 4,
                    base,
                    font_bold,
                    7.0,
                    MIN_TEXT_PT,
                    theme.accent_dark,
                    w * fracs[0] - 8,
                )
                for ci, k in enumerate(contributor_keys, start=1):
                    _draw_fit(
                        c,
                        money_short(by.get(k, Decimal("0.00")), symbol, signed=True),
                        xs[ci + 1] - 4,
                        base,
                        font_bold,
                        7.0,
                        MIN_TEXT_PT,
                        theme.text,
                        w * fracs[ci] - 8,
                        align="right",
                    )
                _draw_fit(
                    c,
                    money_short(total, symbol, signed=True),
                    xs[4] - 4,
                    base,
                    font_bold,
                    7.0,
                    MIN_TEXT_PT,
                    theme.accent_dark,
                    w * fracs[3] - 8,
                    align="right",
                )
                if bal is not None:
                    _draw_fit(
                        c,
                        money_short(bal, symbol),
                        xs[5] - 4,
                        base,
                        font_bold,
                        7.0,
                        MIN_TEXT_PT,
                        theme.text,
                        w * fracs[4] - 8,
                        align="right",
                    )
            _line(c, x, yy - rh, x + w, yy - rh, theme.border_soft, 0.35)
            yy -= rh
            idx += 1

    # ----------------------------- page 1 ---------------------------------- #

    def _draw_overview(c, layout, cfg):
        sec = layout["sections"]
        top = page_h - margin

        def band(name):
            off, h = sec[name]
            return top - off - h, h

        y0, h0 = band("header")
        _draw_header(c, margin, y0, content_w, h0)
        y0, h0 = band("hero")
        _draw_hero(c, margin, y0, content_w, h0)
        y0, h0 = band("tiles")
        _draw_tiles(c, margin, y0, content_w, h0)
        y0, h0 = band("middle")
        chart_w = content_w * 0.585
        gutter = MM_XS * mm
        _draw_chart(c, margin, y0, chart_w, h0, cfg)
        _draw_contributors(c, margin + chart_w + gutter, y0, content_w - chart_w - gutter, h0)
        if include_calendar:
            _draw_calendar(c, layout, cfg)
            _draw_summary(c, layout)
        if "footer" in sec:
            y0, h0 = band("footer")
            _draw_footer(c, margin, y0, content_w, h0, c.getPageNumber())
            _draw_page_number(c, c.getPageNumber())

    # ----------------------------- assembly -------------------------------- #

    BASE_CFG = LayoutConfig(chart_mm=48, hero_mm=30, tiles_mm=15, cal_min_mm=13)
    COMPACT_CFG = LayoutConfig(
        chart_mm=44, hero_mm=28, tiles_mm=14.5, cal_min_mm=12, header_mm=14
    )
    TIGHT_CFG = LayoutConfig(
        chart_mm=40,
        hero_mm=26,
        tiles_mm=14,
        cal_min_mm=11,
        header_mm=13.5,
        gap_tiles_mm=MM_XS,
        compact_hero=True,
    )

    def _choose_cfg():
        for cf in (BASE_CFG, COMPACT_CFG, TIGHT_CFG):
            if _compute_layout(cf, with_footer=True)["natural"] <= content_h + 0.5:
                return cf
        return TIGHT_CFG

    table_lines = _record_lines() if include_entries else []
    table_h = _table_height(table_lines) if table_lines else 0.0
    footer_h = 18 * mm
    one_page = False
    cfg = _choose_cfg()
    if table_lines:
        overview_target = content_h - table_h - footer_h - 6
        if _compute_layout(TIGHT_CFG, with_footer=False)["natural"] <= overview_target:
            one_page = True
            cfg = TIGHT_CFG

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Piggy Bank statement {statement_no}")
    c.setAuthor("MoneyWise")
    c.setSubject(period_label or "Savings statement")

    page_no = 1
    if one_page:
        overview_target = content_h - table_h - footer_h - 6
        layout = _compute_layout(cfg, target_h=overview_target, with_footer=False)
        _draw_overview(c, layout, cfg)
        table_y = margin + footer_h
        _draw_table(c, table_lines, margin, table_y, content_w, table_h)
        _draw_footer(c, margin, margin, content_w, footer_h, 1)
        _draw_page_number(c, 1)
    else:
        layout = _compute_layout(cfg, target_h=content_h, with_footer=True)
        _draw_overview(c, layout, cfg)

    if table_lines and not one_page:
        rec_footer_h = footer_h
        table_top = page_h - margin - 30 * mm
        table_bottom = margin + rec_footer_h + 5 * mm
        avail = table_top - table_bottom
        pages = []
        cur = []
        used = 18.0
        for line in table_lines:
            lh = _line_h(line[0])
            if cur and used + lh > avail + 0.5:
                pages.append(cur)
                cur = []
                used = 18.0
            cur.append(line)
            used += lh
        if cur:
            pages.append(cur)
        for chunk in pages:
            page_no += 1
            c.showPage()
            top = page_h - margin
            _draw_header(c, margin, top - 16 * mm, content_w, 16 * mm)
            _draw_text(
                c,
                "Savings records",
                margin,
                top - 16 * mm - 11,
                font_bold,
                10.0,
                theme.text,
            )
            _draw_fit(
                c,
                f"{period_label or ''}  ·  one row per date",
                margin + content_w,
                top - 16 * mm - 11,
                font_reg,
                7.0,
                MIN_TEXT_PT,
                theme.muted,
                content_w * 0.55,
                align="right",
            )
            _draw_table(c, chunk, margin, table_bottom, content_w, table_top - table_bottom)
            _draw_footer(c, margin, margin, content_w, rec_footer_h, page_no)
            _draw_page_number(c, page_no)

    if debug_layout is not None:
        debug_layout["one_page"] = one_page
        debug_layout["pages"] = page_no
        debug_layout["table_lines"] = len(table_lines)

    c.showPage()
    c.save()
    return buf.getvalue()
