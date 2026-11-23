"""
SQL 분석 결과 기반 HTML 대시보드 생성
[ 구현 기준 ]
- 독립 실행: 외부 라이브러리·폰트·CDN 없이 HTML/CSS/SVG 사용
- 차트 생성: SQL 집계값 기반 HTML 차트 및 SVG 시각화
- 그래프 저장: Matplotlib PNG를 results/charts에 별도 저장
- 데이터 출처: SQL 집계 결과(results) 및 품질 검사 결과(quality)
- 개인정보 보호: 대표자명·기관담당자명 등 개인정보성 컬럼 제외
"""
from __future__ import annotations

import html
import math
import numbers
from datetime import datetime
from pathlib import Path

import pandas as pd

import config

EOK = 100_000_000
JO = 10_000 * EOK
AMOUNT_WORDS = ("금액", "합계")

# 범주별 고정 색상 (라벤더·틸·피치·블루)
# 색상 구분을 보완하기 위한 범주명·비율 라벨 병행 표시
CATEGORY_COLORS = ["#8a7dd0", "#35a08a", "#e3874f", "#5f97d6"]
GAUGE_TEXT = {"#8a7dd0": "#6a5fb0", "#35a08a": "#24786a", "#e3874f": "#b0602e", "#5f97d6": "#3f74b4"}


# ------------------------------------------------------------------ 숫자·문자 표기
def _is_missing(value) -> bool:
    return value is None or value is pd.NA or (isinstance(value, float) and pd.isna(value))


def won(value) -> str:
    """금액의 가독성을 위한 조·억·원 단위 변환"""
    if _is_missing(value):
        return "-"
    v = int(value)
    if abs(v) >= JO:
        jo, rest = divmod(v, JO)
        eok_part = round(rest / EOK)
        if eok_part == 10_000:  # 반올림으로 1조에 도달하는 경우
            jo, eok_part = jo + 1, 0
        return f"{jo:,}조 {eok_part:,}억 원"
    if abs(v) >= EOK:
        return f"{v / EOK:,.1f}억 원"
    return f"{v:,}원"


def eok_short(value) -> str:
    return "-" if _is_missing(value) else f"{int(value) / EOK:,.0f}억"


def num(value) -> str:
    if _is_missing(value):
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def ratio(part, whole) -> float:
    return 0.0 if not whole or _is_missing(part) else float(part) / float(whole)


def pct(part, whole, digits=1) -> str:
    return "-" if not whole else f"{ratio(part, whole) * 100:.{digits}f}%"


def esc(text) -> str:
    return html.escape(str(text))


_DIGIT_BATCHIM = set("013678")  # 영·일·삼·육·칠·팔


def josa(word, with_batchim: str, without: str) -> str:
    """받침 유무에 따른 조사 선택 및 결합"""
    w = str(word).rstrip()
    base = w.split("(")[0].rstrip() if w.endswith(")") else w  # 괄호 앞 단어 기준
    last = base[-1] if base else ""
    if "가" <= last <= "힣":
        has = (ord(last) - 0xAC00) % 28 != 0
    elif last.isdigit():
        has = last in _DIGIT_BATCHIM
    else:
        has = False
    return w + (with_batchim if has else without)


# ------------------------------------------------------------------ 표·접기
# SQL 결과 컬럼명을 화면 표시용으로 변환 (원본 컬럼명 유지)
HEADER_LABELS = {
    "검사항목": "검사 항목", "전체건수": "전체 건수", "레코드수": "레코드 수", "지급결정건수": "지급결정 건수",
    "지급결정금액_결측": "지급결정금액 결측", "지급결정금액합계": "지급결정금액 합계",
    "평균지급결정금액": "지급결정금액 평균", "계약단위수": "계약 수", "정상계약번호수": "정상 계약번호 수",
    "계약금액_계약단위": "계약금액·계약별 1회", "계약금액_레코드단순합": "계약금액·단순 합산",
    "계약금액합계": "계약금액 합계", "배율": "과대 배율", "구간": "지급 비율 구간", "지수표기금액여부": "지수표기 금액",
}


