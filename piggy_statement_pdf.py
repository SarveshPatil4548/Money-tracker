"""Piggy bank statement PDF - modern, clean, printable (ReportLab Platypus).

Design tokens live here so the look can be changed in one place.
All assets/fonts are local; no remote fetches at render time.
"""

from __future__ import annotations

import calendar
import io
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

FONT_REGULAR = "Inter"
FONT_BOLD = "Inter-Bold"


@dataclass(frozen=True)
class Theme:
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


THEMES = {
    "color": Theme(
        "color", "#4f46e5", "#eef2ff", "#3730a3", "#111827", "#6b7280", "#9ca3af",
        "#e5e7eb", "#f3f4f6", "#ffffff", "#f8fafc", "#047857", "#b91c1c", "#fbfbfd",
    ),
    "bw": Theme(
        "bw", "#111111", "#f3f4f6", "#111111", "#111111", "#444444", "#666666",
        "#cfcfcf", "#e5e7eb", "#ffffff", "#f5f5f5", "#111111", "#111111", "#fafafa",
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


def inr_grouping(amount) -> str:
    value = _q2(amount)
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    frac = f"{(value - Decimal(whole)):.2f}"[2:]
    s = str(whole)
    if len(s) <= 3:
        grouped = s
    else:
        grouped = s[-3:]
        s = s[:-3]
        while len(s) > 2:
            grouped = s[-2:] + "," + grouped
            s = s[:-2]
        if s:
            grouped = s + "," + grouped
    return f"{sign}{grouped}.{frac}"


def money(value, symbol: str = "â‚¹", signed: bool = False) -> str:
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


def short_hash(value: str, head: int = 12, tail: int = 8) -> str:
    v = (value or "").strip()
    if len(v) <= head + tail + 3:
        return v
    return f"{v[:head]}â€¦{v[-tail:]}"


def statement_number(year: int, seq: int) -> str:
    return f"PB-{year}-{int(seq):04d}"


_FONT_READY = False
_FONT_REG = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_HAS_RUPEE = False


def register_fonts():
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
        (BASE_DIR / "static" / "fonts" / "Inter-Regular.ttf", BASE_DIR / "static" / "fonts" / "Inter-Bold.ttf", FONT_REGULAR, FONT_BOLD),
        (BASE_DIR / "static" / "fonts" / "DejaVuSans.ttf", BASE_DIR / "static" / "fonts" / "DejaVuSans-Bold.ttf", "DejaVuSans", "DejaVuSans-Bold"),
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf"), "AppSans", "AppSans-Bold"),
        (Path(r"C:\Windows\Fonts\segoeui.ttf"), Path(r"C:\Windows\Fonts\segoeuib.ttf"), "AppSans", "AppSans-Bold"),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), "DejaVuSans", "DejaVuSans-Bold"),
    ]
    for reg_path, bold_path, reg_name, bold_name in candidates:
        try:
            if not reg_path.exists():
                continue
            face = TTFont(reg_name, str(reg_path))
            pdfmetrics.registerFont(face)
            has_rupee = 0x20B9 in getattr(face.face, "charToGlyph", {})
            bname = reg_name
            if bold_path and Path(bold_path).exists():
                pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
                bname = bold_name
            pdfmetrics.registerFontFamily(reg_name, normal=reg_name, bold=bname, italic=reg_name, boldItalic=bname)
            _FONT_REG, _FONT_BOLD, _HAS_RUPEE = reg_name, bname, has_rupee
            _FONT_READY = True
            return _FONT_REG, _FONT_BOLD, _HAS_RUPEE
        except Exception:
            continue
    _FONT_READY = True
    return _FONT_REG, _FONT_BOLD, _HAS_RUPEE


def _hx(color: str):
    from reportlab.lib import colors
    return colors.HexColor(color)


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


def _qr_flowable(data: str, size):
    try:
        import qrcode
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image
        qr = qrcode.QRCode(border=1, box_size=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        b = io.BytesIO()
        img.save(b, format="PNG")
        b.seek(0)
        return Image(ImageReader(b), width=size, height=size)
    except Exception:
        return None


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
) -> bytes:
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle, PageBreak
    )
    from reportlab.pdfgen import canvas as pdfcanvas

    theme = get_theme(theme_name)
    font_reg, font_bold, has_rupee = register_fonts()
    symbol = "â‚¹" if has_rupee else "Rs"
    margin = MARGIN_MM * mm
    page_w, page_h = A4
    content_w = page_w - 2 * margin
    styles = getSampleStyleSheet()

    def PS(name, **kw):
        base = kw.pop('parent', styles['Normal'])
        return ParagraphStyle(
            name, parent=base,
            fontName=kw.pop('fontName', font_reg),
            textColor=_hx(kw.pop('textColor', theme.text)),
            leading=kw.pop('leading', 12),
            fontSize=kw.pop('fontSize', 9),
            **kw,
        )

    p_small = PS('Small', fontSize=7.5, leading=10, textColor=theme.muted)
    p_body = PS('Body', fontSize=9, leading=13, textColor=theme.text)
    p_body_b = PS('BodyB', fontSize=9, leading=13, fontName=font_bold, textColor=theme.text)
    p_right = PS('Right', fontSize=9, leading=13, alignment=TA_RIGHT, textColor=theme.text)
    p_h2 = PS('H2', fontSize=11, leading=14, fontName=font_bold, textColor=theme.text, spaceBefore=SPACE, spaceAfter=4)

    ps = _parse_date(period_start) or date.today()
    pe = _parse_date(period_end) or ps
    if isinstance(generated_at, datetime):
        generated_str = generated_at.strftime("%d %b %Y, %H:%M")
    elif isinstance(generated_at, date):
        generated_str = generated_at.strftime("%d %b %Y")
    else:
        generated_str = str(generated_at)

    entries = list(entries or [])
    # apply contributor filter for display/list if provided (data for hash/totals should be handled by caller)
    display_entries = entries
    if contributor_filter and contributor_filter not in ("", "all"):
        display_entries = [e for e in entries if e.get('contributor') == contributor_filter]

    opening = _q2(opening_balance)
    closing = _q2(closing_balance)
    tdep = _q2(total_deposits)
    twd = _q2(total_withdrawals)

    # stats based on displayed period entries (deposits)
    dep_days = sorted({str(e.get('entry_date'))[:10] for e in display_entries if e.get('entry_type') == 'deposit' and e.get('entry_date')})
    days_saved = len(dep_days)
    ds = [d for d in (_parse_date(x) for x in dep_days) if d]
    ds = sorted(set(ds))
    longest = cur = 0
    if ds:
        longest = cur = 1
        for i in range(1, len(ds)):
            if (ds[i]-ds[i-1]).days == 1:
                cur += 1
            else:
                longest = max(longest, cur); cur = 1
        longest = max(longest, cur)
        cur_streak = 1
        for i in range(len(ds)-1, 0, -1):
            if (ds[i]-ds[i-1]).days == 1: cur_streak += 1
            else: break
    else:
        cur_streak = 0
    avg_day = (tdep / Decimal(days_saved)) if days_saved else Decimal('0.00')

    story = []

    # Header
    logo_flow = None
    for cand in [logo_path, BASE_DIR/"static"/"img"/"apple-touch-icon.png", BASE_DIR/"static"/"img"/"favicon-32x32.png"]:
        if not cand:
            continue
        try:
            p = Path(cand)
            if p.exists() and p.suffix.lower() in ('.png', '.jpg', '.jpeg'):
                from reportlab.platypus import Image
                logo_flow = Image(str(p), width=16*mm, height=16*mm)
                break
        except Exception:
            pass
    brand = Paragraph("<b>MoneyWise</b>", PS('Brand', fontSize=14, leading=16, fontName=font_bold, textColor=theme.accent_dark))
    left_cell = [[logo_flow, brand]] if logo_flow else [[brand]]
    left = Table(left_cell, colWidths=[18*mm, content_w*0.36] if logo_flow else [content_w*0.45])
    left.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    right = Table([
        [Paragraph('<b>Savings Statement</b>', PS('ST', fontSize=13, leading=15, fontName=font_bold, alignment=TA_RIGHT, textColor=theme.text))],
        [Paragraph(escape(statement_no), PS('SN', fontSize=8.5, leading=11, alignment=TA_RIGHT, textColor=theme.muted))],
        [Paragraph(f"Generated {escape(generated_str)}", PS('SG', fontSize=8, leading=10, alignment=TA_RIGHT, textColor=theme.faint))],
    ], colWidths=[content_w*0.45])
    right.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))
    header = Table([[left, right]], colWidths=[content_w*0.53, content_w*0.47])
    header.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),SPACE)]))
    story.append(header)

    # Hero
    period_text = f"{_fmt_date(ps)} â€“ {_fmt_date(pe)}"
    hero = Table([[
        Table([[Paragraph(escape(str(bank.get('name','Piggy Bank'))), PS('HN', fontSize=11, leading=14, fontName=font_bold, textColor=theme.accent_dark))],
               [Paragraph(f"Statement period: {escape(period_text)}", PS('HP', fontSize=8.5, leading=11, textColor=theme.muted))],
               [Paragraph(f"Period: {escape(period_label)}", PS('HL', fontSize=8, leading=10, textColor=theme.faint))]], colWidths=[content_w*0.55]),
        Table([[Paragraph("TOTAL SAVED", PS('HT', fontSize=8, leading=10, alignment=TA_RIGHT, textColor=theme.muted))],
               [Paragraph(escape(money(closing, symbol)), PS('HV', fontSize=22, leading=26, fontName=font_bold, alignment=TA_RIGHT, textColor=theme.text))]], colWidths=[content_w*0.35])
    ]], colWidths=[content_w*0.58, content_w*0.38])
    hero.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,-1), _hx(theme.accent_soft if theme.name=='color' else theme.surface_tint)),
        ('BOX',(0,0),(-1,-1), 0.5, _hx(theme.border)),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12),
    ]))
    story.append(hero)
    story.append(Spacer(1, SPACE*2))

    # Stat tiles
    tile_w = (content_w - 3*2) / 4.0
    def tile(lbl, val):
        return Table([[Paragraph(escape(lbl), PS('TL', fontSize=7.5, leading=10, textColor=theme.muted))],
                      [Paragraph(escape(str(val)), PS('TV', fontSize=12, leading=15, fontName=font_bold, textColor=theme.text))]], colWidths=[tile_w])
    tiles = Table([[tile('Days saved', days_saved), tile('Current streak', f'{cur_streak} days'), tile('Longest streak', f'{longest} days'), tile('Avg per day', money(avg_day, symbol))]], colWidths=[tile_w]*4)
    tiles.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.5,_hx(theme.border)),('INNERGRID',(0,0),(-1,-1),0.5,_hx(theme.border)),('BACKGROUND',(0,0),(-1,-1),_hx(theme.surface)),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP')]))
    story.append(tiles)
    story.append(Spacer(1, SPACE*2))

    # Contributions
    story.append(Paragraph('Contributions', p_h2))
    buckets = defaultdict(lambda: {'deposits': Decimal('0'), 'withdrawals': Decimal('0')})
    for e in display_entries:
        name = e.get('contributor_name') or ('Me' if e.get('contributor')=='me' else 'Her')
        amt = _q2(e.get('amount'))
        if e.get('entry_type') == 'withdrawal': buckets[name]['withdrawals'] += amt
        else: buckets[name]['deposits'] += amt
    total_net = sum((v['deposits']-v['withdrawals']) for v in buckets.values()) or (tdep - twd)
    if not buckets:
        story.append(Paragraph('No contributions in this period.', p_small))
    else:
        for name, b in buckets.items():
            net = b['deposits'] - b['withdrawals']
            pct = float((net/total_net*100) if total_net else 0)
            pct = max(0.0, min(100.0, pct))
            bar_w = content_w * 0.42
            try:
                from reportlab.graphics.shapes import Drawing, Rect
                dr = Drawing(bar_w, 10)
                dr.add(Rect(0,2,bar_w,6,fillColor=_hx(theme.border_soft), strokeColor=_hx(theme.border), strokeWidth=0.25))
                dr.add(Rect(0,2,bar_w*pct/100.0,6,fillColor=_hx(theme.accent), strokeColor=None))
            except Exception:
                dr = Paragraph('', p_small)
            row = Table([[Paragraph(escape(name), p_body_b),
                          Paragraph(escape(money(net, symbol)), PS('CN', fontSize=9, alignment=TA_RIGHT, fontName=font_bold)),
                          Paragraph(f"{pct:.0f}%", PS('CP', fontSize=8.5, alignment=TA_RIGHT, textColor=theme.muted)),
                          dr]], colWidths=[content_w*0.22, content_w*0.16, content_w*0.10, content_w*0.44])
            row.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('LINEBELOW',(0,0),(-1,-1),0.25,_hx(theme.border_soft))]))
            story.append(row)
    story.append(Spacer(1, SPACE))

    # Goal
    if goal and goal.get('name'):
        story.append(Paragraph('Goal progress', p_h2))
        target = _q2(goal.get('target') or goal.get('target_amount') or 0)
        saved = _q2(goal.get('saved') if goal.get('saved') is not None else closing)
        pct = float((saved/target*100) if target>0 else 0); pct_c = max(0.0, min(100.0, pct))
        remaining = max(Decimal('0.00'), target-saved) if target>0 else Decimal('0.00')
        eta = goal.get('estimated_completion') or goal.get('eta') or goal.get('estimated_date') or ''
        try:
            from reportlab.graphics.shapes import Drawing, Rect
            dr = Drawing(content_w, 12)
            dr.add(Rect(0,2,content_w,8,fillColor=_hx(theme.border_soft), strokeColor=_hx(theme.border), strokeWidth=0.25))
            dr.add(Rect(0,2,content_w*pct_c/100.0,8,fillColor=_hx(theme.accent), strokeColor=None))
            story.append(dr)
        except Exception:
            pass
        info = f"<b>{escape(str(goal.get('name')))}</b> â€” {pct_c:.0f}% complete Â· Remaining {escape(money(remaining, symbol))}"
        if eta:
            info += f" Â· Estimated completion {escape(str(eta))}"
        story.append(Paragraph(info, p_small))
        story.append(Spacer(1, SPACE))

    # --------------------------- calendar helpers ------------------------- #
    rows_bal = _entry_rows(display_entries, opening)

    def _months_in_period(start_d, end_d):
        cur = date(start_d.year, start_d.month, 1)
        last = date(end_d.year, end_d.month, 1)
        out = []
        while cur <= last:
            out.append((cur.year, cur.month))
            if cur.month == 12:
                cur = date(cur.year + 1, 1, 1)
            else:
                cur = date(cur.year, cur.month + 1, 1)
        return out

    def _month_end_dt(y, m):
        if m == 12:
            return date(y, 12, 31)
        return date(y, m + 1, 1) - timedelta(days=1)

    def _initials(n):
        parts = [p for p in str(n or '').strip().split() if p]
        if not parts:
            return '?'
        if len(parts) == 1:
            return parts[0][:1].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()

    def _fit_amt(text_s, max_fs, min_fs=5.0):
        # crude fit by length to keep within cell (Indian grouped can be long)
        s = str(text_s)
        n = len(s)
        fs = max_fs
        if n > 10:
            fs -= 2.0
        if n > 13:
            fs -= 1.5
        if n > 16:
            fs -= 1.0
        return max(min_fs, fs)

    def _day_agg_for_month(y, m, month_start_d, month_end_d):
        # aggregate by day using display_entries (same soft-delete/filter as entries table)
        days = {}
        for e in display_entries:
            d = _parse_date(e.get('entry_date'))
            if not d or d.year != y or d.month != m:
                continue
            if d < month_start_d or d > month_end_d:
                continue
            rec = days.setdefault(d.day, {'deposit': Decimal('0.00'), 'withdrawal': Decimal('0.00'), 'by': defaultdict(lambda: Decimal('0.00')), 'names': {}})
            amt = _q2(e.get('amount'))
            who = e.get('contributor') or 'me'
            wname = e.get('contributor_name') or who
            if e.get('entry_type') == 'withdrawal':
                rec['withdrawal'] += amt
                rec['by'][('w', who)] -= amt
            else:
                rec['deposit'] += amt
                rec['by'][('d', who)] += amt
            rec['names'][who] = wname
        return days

    def _month_stats(y, m, month_start_d, month_end_d, days, target):
        dim = calendar.monthrange(y, m)[1]
        m_start = date(y, m, 1)
        m_end = date(y, m, dim)
        eff_start = max(m_start, month_start_d)
        eff_end = min(m_end, month_end_d)
        today = date.today()
        count_end = min(eff_end, today)  # don't mark future as missed
        total = Decimal('0.00')
        days_saved = 0
        missed = 0
        best_amt = Decimal('0.00')
        best_day = None
        dep_days = []
        if count_end >= eff_start:
            d = eff_start
            while d <= count_end:
                rec = days.get(d.day)
                net_dep = _q2((rec['deposit'] - rec['withdrawal']) if rec else Decimal('0.00'))
                dep_only = _q2(rec['deposit']) if rec else Decimal('0.00')
                if dep_only > 0 and (not rec or rec['withdrawal'] == 0):
                    total += dep_only
                    days_saved += 1
                    dep_days.append(d)
                    if dep_only > best_amt:
                        best_amt = dep_only; best_day = d.day
                elif rec and (rec['deposit'] > 0 or rec['withdrawal'] > 0):
                    # has activity (incl withdrawal/mixed) - count deposits toward total but treat specially
                    if dep_only > 0:
                        total += dep_only
                        days_saved += 1
                        dep_days.append(d)
                        if dep_only > best_amt:
                            best_amt = dep_only; best_day = d.day
                    if highlight_missed and dep_only == 0 and rec['withdrawal'] > 0:
                        missed += 1
                else:
                    if highlight_missed:
                        missed += 1
                d += timedelta(days=1)
        dep_days = sorted(set(dep_days))
        longest = cur = 0
        if dep_days:
            longest = cur = 1
            for i in range(1, len(dep_days)):
                if (dep_days[i]-dep_days[i-1]).days == 1:
                    cur += 1
                else:
                    longest = max(longest, cur); cur = 1
            longest = max(longest, cur)
        return {
            'total': _q2(total), 'days_saved': days_saved, 'missed': missed,
            'best_day': best_day, 'best_amt': _q2(best_amt), 'streak': longest,
        }

    def _calendar_block(y, m, month_start_d, month_end_d, target, compact=False, cell_w=None, cell_h=None):
        days = _day_agg_for_month(y, m, month_start_d, month_end_d)
        dim = calendar.monthrange(y, m)[1]
        first_wd = date(y, m, 1).weekday()  # Mon=0
        stats = _month_stats(y, m, month_start_d, month_end_d, days, target)
        today = date.today()
        week_hdr = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
        cw = cell_w or ((content_w) / 7.0)
        ch = cell_h or (18*mm if compact else 22*mm)

        def cell_flow(daynum):
            if daynum is None:
                return Paragraph('', p_small)
            d = date(y, m, daynum)
            rec = days.get(daynum)
            dep = _q2(rec['deposit']) if rec else Decimal('0.00')
            wd = _q2(rec['withdrawal']) if rec else Decimal('0.00')
            net = dep - wd
            in_period = (month_start_d <= d <= month_end_d)
            is_future = d > today
            is_out = not in_period
            # status
            status = 'empty'
            if is_out or is_future:
                status = 'future'
            elif rec and wd > 0 and dep == 0:
                status = 'withdrawal'
            elif rec and dep > 0 and wd == 0:
                if target > 0 and dep == target:
                    status = 'saved'
                elif target > 0 and dep != target:
                    status = 'extra'
                else:
                    status = 'saved'
            elif rec and (dep > 0 or wd > 0):
                status = 'extra' if dep > 0 else 'withdrawal'
            else:
                status = 'missed' if highlight_missed else 'empty'

            # bg
            bg = None
            if status == 'saved':
                bg = _hx('#e8f7ee' if theme.name=='color' else theme.surface_tint)
            elif status == 'missed':
                bg = _hx('#fff1f2' if theme.name=='color' else '#f3f4f6')
            elif status == 'extra':
                bg = _hx('#eef2ff' if theme.name=='color' else theme.surface_tint)
            elif status == 'withdrawal':
                bg = _hx('#fef2f2' if theme.name=='color' else '#f5f5f5')

            marker = ''
            if status == 'saved':
                marker = '&#10003;'
            elif status == 'missed':
                marker = '&ndash;' if not highlight_missed else '&#10007;'
            elif status == 'extra':
                marker = '&#9650;'
            elif status == 'withdrawal':
                marker = '&minus;'

            if compact:
                # mini: tick/tint only, no amount
                if status in ('saved','extra'):
                    sym = 'â€¢'
                elif status == 'withdrawal':
                    sym = 'âˆ’'
                elif status == 'missed':
                    sym = 'Â·'
                else:
                    sym = ''
                dn = f"<font size='5' color='{theme.faint}'>{daynum}</font>"
                mid = f"<font size='6' color='{theme.muted}'>{sym}</font>" if sym else ''
                html = f"{dn}<br/>{mid}"
                return (Paragraph(html, PS(f'cd{y}{m}{daynum}', fontSize=6, leading=7, alignment=TA_CENTER, textColor=theme.text)), bg)

            # full cell
            dn = f"<font size='6.5' color='{theme.faint}'>{daynum}</font>"
            if status == 'future':
                amt_html = ''
            elif status == 'missed' and not (rec and (dep>0 or wd>0)):
                amt_html = f"<font size='8' color='{theme.muted}'>{'&#10007;' if highlight_missed else '&ndash;'}</font>"
            elif status == 'withdrawal' and dep == 0:
                fs = _fit_amt(money(-wd, symbol, signed=True), 8.5)
                amt_html = f"<font size='{fs}' color='{theme.withdrawal}'><b>{escape(money(-wd, symbol, signed=True))}</b></font>"
            elif rec and (dep>0 or wd>0):
                val = net
                is_extra = (status == 'extra')
                is_saved = (status == 'saved')
                color = theme.deposit if val >= 0 else theme.withdrawal
                if theme.name == 'bw':
                    color = theme.text
                fs = _fit_amt(money(val, symbol, signed=True if wd>0 else False), 8.5 if not is_extra else 8.8)
                weight_open = '<b>' if (is_extra or is_saved or wd>0) else ''
                weight_close = '</b>' if (is_extra or is_saved or wd>0) else ''
                show_val = money(val, symbol, signed=(wd>0 and dep==0)) if wd>0 and dep==0 else (money(dep, symbol) if wd==0 else money(net, symbol, signed=True))
                prefix = ('&#10003; ' if is_saved else ('&#9650; ' if is_extra else ('&minus; ' if wd>0 and dep==0 else '')))
                amt_html = f"<font size='{fs}' color='{color}'>{weight_open}{prefix}{escape(show_val)}{weight_close}</font>"
            else:
                amt_html = ''

            contrib_html = ''
            if not compact and rec and dep > 0 and wd == 0:
                # tiny initials line if both contributed
                who_keys = [k for k in ('me','her') if rec['by'].get(('d',k), Decimal('0.00')) > 0]
                if len(who_keys) >= 2:
                    parts = []
                    for wk in who_keys:
                        nm = rec['names'].get(wk) or bank.get('contributor_'+wk) or wk
                        ini = _initials(nm)
                        amt_k = rec['by'].get(('d',wk), Decimal('0.00'))
                        parts.append(f"{ini}:{inr_grouping(amt_k)}")
                    contrib_html = f"<br/><font size='5.2' color='{theme.muted}'>{escape(' '.join(parts))}</font>"
            elif not compact and rec and (wd > 0 and dep > 0):
                parts = []
                for wk in ('me','her'):
                    a = rec['by'].get(('d',wk), Decimal('0.00')) + rec['by'].get(('w',wk), Decimal('0.00'))
                    if a != 0:
                        nm = rec['names'].get(wk) or bank.get('contributor_'+wk) or wk
                        parts.append(f"{_initials(nm)}:{money(a,symbol,signed=True)}")
                if parts:
                    contrib_html = f"<br/><font size='5.2' color='{theme.muted}'>{escape(' '.join(parts))}</font>"

            html = f"{dn}<br/>{amt_html}{contrib_html}" if amt_html or contrib_html else dn
            return (Paragraph(html, PS(f'c{y}{m}{daynum}', fontSize=8, leading=9.5, alignment=TA_CENTER, textColor=theme.text)), bg)

        # build grid
        data = [[Paragraph(f"<b>{h}</b>", PS(f'h{h}{y}{m}', fontSize=6.5 if compact else 7.5, leading=8, alignment=TA_CENTER, textColor=theme.muted, fontName=font_bold)) for h in week_hdr]]
        row_bgs = {}
        daynum = 1
        for r in range(6):
            row = []
            for c in range(7):
                idx = r*7 + c
                if idx < first_wd or daynum > dim:
                    row.append(Paragraph('', p_small))
                else:
                    flow, bg = cell_flow(daynum)
                    row.append(flow)
                    if bg is not None:
                        row_bgs[(r+1, c)] = bg
                    daynum += 1
            data.append(row)
            if daynum > dim:
                break
        colw = [cw]*7
        rowh = [12 if compact else 14] + [ch]*(len(data)-1)
        tbl = Table(data, colWidths=colw, rowHeights=rowh, repeatRows=1)
        ts = [
            ('GRID',(0,0),(-1,-1),0.35,_hx(theme.border)),
            ('BACKGROUND',(0,0),(-1,0),_hx(theme.surface_tint)),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('ALIGN',(0,0),(-1,-1),'CENTER'),
            ('LEFTPADDING',(0,0),(-1,-1),2),('RIGHTPADDING',(0,0),(-1,-1),2),
            ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),
        ]
        for (rr, cc), bgc in row_bgs.items():
            ts.append(('BACKGROUND',(cc,rr),(cc,rr),bgc))
        tbl.setStyle(TableStyle(ts))

        best_str = '-'
        if stats['best_day']:
            best_str = f"{stats['best_day']} {date(y,m,1).strftime('%b')} ({money(stats['best_amt'], symbol)})"
        summ = f"Total {money(stats['total'], symbol)}  Â·  Saved {stats['days_saved']} days  Â·  Missed {stats['missed']}  Â·  Best {best_str}  Â·  Streak {stats['streak']}"
        legend = "Legend: &#10003; Saved  &#10007; Missed  &#9650; Extra/partial  &minus; Withdrawal"
        return tbl, summ, legend, stats

    # Chart (cumulative balance)
    story.append(Paragraph('Cumulative savings', p_h2))

    try:
        from reportlab.graphics.shapes import Drawing, Rect, Line, String, PolyLine
        cw, ch = content_w, 38*mm
        d = Drawing(cw, ch)
        pad_l, pad_r, pad_t, pad_b = 26, 6, 6, 16
        pw = max(10, cw - pad_l - pad_r); ph = max(10, ch - pad_t - pad_b)
        d.add(Rect(0,0,cw,ch,fillColor=_hx(theme.surface), strokeColor=_hx(theme.border), strokeWidth=0.5))
        if not rows_bal:
            d.add(String(cw/2, ch/2, "No data for this period", textAnchor='middle', fontSize=8, fillColor=_hx(theme.muted), fontName=font_reg))
        else:
            vals = [float(r['_balance']) for r in rows_bal]
            vmin = min(vals + [0.0]); vmax = max(vals)
            if abs(vmax-vmin) < 1e-9: vmax = vmin + 1
            for i in range(4):
                y = pad_b + ph*i/3
                d.add(Line(pad_l, y, pad_l+pw, y, strokeColor=_hx(theme.border_soft), strokeWidth=0.5))
            pts=[]
            n=len(vals)
            for i,v in enumerate(vals):
                x = pad_l + (pw*i/(n-1) if n>1 else pw/2)
                y = pad_b + ph*((v-vmin)/(vmax-vmin))
                pts.extend([x,y])
            if len(pts)>=4:
                d.add(PolyLine(pts, strokeColor=_hx(theme.accent), strokeWidth=1.6))
            d.add(String(pad_l, 4, "Cumulative balance over selected period", fontSize=7, fillColor=_hx(theme.muted), fontName=font_reg))
            d.add(String(cw-4, ch-9, money(Decimal(str(vmax)), symbol), textAnchor='end', fontSize=6.5, fillColor=_hx(theme.faint), fontName=font_reg))
        story.append(d)
    except Exception:
        story.append(Paragraph('Chart unavailable for this period.', p_small))

    # Daily Calendar (after summary page, before entries table)
    if include_calendar:
        story.append(PageBreak())
        story.append(Paragraph('Daily Calendar', PS('CalH', fontSize=13, leading=16, fontName=font_bold, textColor=theme.text, spaceAfter=4)))
        try:
            target = _q2(daily_target) if daily_target not in (None, '') else Decimal('0.00')
        except Exception:
            target = Decimal('0.00')
        # if contributor filter is one person, interpret target as per-contributor? use given daily_target as-is
        months = _months_in_period(ps, pe)
        total_months = len(months)
        if total_months > 12:
            # Compact year view (12 mini-months) for readability, plus full daily for last 3 months
            story.append(Paragraph('Compact year view (amounts in month summaries). Full daily calendar shown for the last 3 months.', p_small))
            story.append(Spacer(1, 4))
            # group by year
            years = defaultdict(list)
            for y,m in months:
                years[y].append((y,m))
            for yr in sorted(years.keys()):
                story.append(Paragraph(str(yr), PS(f'Yr{yr}', fontSize=10, leading=13, fontName=font_bold, textColor=theme.text, spaceAfter=3)))
                mini_cells = []
                mini_w = content_w/3.0 - 10
                for (y,m) in years[yr]:
                    ms = max(date(y,m,1), ps); me = min(_month_end_dt(y,m), pe)
                    mtbl, msumm, _, _ = _calendar_block(y,m,ms,me,target,compact=True, cell_w=mini_w/7.0, cell_h=8)
                    cap = Paragraph(f"<b>{date(y,m,1).strftime('%b')}</b>  <font size='6' color='{theme.muted}'>{escape(msumm.split('·')[0].strip())}</font>", PS(f'mc{y}{m}', fontSize=7.5, leading=9, alignment=TA_CENTER, textColor=theme.text))
                    cell = Table([[cap],[mtbl]], colWidths=[mini_w], rowHeights=[12, None])
                    cell.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),1),('RIGHTPADDING',(0,0),(-1,-1),1),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),2)]))
                    mini_cells.append(cell)
                # 3 columns
                rows_m = []
                row = []
                for cell in mini_cells:
                    row.append(cell)
                    if len(row) == 3:
                        rows_m.append(row); row=[]
                if row:
                    while len(row) < 3:
                        row.append(Paragraph('', p_small))
                    rows_m.append(row)
                if rows_m:
                    grid = Table(rows_m, colWidths=[content_w/3.0]*3)
                    grid.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),2),('RIGHTPADDING',(0,0),(-1,-1),2),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
                    story.append(grid)
                story.append(Spacer(1, 4))
            # full detail for last 3 months
            detail_months = months[-3:]
            story.append(Spacer(1, SPACE))
            story.append(Paragraph('Daily detail (last 3 months)', p_h2))
            for i,(y,m) in enumerate(detail_months):
                ms = max(date(y,m,1), ps); me = min(_month_end_dt(y,m), pe)
                mtbl, msumm, legend, _ = _calendar_block(y,m,ms,me,target,compact=False)
                block = [Paragraph(f"<b>{date(y,m,1).strftime('%B %Y')}</b>", PS(f'dmh{y}{m}', fontSize=10.5, leading=13, fontName=font_bold, textColor=theme.text, spaceAfter=3)), mtbl,
                         Spacer(1,3), Paragraph(escape(msumm), p_small), Paragraph(legend, PS(f'leg{y}{m}', fontSize=7, leading=9, textColor=theme.faint))]
                from reportlab.platypus import KeepTogether
                story.append(KeepTogether(block))
                if i < len(detail_months)-1:
                    story.append(Spacer(1, SPACE))
        else:
            # one per block; try to fit two small months per page
            from reportlab.platypus import KeepTogether
            blocks = []
            for (y,m) in months:
                ms = max(date(y,m,1), ps); me = min(_month_end_dt(y,m), pe)
                mtbl, msumm, legend, _ = _calendar_block(y,m,ms,me,target,compact=False)
                block = KeepTogether([
                    Paragraph(f"<b>{date(y,m,1).strftime('%B %Y')}</b>", PS(f'mh{y}{m}', fontSize=10.5, leading=13, fontName=font_bold, textColor=theme.text, spaceAfter=3)),
                    mtbl,
                    Spacer(1,3),
                    Paragraph(escape(msumm), p_small),
                    Paragraph(legend, PS(f'leg2{y}{m}', fontSize=7, leading=9, textColor=theme.faint)),
                ])
                blocks.append(block)
            # pair short months? heuristic: 28/29/30-day months starting late have <=5 week rows, try 2-up
            idx = 0
            while idx < len(blocks):
                if idx + 1 < len(blocks):
                    y1,m1 = months[idx]; y2,m2 = months[idx+1]
                    dim1 = calendar.monthrange(y1,m1)[1]; dim2 = calendar.monthrange(y2,m2)[1]
                    rows1 = (date(y1,m1,1).weekday() + dim1 + 6)//7
                    rows2 = (date(y2,m2,1).weekday() + dim2 + 6)//7
                    if rows1 <= 5 and rows2 <= 5 and total_months <= 4:
                        # two per page vertically stacked
                        story.append(blocks[idx])
                        story.append(Spacer(1, SPACE*2))
                        story.append(blocks[idx+1])
                        idx += 2
                        if idx < len(blocks):
                            story.append(PageBreak())
                        continue
                story.append(blocks[idx])
                idx += 1
                if idx < len(blocks):
                    story.append(PageBreak())
        if not include_entries:
            story.append(Spacer(1, SPACE))

    # Entries (detailed only) - start on new page if including, else compact summary note
    if include_entries and detail != 'compact':
        story.append(PageBreak())
        story.append(Paragraph('Entries', PS('EH', fontSize=13, leading=16, fontName=font_bold, textColor=theme.text, spaceAfter=4)))
        if not rows_bal:
            story.append(Spacer(1, 24*mm))
            story.append(Paragraph('No entries in this period', PS('EmptyT', fontSize=13, leading=17, fontName=font_bold, alignment=TA_CENTER, textColor=theme.muted)))
            story.append(Spacer(1, 4))
            story.append(Paragraph('There were no deposits or withdrawals for the selected period or filters.', PS('EmptyS', fontSize=9.5, leading=13, alignment=TA_CENTER, textColor=theme.faint)))
        else:
            by_month = defaultdict(list)
            for e in rows_bal:
                d = _parse_date(e.get('entry_date')) or ps
                by_month[(d.year, d.month)].append(e)
            header = [Paragraph('Date', PS('TH', fontSize=8, fontName=font_bold, textColor=theme.muted)),
                      Paragraph('Contributor', PS('TH2', fontSize=8, fontName=font_bold, textColor=theme.muted)),
                      Paragraph('Note', PS('TH3', fontSize=8, fontName=font_bold, textColor=theme.muted)),
                      Paragraph('Amount', PS('TH4', fontSize=8, fontName=font_bold, textColor=theme.muted, alignment=TA_RIGHT)),
                      Paragraph('Balance', PS('TH5', fontSize=8, fontName=font_bold, textColor=theme.muted, alignment=TA_RIGHT))]
            colw = [22*mm, 28*mm, content_w - 22*mm - 28*mm - 28*mm - 30*mm, 28*mm, 30*mm]
            for idx, key in enumerate(sorted(by_month.keys())):
                month_rows = by_month[key]
                mdate = date(key[0], key[1], 1)
                mdep = sum((_q2(e.get('amount')) for e in month_rows if e.get('entry_type')!='withdrawal'), Decimal('0'))
                mwd = sum((_q2(e.get('amount')) for e in month_rows if e.get('entry_type')=='withdrawal'), Decimal('0'))
                mnet = mdep - mwd
                story.append(Spacer(1, SPACE if idx else 0))
                story.append(Paragraph(f"<b>{escape(mdate.strftime('%B %Y'))}</b>  <font color='{theme.muted}'>Subtotal {escape(money(mnet, symbol, signed=True))}</font>", PS('MH', fontSize=9.5, leading=13, textColor=theme.text)))
                data = [header]
                for i,e in enumerate(month_rows):
                    d = _parse_date(e.get('entry_date'))
                    amt = _q2(e.get('amount'))
                    is_w = e.get('entry_type') == 'withdrawal'
                    amt_color = theme.withdrawal if is_w else theme.deposit
                    sign_amt = money(-amt if is_w else amt, symbol, signed=True)
                    note_txt = e.get('note') or '-'
                    note_p = Paragraph(escape(str(note_txt)), PS(f'N{i}', fontSize=8.5, leading=11, textColor=theme.text))
                    amt_p = Paragraph(f"<font color='{amt_color}'>{escape(sign_amt)}</font>", PS(f'A{i}', fontSize=8.5, leading=11, alignment=TA_RIGHT, textColor=amt_color))
                    bal_p = Paragraph(escape(money(e.get('_balance'), symbol)), PS(f'B{i}', fontSize=8.5, leading=11, alignment=TA_RIGHT, fontName=font_bold, textColor=theme.text))
                    data.append([
                        Paragraph(escape(_fmt_date(d)), PS(f'D{i}', fontSize=8.5, leading=11, textColor=theme.text)),
                        Paragraph(escape(str(e.get('contributor_name') or e.get('contributor') or '-')), PS(f'C{i}', fontSize=8.5, leading=11, textColor=theme.text)),
                        note_p, amt_p, bal_p
                    ])
                # subtotal
                data.append([
                    Paragraph('', p_small),
                    Paragraph('<b>Month subtotal</b>', PS('MS', fontSize=8.5, fontName=font_bold, textColor=theme.muted)),
                    Paragraph('', p_small),
                    Paragraph(escape(money(mnet, symbol, signed=True)), PS('MSA', fontSize=8.5, fontName=font_bold, alignment=TA_RIGHT, textColor=theme.text)),
                    Paragraph(escape(money(month_rows[-1].get('_balance'), symbol)), PS('MSB', fontSize=8.5, fontName=font_bold, alignment=TA_RIGHT, textColor=theme.text)),
                ])
                tbl = Table(data, colWidths=colw, repeatRows=1)
                ts = [
                    ('LINEBELOW',(0,0),(-1,0),0.75,_hx(theme.border)),
                    ('LINEBELOW',(0,1),(-1,-2),0.25,_hx(theme.border_soft)),
                    ('LINEABOVE',(0,-1),(-1,-1),0.5,_hx(theme.border)),
                    ('VALIGN',(0,0),(-1,-1),'TOP'),
                    ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
                    ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
                    ('BACKGROUND',(0,-1),(-1,-1), _hx(theme.surface_tint)),
                ]
                for r in range(1, len(data)-1):
                    if r % 2 == 0:
                        ts.append(('BACKGROUND',(0,r),(-1,r), _hx(theme.zebra)))
                tbl.setStyle(TableStyle(ts))
                story.append(tbl)
    elif include_entries and detail == 'compact':
        story.append(Spacer(1, SPACE))
        story.append(Paragraph('Entry list omitted (Compact mode).', p_small))

    # Totals summary box
    story.append(Spacer(1, SPACE*2))
    story.append(Paragraph('Totals', p_h2))
    summary = Table([
        [Paragraph('Total deposits', p_body), Paragraph(escape(money(tdep, symbol)), PS('Td', fontSize=9, alignment=TA_RIGHT, fontName=font_bold, textColor=theme.deposit if theme.name=='color' else theme.text))],
        [Paragraph('Total withdrawals', p_body), Paragraph(escape(money(twd, symbol)), PS('Tw', fontSize=9, alignment=TA_RIGHT, fontName=font_bold, textColor=theme.withdrawal if theme.name=='color' else theme.text))],
        [Paragraph('Closing balance', p_body_b), Paragraph(escape(money(closing, symbol)), PS('Tc', fontSize=10, alignment=TA_RIGHT, fontName=font_bold, textColor=theme.text))],
    ], colWidths=[content_w*0.45, content_w*0.25])
    summary.setStyle(TableStyle([('LINEBELOW',(0,0),(-1,-2),0.25,_hx(theme.border_soft)),('LINEABOVE',(0,-1),(-1,-1),0.75,_hx(theme.border)),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4)]))
    story.append(summary)
    story.append(Spacer(1, SPACE*3))

    # Signatures
    names = [bank.get('contributor_me') or 'Contributor 1', bank.get('contributor_her') or 'Contributor 2']
    def sig_block(name):
        t = Table([
            [Paragraph(escape(name), PS('SigN', fontSize=8.5, fontName=font_bold, textColor=theme.text))],
            [Spacer(1, 18)],
            [Paragraph('Signature: __________________________', PS('SigL', fontSize=8.5, textColor=theme.text))],
            [Paragraph('Date: ________________', PS('SigD', fontSize=8.5, textColor=theme.text))],
        ], colWidths=[content_w*0.45])
        t.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)]))
        return t
    story.append(Table([[sig_block(names[0]), sig_block(names[1])]], colWidths=[content_w*0.5, content_w*0.5]))
    story.append(Spacer(1, SPACE*2))

    # Verification
    hash_show = (statement_file_hash or '').strip()
    qr_data = f"statement={statement_no};sha256={hash_show}" if hash_show else f"statement={statement_no}"
    qr = _qr_flowable(qr_data, 22*mm) if qr_data else None
    ver_lines = [
        Paragraph('<b>Verification</b>', PS('Vh', fontSize=9, fontName=font_bold, textColor=theme.text)),
        Paragraph(f"Statement number: {escape(statement_no)}", p_small),
        Paragraph(f"SHA-256: {escape(short_hash(hash_show) if hash_show else 'available after save')}", p_small),
        Paragraph(f"<font name='Courier' size='6.5'>{escape(hash_show or 'â€”')}</font>", PS('Vf', fontSize=6.5, leading=8, textColor=theme.faint)),
        Paragraph(f"Generated: {escape(generated_str)}", p_small),
    ]
    ver_inner = Table([[x] for x in ver_lines], colWidths=[content_w*0.68 if qr else content_w*0.92])
    ver_inner.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),1),('BOTTOMPADDING',(0,0),(-1,-1),1)]))
    if qr:
        vbox = Table([[ver_inner, qr]], colWidths=[content_w*0.72, content_w*0.22])
    else:
        vbox = Table([[ver_inner]], colWidths=[content_w*0.94])
    vbox.setStyle(TableStyle([('BOX',(0,0),(-1,-1),0.5,_hx(theme.border)),('BACKGROUND',(0,0),(-1,-1),_hx(theme.surface_tint)),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story.append(vbox)
    story.append(Spacer(1, SPACE))
    story.append(Paragraph(f"<font color='{theme.faint}'>Self-maintained savings record, not a bank document.</font>", PS('Disc', fontSize=7.5, leading=10, textColor=theme.faint)))

    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=20*mm,
        title=f"{bank.get('name','Piggy Bank')} - {statement_no}",
        author="MoneyWise",
        subject="Savings statement",
    )
    frame = Frame(margin, 20*mm, content_w, page_h - margin - 20*mm, id='normal', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id='main', frames=[frame], onPage=lambda c,d: None)])

    class PageNumCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []
        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_footer(num_pages)
                super().showPage()
            super().save()
        def _draw_footer(self, page_count):
            self.saveState()
            self.setFont(font_reg, 7.5)
            self.setFillColor(_hx(theme.muted))
            self.drawString(margin, 12*mm, f"MoneyWise  |  Statement {statement_no}")
            self.drawRightString(page_w - margin, 12*mm, f"Page {self._pageNumber} of {page_count}")
            self.setStrokeColor(_hx(theme.border))
            self.setLineWidth(0.4)
            self.line(margin, 16*mm, page_w - margin, 16*mm)
            self.restoreState()

    doc.build(story, canvasmaker=PageNumCanvas)
    return buf.getvalue()