def _header(col, amount_in_eok=True) -> str:
    label = HEADER_LABELS.get(str(col), str(col))
    return f"{label} (억 원)" if _is_amount_col(col, amount_in_eok) else label


def _is_amount_col(col, enabled=True) -> bool:
    name = str(col)
    excluded = ("결측", "건수", "레코드수", "단위수", "비율", "여부")
    return enabled and not any(w in name for w in excluded) and any(w in name for w in AMOUNT_WORDS)


def table(df: pd.DataFrame, amount_in_eok=True, max_height=None) -> str:
    """금액의 억 원 단위 표시 및 원 단위 툴팁 제공"""
    if df is None or df.empty:
        return '<p class="muted">데이터가 없습니다.</p>'
    heads = "".join(
        f"<th>{esc(_header(c, amount_in_eok))}</th>" for c in df.columns)
    rows = []
    for rec in df.itertuples(index=False):
        cells = []
        for col, val in zip(df.columns, rec):
            if str(col).endswith("여부"):
                cells.append(f"<td>{'예' if val else '-'}</td>")
            elif (isinstance(val, numbers.Number) and not isinstance(val, bool)) or _is_missing(val):
                if str(col) == "배율" and not _is_missing(val):
                    cells.append(f'<td class="num">{float(val):.2f}</td>')
                elif _is_amount_col(col, amount_in_eok) and not _is_missing(val):
                    cells.append(f'<td class="num" title="{int(val):,}원">{int(val) / EOK:,.1f}</td>')
                else:
                    cells.append(f'<td class="num">{num(val)}</td>')
            elif col == "판정":
                cells.append(f'<td><span class="badge b-{esc(val)}">{esc(val)}</span></td>')
            else:
                cells.append(f"<td>{esc(val)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    style = f' style="max-height:{max_height}px"' if max_height else ""
    return (f'<div class="table-wrap"{style}><table><thead><tr>{heads}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def details(title: str, inner: str, note: str = "") -> str:
    meta = f'<span class="muted">{esc(note)}</span>' if note else ""
    return (f'<details><summary><span>{esc(title)}</span>{meta}</summary>'
            f'<div class="details-body">{inner}</div></details>')


# ------------------------------------------------------------------ 시각 요소
def kpi(label: str, value: str, sub: str = "") -> str:
    return (f'<div class="card kpi"><div class="kpi-label">{esc(label)}</div>'
            f'<div class="kpi-value">{esc(value)}</div><div class="kpi-sub">{esc(sub)}</div></div>')


def _polar(cx, cy, r, deg):
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _arc(cx, cy, r, a0, a1) -> str:
    x0, y0 = _polar(cx, cy, r, a0)
    x1, y1 = _polar(cx, cy, r, a1)
    large = 1 if a1 - a0 > 180 else 0
    return f"M{x0:.2f},{y0:.2f} A{r},{r} 0 {large} 1 {x1:.2f},{y1:.2f}"


def gauge(title: str, share: float, sub: str, color: str) -> str:
    """0~1 비율 기반 240° 원호 게이지 생성"""
    share = max(0.0, min(1.0, share))
    start, sweep = 150, 240
    end = start + sweep * share
    kx, ky = _polar(100, 100, 78, end)
    value_arc = (f'<path d="{_arc(100, 100, 78, start, end)}" stroke="{color}" stroke-width="14" '
                 f'fill="none" stroke-linecap="round"/>' if share > 0 else "")
    svg = (f'<svg viewBox="0 0 200 165" aria-hidden="true">'
           f'<path d="{_arc(100, 100, 78, start, start + sweep)}" stroke="#efedf3" stroke-width="14" '
           f'fill="none" stroke-linecap="round"/>{value_arc}'
           f'<circle cx="{kx:.2f}" cy="{ky:.2f}" r="7" fill="#fff" stroke="{color}" stroke-width="4"/></svg>')
    return (f'<div class="card gauge"><div class="card-title">{esc(title)}</div>'
            f'<div class="gauge-body">{svg}<div class="gauge-center">'
            f'<div class="gauge-pct" style="color:{GAUGE_TEXT.get(color, color)}">{share * 100:.1f}%</div>'
            f'</div></div><div class="gauge-sub">{esc(sub)}</div></div>')


def chart_card(title: str, body: str, caption: str = "", sub: str = "") -> str:
    sub_html = f'<span class="muted">{esc(sub)}</span>' if sub else ""
    cap = f'<div class="caption">{esc(caption)}</div>' if caption else ""
    return (f'<div class="card chart"><div class="card-head"><div class="card-title">{esc(title)}</div>'
            f'{sub_html}</div>{body}{cap}</div>')


def column_bars(df: pd.DataFrame, label_col: str, value_col: str) -> str:
    """최댓값을 82% 높이로 표시하는 월별 세로 막대 차트 생성"""
    values = [0 if _is_missing(v) else int(v) for v in df[value_col]]
    peak = max(values + [1])
    cols = "".join(
        f'<div class="col" title="{esc(lab)} {v:,}원"><div class="col-val">{eok_short(v)}</div>'
        f'<div class="col-bar" style="height:{v / peak * 82:.1f}%"></div>'
        f'<div class="col-label">{esc(lab)}</div></div>'
        for lab, v in zip(df[label_col], values))
    return f'<div class="columns">{cols}</div>'


def row_bars(df: pd.DataFrame, label_col: str, value_col: str, total) -> str:
    """금액 및 전체 대비 비중을 표시하는 기관별 가로 막대 차트 생성"""
    values = [0 if _is_missing(v) else int(v) for v in df[value_col]]
    peak = max(values + [1])
    rows = "".join(
        f'<div class="hrow" title="{esc(lab)} {v:,}원"><div class="hname">{esc(lab)}</div>'
        f'<div class="htrack"><div class="hfill" style="width:{v / peak * 100:.1f}%"></div></div>'
        f'<div class="hval">{eok_short(v)}</div><div class="hpct">{pct(v, total)}</div></div>'
        for lab, v in zip(df[label_col], values))
    return f'<div class="hbars">{rows}</div>'


def donut(df: pd.DataFrame, label_col: str, value_col: str, center_value: str, center_label: str,
          value_fmt=num, unit="") -> str:
    items = [(str(r[label_col]), 0 if _is_missing(r[value_col]) else max(0, int(r[value_col])))
             for _, r in df.iterrows()]
    total = sum(v for _, v in items)
    if not total:
        return '<p class="muted">표시할 데이터가 없습니다.</p>'
    gap = 0.6 if len(items) > 1 else 0  # 조각 사이 흰 간격
    segs, offset, legend = [], 0.0, []
    for i, (label, v) in enumerate(items):
        share = v / total * 100
        dash = max(share - gap, 0.01)
        color = CATEGORY_COLORS[i % len(CATEGORY_COLORS)]
        segs.append(f'<circle cx="80" cy="80" r="60" fill="none" stroke="{color}" stroke-width="22" '
                    f'pathLength="100" stroke-dasharray="{dash:.3f} {100 - dash:.3f}" '
                    f'stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 80 80)">'
                    f'<title>{esc(label)} {share:.1f}%</title></circle>')
        offset += share
        legend.append(f'<li><span class="dot" style="background:{color}"></span><span class="lg-name">{esc(label)}</span>'
                      f'<span class="lg-val">{value_fmt(v)}{unit}</span><span class="lg-pct">{share:.1f}%</span></li>')
    svg = f'<svg viewBox="0 0 160 160" aria-hidden="true">{"".join(segs)}</svg>'
    return (f'<div class="donut"><div class="donut-ring">{svg}<div class="donut-center">'
            f'<b>{esc(center_value)}</b><span>{esc(center_label)}</span></div></div>'
            f'<ul class="legend">{"".join(legend)}</ul></div>')


def quality_tiles(quality) -> str:
    if quality is None:
        return '<p class="muted">품질 검사 결과가 없습니다. 메뉴 1(CSV 데이터 검증)을 먼저 실행하세요.</p>'
    missing = quality.column_stats.set_index("컬럼").loc["지급결정금액"]
    tiles = [
        ("완전 중복 행", quality.check_value("완전 중복 행"), "warn", "DB에 보존 · 분석에서 제외"),
        ("대금지급번호 중복", quality.check_value("대금지급번호 중복 행"), "warn", "기본키 대신 내부 키 사용"),
        ("계약번호 손상", quality.check_value("계약납품요구번호 지수표기 손상"), "error", "지수표기(2.02E+09)"),
        ("지급결정금액 결측", int(missing["결측건수"]), "info", f"{missing['결측률(%)']:.1f}% · NULL 유지"),
    ]
    return '<div class="q-grid">' + "".join(
        f'<div class="q-tile q-{lvl}"><div class="q-label"><span class="dot"></span>{esc(t)}</div>'
        f'<div class="q-value">{num(v)}<small>건</small></div><div class="q-sub">{esc(s)}</div></div>'
        for t, v, lvl, s in tiles) + "</div>"


# ------------------------------------------------------------------ 스타일
CSS = """
:root{--bg:#f8f8f7;--side:#f3f1f4;--card:#fff;--line:#ece9ee;--ink:#30303a;--ink-2:#5f5c68;--muted:#777580;
--primary:#8a7dd0;--primary-dark:#6a5fb0;--primary-soft:#f2eff9;--radius:18px;--shadow:0 8px 30px rgba(55,45,80,.035)}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:400 14px/1.65 'Pretendard Variable','Pretendard','SUIT Variable','SUIT',
'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif;letter-spacing:-.025em;-webkit-font-smoothing:antialiased;word-break:keep-all}

/* 레이아웃 */
.shell{display:grid;grid-template-columns:218px minmax(0,1fr);min-height:100vh;
background:linear-gradient(to right,var(--side) 0 218px,var(--bg) 218px)}
.side{position:sticky;top:0;height:100vh;padding:34px 20px;display:flex;flex-direction:column;border-right:1px solid #eae7ed}
.brand{display:flex;align-items:center;gap:10px;margin:0 6px 30px}
.brand b{display:block;font-size:15px;line-height:1.2;letter-spacing:-.035em}.brand span{font-size:11px;color:var(--muted)}
.side-label{font-size:11px;font-weight:700;color:var(--muted);margin:0 8px 8px}
.side nav{display:flex;flex-direction:column;gap:2px;margin-bottom:28px}
.side nav a{color:var(--ink-2);text-decoration:none;padding:11px 13px;border-radius:9px;font-weight:500;font-size:13px}
.side nav a:hover{background:#fff;color:var(--primary-dark)}
.info{margin:0 8px;font-size:12px}.info dt{color:var(--muted);margin-top:12px}.info dd{margin:2px 0 0;font-weight:600}
.side-foot{margin:auto 8px 0;font-size:11px;color:var(--muted)}
.main{padding:42px 44px 60px;min-width:0;max-width:1450px}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:28px}
.head h1{margin:0;font-size:29px;font-weight:750;letter-spacing:-.055em}.head p{margin:7px 0 0;color:var(--ink-2);font-size:13px}
.pill{background:#fff;border:1px solid var(--line);border-radius:99px;padding:7px 14px;font-size:12px;color:var(--ink-2);white-space:nowrap}
section{margin-bottom:32px;scroll-margin-top:16px}
.sec-title{font-size:17px;font-weight:700;letter-spacing:-.04em;margin:0 0 14px;display:flex;align-items:center;gap:8px}
.sec-title::before{content:'';width:3px;height:16px;border-radius:2px;background:var(--primary)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}
.card-title{font-size:14px;font-weight:700;letter-spacing:-.035em}
.grid-4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.muted{color:var(--muted);font-size:12px;font-weight:400}

/* KPI·게이지 */
.kpi{padding:22px 22px 20px;min-height:126px}.kpi-label{font-size:12px;color:var(--ink-2);font-weight:500}
.kpi-value{font-size:25px;font-weight:750;letter-spacing:-.055em;margin-top:10px;white-space:nowrap;font-variant-numeric:tabular-nums}
.kpi-sub{font-size:11px;margin-top:6px;color:var(--muted)}
.gauge{padding:22px 24px 14px}.gauge-body{position:relative;max-width:190px;margin:4px auto 0}
.gauge-body svg{display:block;width:100%;height:auto}
.gauge-center{position:absolute;left:0;right:0;top:50%;transform:translateY(-50%);text-align:center}
.gauge-pct{font-size:27px;font-weight:800;letter-spacing:-.05em;line-height:1.1}
.gauge-sub{font-size:12px;color:var(--ink-2);text-align:center;margin:-14px 0 10px}

/* 차트 */
.chart{padding:24px 24px 18px;min-height:345px;display:flex;flex-direction:column}
.card-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:18px}
.caption{margin-top:auto;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--ink-2)}
.columns{display:flex;align-items:flex-end;gap:10%;height:220px;padding:0 8%;margin-bottom:32px;
background:repeating-linear-gradient(to top,transparent 0 43px,#f2f0f5 43px 44px)}
.col{flex:1;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;position:relative}
.col-bar{width:100%;max-width:70px;background:linear-gradient(180deg,#a99fe0,var(--primary));border-radius:8px 8px 0 0;min-height:2px}
.col-val{font-size:12px;font-weight:700;margin-bottom:4px}
.col-label{position:absolute;bottom:-22px;font-size:12px;color:var(--ink-2)}
.hbars{display:flex;flex-direction:column;gap:7px;margin-bottom:12px}
.hrow{display:grid;grid-template-columns:minmax(0,190px) minmax(0,1fr) 62px 46px;gap:10px;align-items:center;font-size:12px}
.hname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink-2)}
.htrack{height:10px;background:#f3f0f8;border-radius:99px;overflow:hidden}
.hfill{height:100%;background:var(--primary);border-radius:99px}
.hval{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}
.hpct{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
.donut{display:flex;align-items:center;gap:22px;margin-bottom:12px}
.donut-ring{position:relative;width:158px;flex:none}.donut-ring svg{display:block;width:100%;height:auto}
.donut-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.donut-center b{font-size:15px}.donut-center span{font-size:11px;color:var(--muted)}
.legend{list-style:none;margin:0;padding:0;flex:1;min-width:0}
.legend li{display:grid;grid-template-columns:12px minmax(0,1fr) auto 50px;gap:8px;align-items:center;padding:7px 0;
border-bottom:1px dashed var(--line);font-size:12px}
.legend li:last-child{border-bottom:0}.lg-name{color:var(--ink-2)}.lg-val{font-weight:700;text-align:right;white-space:nowrap}
.lg-pct{text-align:right;color:var(--muted)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;background:var(--muted);flex:none}

/* 품질·SQL */
.q-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:10px}
.q-tile{background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px 18px 16px}
.q-label{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2)}
.q-value{font-size:22px;font-weight:750;letter-spacing:-.05em;margin-top:2px;font-variant-numeric:tabular-nums}
.q-value small{font-size:12px;font-weight:500;color:var(--muted);margin-left:2px}.q-sub{font-size:11px;color:var(--muted)}
.q-warn .dot{background:#e3874f}.q-error .dot{background:#d0584c}.q-info .dot{background:var(--primary)}
.highlight{display:flex;align-items:center;gap:22px;padding:20px 24px;margin-bottom:12px}
.highlight-num{font-size:34px;font-weight:800;letter-spacing:-.05em;color:var(--primary-dark);line-height:1}
.highlight-text b{display:block;font-size:14px}.highlight-text span{font-size:12px;color:var(--ink-2)}

/* 표·접기 */
.table-wrap{overflow:auto;max-height:380px;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:9px 12px;border-bottom:1px solid #f2f0f4;text-align:left;vertical-align:top}
th{position:sticky;top:0;background:#faf9fb;color:var(--ink-2);font-weight:600;white-space:nowrap;z-index:1}
tbody tr:hover{background:#fbfaff}td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.badge{border-radius:5px;padding:1px 7px;font-size:11px;font-weight:700;white-space:nowrap}
.b-오류{background:#fdeceb;color:#b4443a}.b-주의{background:#fff3e3;color:#9a6420}.b-정상{background:#e8f6ef;color:#2f7d5b}
.b-정보,.b-참고{background:#f1f0f4;color:var(--ink-2)}
details{background:#fff;border:1px solid var(--line);border-radius:14px;margin-bottom:8px}
summary{cursor:pointer;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:12px;
padding:13px 16px;font-weight:600;font-size:13px}
summary::-webkit-details-marker{display:none}
summary>span:first-child::before{content:'+';display:inline-grid;place-items:center;width:18px;height:18px;border-radius:5px;
background:var(--primary-soft);color:var(--primary-dark);margin-right:9px;font-weight:700}
details[open] summary>span:first-child::before{content:'−'}
.details-body{padding:0 16px 16px}.details-body h4{font-size:13px;margin:14px 0 6px}
.notes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.notes ul{margin:6px 0 0;padding-left:18px;color:var(--ink-2);font-size:13px;line-height:1.85}.notes li{margin:5px 0}
code{font-family:Consolas,'D2Coding',monospace;font-size:12px;background:#f1f0f4;padding:1px 5px;border-radius:4px}
footer{color:var(--muted);font-size:11px;line-height:1.7;padding-top:6px}

/* 반응형·인쇄 */
@media (max-width:1200px){.grid-4,.q-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:900px){.shell{display:block;background:var(--bg)}
.side{position:static;height:auto;padding:16px;background:var(--side);border-right:0;border-bottom:1px solid #eae7ed}
.brand{margin:0 0 10px}.side-label,.info,.side-foot{display:none}
.side nav{flex-direction:row;overflow-x:auto;margin:0}.side nav a{white-space:nowrap;padding:7px 10px}
.main{padding:24px 18px}.grid-2,.notes{grid-template-columns:1fr}.head{display:block}.head .pill{display:inline-block;margin-top:10px}}
@media (max-width:560px){.head h1{font-size:23px}.kpi{padding:16px}.kpi-value{font-size:20px}.chart{padding:18px}
.hrow{grid-template-columns:minmax(0,1fr) 70px 54px;row-gap:3px}.hrow .htrack{grid-column:1/-1;grid-row:2}
.donut{flex-direction:column;align-items:stretch}.donut-ring{margin:0 auto}.highlight{align-items:flex-start;flex-direction:column;gap:8px}}
@media print{.side{display:none}.shell{display:block;background:#fff}.card,details{box-shadow:none}.table-wrap{max-height:none}}
"""

# 인쇄 시 접힌 상세 항목 자동 펼침
JS = "window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>d.open=true));"


# ------------------------------------------------------------------ 보고서
def generate_report(results: dict, charts: list[Path], quality, changes: pd.DataFrame,
                    history: list[dict], output_path=None) -> Path:
    """Matplotlib PNG 경로 인자 호환 유지 및 HTML 기반 차트 생성"""
    output_path = Path(output_path or config.RESULTS_DIR / "report.html")
    o = results["overview"]
    src = config.SOURCE_INFO
    total = o["decided_sum"] or 0
    excluded = o["stored_records"] - o["analysis_records"]

    monthly = results["monthly_decided"]
    agencies = results["top_agencies_by_decided"]
    biz = results["by_business_type"]
    exp = results["by_expense_type"]

    # ---- KPI
    kpis = "".join([
        kpi("분석 대상 레코드", f"{o['analysis_records']:,}건", f"원본 {o['stored_records']:,}건 · 중복 {excluded:,}건 제외"),
        kpi("계약 수", f"{o['contract_units']:,}건", "계약번호 기준"),
        kpi("기관 수", f"{o['agencies']:,}곳", "기관명 기준"),
        kpi("지급결정금액 합계", won(o["decided_sum"]), f"{o['decided_count']:,}건 · 결측 제외"),
    ])

    # ---- 게이지
    top_exp = exp.dropna(subset=["지급결정금액합계"]).sort_values("지급결정금액합계", ascending=False)
    gauges = [gauge("지급결정금액 입력률", ratio(o["decided_count"], o["analysis_records"]),
                    f"{o['decided_count']:,} / {o['analysis_records']:,}건", CATEGORY_COLORS[1])]
    if len(top_exp):
        t = top_exp.iloc[0]
        gauges.append(gauge(f"지급결정금액 중 {t['지출구분명']} 비중", ratio(t["지급결정금액합계"], total),
                            won(t["지급결정금액합계"]), CATEGORY_COLORS[0]))

    # ---- 차트 (설명은 수치 한 줄)
    cap_month = ""
    if len(monthly) >= 2 and monthly["지급결정금액합계"].iloc[0]:
        first, last = monthly.iloc[0], monthly.iloc[-1]
        cap_month = (f"{josa(last['입력월'], '이', '가')} 전체의 {pct(last['지급결정금액합계'], total)} · "
                     f"{first['입력월']} 대비 {last['지급결정금액합계'] / first['지급결정금액합계']:.1f}배")
    cap_agency = (f"1위 {josa(agencies.iloc[0]['기관명'], '이', '가')} 전체의 "
                  f"{pct(agencies.iloc[0]['지급결정금액합계'], total)}" if len(agencies) else "")
    biz_rate = biz.assign(rate=lambda d: 1 - d["지급결정건수"] / d["레코드수"]).sort_values("rate")
    cap_biz = "지급결정금액 결측률 " + " · ".join(
        f"{r['업무구분명']} {r['rate'] * 100:.1f}%" for _, r in biz_rate.iterrows())
    cap_exp = ""
    if len(top_exp):
        t = top_exp.iloc[0]
        cap_exp = (f"{t['지출구분명']} 비중: 건수 {pct(t['레코드수'], o['analysis_records'])} · "
                   f"금액 {pct(t['지급결정금액합계'], total)}")

    charts_html = "".join([
        chart_card("월별 지급결정금액", column_bars(monthly, "입력월", "지급결정금액합계"), cap_month, "입력일자 기준"),
        chart_card(f"기관별 지급결정금액 상위 {len(agencies)}", row_bars(agencies, "기관명", "지급결정금액합계", total), cap_agency, "억 원 · 전체 대비 비중"),
        chart_card("업무구분별 레코드 수", donut(biz, "업무구분명", "레코드수", f"{int(biz['레코드수'].sum()):,}",
                                          "레코드", unit="건"), cap_biz),
        chart_card("지출구분별 지급결정금액", donut(exp, "지출구분명", "지급결정금액합계", won(total).replace(" 원", ""),
                                             "지급결정금액", value_fmt=eok_short), cap_exp),
    ])

    # ---- 데이터 품질 상세 (하나의 접기)
    q_inner = ""
    if quality is not None:
        q_inner += "<h4>검사 항목 전체</h4>" + table(quality.checks, amount_in_eok=False, max_height=320)
        q_inner += "<h4>업무·지출구분별 지급결정금액 결측</h4>" + table(quality.missing_by_group, amount_in_eok=False)
    if len(changes):
        change_summary = changes.groupby(["컬럼", "사유"]).size().reset_index(name="건수")
        q_inner += "<h4>값 변환 요약</h4>" + table(change_summary, amount_in_eok=False)
    quality_more = details("데이터 품질 상세 보기", q_inner, "검사 항목 · 결측 · 값 변환") if q_inner else ""

    # ---- SQL 집계: 핵심 발견 1개 + 집계 8종(접기)
    rec_sum, unit_sum = o.get("contract_amount_record_sum"), o.get("contract_amount_unit_sum")
    highlight = ""
    if rec_sum and unit_sum:
        highlight = (f'<div class="card highlight"><div class="highlight-num">{rec_sum / unit_sum:.2f}배</div>'
                     f'<div class="highlight-text"><b>단순 합산과 계약별 집계의 계약금액 차이</b>'
                     f'<span>레코드 단순 합산 {won(rec_sum)} · 계약별 1회 집계 {won(unit_sum)}</span>'
                     f'</div></div>')
    agency_amounts = results["contract_amount_by_agency"].copy()
    agency_amounts["배율"] = [round(r / u, 2) if u else None
                            for r, u in zip(agency_amounts["계약금액_레코드단순합"], agency_amounts["계약금액_계약단위"])]
    sql_tables = [
        ("월별 지급결정금액", results["monthly_decided"]),
        ("지출구분별 지급결정금액", results["by_expense_type"]),
        ("업무구분별 계약·지급결정 현황", results["by_business_type"]),
        ("지급결정금액 상위 기관", results["top_agencies_by_decided"]),
        ("레코드 수 상위 기관", results["records_by_agency"]),
        ("기관별 계약금액: 단순 합산과 계약별 집계 비교", agency_amounts),
        ("계약별 지급결정 비율 구간", results["contract_vs_decided"]),
        ("지급결정금액 상위 레코드", results["top_records_by_decided"]),
    ]
    sql_html = "".join(details(f"{i}. {t}", table(df, max_height=340), f"{len(df)}행")
                       for i, (t, df) in enumerate(sql_tables, 1))

    notes = details("데이터 처리 기준과 분석의 한계", f"""<div class="notes"><div><h4>처리 기준</h4><ul>
<li>원본 23개 컬럼을 문자열로 읽고 앞뒤 공백만 제거</li>
<li>내부 기본키 <code>record_id</code> 사용, 식별번호는 원본 그대로 보존</li>
<li>완전 중복 행은 DB에 보존하고 분석 뷰 <code>v_payments</code>에서 제외</li>
<li>금액 결측은 NULL로 저장해 합계·평균에서 제외</li>
<li>계약금액은 <code>v_contracts</code>에서 계약별 1회만 집계</li>
<li>대표자명·기관담당자명은 보고서에 싣지 않음</li></ul></div>
<div><h4>분석의 한계</h4><ul>
<li>입력일자는 시스템 입력일이며 실제 지급일과 다를 수 있음</li>
<li>{esc(o['min_date'])} ~ {esc(o['max_date'])} 입력분만 포함</li>
<li>계약 대비 지급 비율은 이 기간의 지급만 반영(낮아도 미지급을 뜻하지 않음)</li>
<li>지수표기 원본 값은 정밀도가 손실돼 복원 불가</li>
<li>손상된 계약번호(분석 대상 중 {num(o['invalid_contract_no_rows'])}행)는 계약 단위로 묶지 못하고 행별 계산</li>
<li>기관명 표준화·상하위 기관 통합은 하지 않음</li></ul></div></div>""")

    body = f"""
<div class="shell">
<aside class="side">
  <div class="brand"><div><b>Payment Analytics</b><span>조달청 공공데이터</span></div></div>
  <div class="side-label">메뉴</div>
  <nav><a href="#summary">요약</a><a href="#payments">지급 분석</a>
  <a href="#quality">데이터 품질</a><a href="#sql">SQL 집계</a><a href="#method">분석 기준</a></nav>
  <div class="side-label">데이터</div>
  <dl class="info">
    <dt>출처</dt><dd>{esc(src['provider'])} · {esc(src['portal'])}</dd>
    <dt>입력 기간</dt><dd>{esc(o['min_date'])} ~ {esc(o['max_date'])}</dd>
    <dt>원본 데이터</dt><dd>{o['stored_records']:,}건 · 23개 컬럼</dd>
    <dt>생성</dt><dd>{datetime.now():%Y-%m-%d %H:%M}</dd>
  </dl>
  <div class="side-foot">Python · SQLite · SQL</div>
</aside>
<div class="main">
  <header class="head"><div><h1>계약·대금지급 데이터 분석</h1>
    <p>{esc(src['dataset'])} · SQLite 집계 결과</p></div>
    <span class="pill">{esc(o['min_date'])} ~ {esc(o['max_date'])}</span></header>

  <section id="summary">
    <div class="grid-4">{kpis}</div>
    <div class="grid-2" style="margin-top:16px">{''.join(gauges)}</div>
  </section>

  <section id="payments"><h2 class="sec-title">지급 분석</h2>
    <div class="grid-2">{charts_html}</div></section>

  <section id="quality"><h2 class="sec-title">데이터 품질</h2>
    {quality_tiles(quality)}{quality_more}</section>

  <section id="sql"><h2 class="sec-title">SQL 집계</h2>
    {highlight}{sql_html}</section>

  <section id="method"><h2 class="sec-title">분석 기준</h2>{notes}</section>

  <footer>출처: {esc(src['portal'])} {esc(src['dataset'])} ({esc(src['url'])}) · 공공데이터를 가공한 분석 결과이며 공식 통계가 아닙니다.</footer>
</div></div>"""
    doc = (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>계약·대금지급 데이터 분석 대시보드</title>"
        f"<style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")
    return output_path