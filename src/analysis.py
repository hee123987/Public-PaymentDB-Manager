"""계약·대금지급 데이터 SQL 조회·집계 및 시각화
[ 집계 기준 ]
- 레코드 단위: v_payments (완전 중복 행 제외)
- 계약 단위: v_contracts (계약별 1행, 계약금액 중복 집계 방지)
- 금액 결측: NULL 유지, 집계 시 제외
- 날짜 기준: 데이터 입력일 (계약일·실제 지급일과 구분)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # 화면 출력 없이 PNG 파일 저장
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

import config  # noqa: E402

NO_AGENCY = "(기관명 없음)"

SORT_COLUMNS = {
    "입력일자": "input_date",
    "기관명": "agency_name",
    "계약금액": "contract_amount",
    "지급결정금액": "decided_amount",
    "계약건명": "contract_name",
}

SEARCH_SELECT = """
SELECT record_id      AS 레코드ID,
       input_date     AS 입력일자,
       agency_name    AS 기관명,
       contract_name  AS 계약건명,
       company_name   AS 업체명,
       business_type  AS 업무구분명,
       expense_type   AS 지출구분명,
       contract_amount AS 계약금액,
       decided_amount AS 지급결정금액,
       payment_no     AS 대금지급번호,
       contract_no    AS 계약납품요구번호
FROM v_payments
"""


class QueryError(ValueError):
    """잘못된 검색 조건"""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _check_date(value: str, label: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise QueryError(f"{label}은(는) YYYY-MM-DD 형식이어야 합니다: {value}") from None
    return value


def _check_amount(value, label: str) -> int:
    if isinstance(value, bool):
        raise QueryError(f"{label}은(는) 0 이상의 정수여야 합니다.")
    try:
        number = int(str(value).replace(",", "").strip())
    except ValueError:
        raise QueryError(f"{label}은(는) 0 이상의 정수여야 합니다: {value}") from None
    if number < 0:
        raise QueryError(f"{label}은(는) 0 이상이어야 합니다.")
    return number


def build_search(agency=None, contract_name=None, start_date=None, end_date=None,
                 min_amount=None, max_amount=None):
    conditions, params = [], []
    if agency:
        conditions.append("agency_name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(agency.strip())}%")
    if contract_name:
        conditions.append("contract_name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(contract_name.strip())}%")
    if start_date:
        conditions.append("input_date >= ?")
        params.append(_check_date(start_date, "시작일"))
    if end_date:
        conditions.append("input_date <= ?")
        params.append(_check_date(end_date, "종료일"))
    if start_date and end_date and start_date > end_date:
        raise QueryError("시작일이 종료일보다 늦을 수 없습니다.")
    lo = _check_amount(min_amount, "최소 계약금액") if min_amount not in (None, "") else None
    hi = _check_amount(max_amount, "최대 계약금액") if max_amount not in (None, "") else None
    if lo is not None:
        conditions.append("contract_amount >= ?")
        params.append(lo)
    if hi is not None:
        conditions.append("contract_amount <= ?")
        params.append(hi)
    if lo is not None and hi is not None and lo > hi:
        raise QueryError("최소 계약금액이 최대 계약금액보다 클 수 없습니다.")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return where, params


def search_records(conn: sqlite3.Connection, agency=None, contract_name=None, start_date=None,
                   end_date=None, min_amount=None, max_amount=None, order_by="입력일자",
                   descending=False, limit=20, offset=0) -> pd.DataFrame:
    """조건 검색 (limit=None이면 전체 결과 반환)"""
    if order_by not in SORT_COLUMNS:
        raise QueryError(f"정렬 기준은 {', '.join(SORT_COLUMNS)} 중 하나여야 합니다.")
    where, params = build_search(agency, contract_name, start_date, end_date, min_amount, max_amount)
    direction = "DESC" if descending else "ASC"
    sql = f"{SEARCH_SELECT}{where} ORDER BY {SORT_COLUMNS[order_by]} {direction}, record_id"
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise QueryError("limit은 1 이상의 정수여야 합니다.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise QueryError("offset은 0 이상의 정수여야 합니다.")
        sql += " LIMIT ? OFFSET ?"
        params = [*params, limit, offset]
    return pd.read_sql_query(sql, conn, params=params)


def count_records(conn, **filters) -> int:
    where, params = build_search(**filters)
    return conn.execute(f"SELECT COUNT(*) FROM v_payments{where}", params).fetchone()[0]


# ------------------------------------------------------------------ 집계 SQL
def _df(conn, sql, params=()):
    df = pd.read_sql_query(sql, conn, params=params)
    # 결측값으로 인해 float로 읽힌 정수 데이터를 Int64로 변환
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_float_dtype(s) and (s.dropna() % 1 == 0).all():
            df[col] = s.astype("Int64")
    return df


def overview(conn) -> dict:
    """1. 전체 레코드 건수와 기본 지표"""
    row = conn.execute("""
        SELECT (SELECT COUNT(*) FROM payment_records)                       AS stored_records,
               COUNT(*)                                                    AS analysis_records,
               COUNT(DISTINCT payment_no)                                  AS distinct_payment_no,
               (SELECT COUNT(*) FROM v_contracts)                          AS contract_units,
               (SELECT COUNT(*) FROM v_contracts WHERE contract_no_valid = 1) AS valid_contracts,
               SUM(contract_no_valid = 0)                                  AS invalid_contract_no_rows,
               COUNT(decided_amount)                                       AS decided_count,
               SUM(decided_amount IS NULL)                                 AS decided_missing,
               SUM(decided_amount)                                         AS decided_sum,
               COUNT(DISTINCT agency_name)                                 AS agencies,
               SUM(contract_amount)                                        AS contract_amount_record_sum,
               (SELECT SUM(contract_amount) FROM v_contracts)              AS contract_amount_unit_sum,
               MIN(input_date) AS min_date, MAX(input_date) AS max_date
        FROM v_payments
    """).fetchone()
    return dict(row)


def records_by_agency(conn, limit=10) -> pd.DataFrame:
    """2. 기관별 계약 관련 레코드 건수"""
    return _df(conn, f"""
        SELECT COALESCE(agency_name, '{NO_AGENCY}') AS 기관명,
               COUNT(*)               AS 레코드수,
               COUNT(DISTINCT CASE WHEN contract_no_valid = 1 THEN contract_no END) AS 정상계약번호수,
               COUNT(decided_amount)  AS 지급결정건수,
               SUM(decided_amount)    AS 지급결정금액합계
        FROM v_payments
        GROUP BY 1
        ORDER BY 레코드수 DESC, 기관명
        LIMIT ?""", (limit,))


def contract_amount_by_agency(conn, limit=10) -> pd.DataFrame:
    """3. 기관별 계약금액 (계약 단위 합계와 레코드 단순 합계 비교)"""
    return _df(conn, f"""
        WITH c AS (
            SELECT COALESCE(agency_name, '{NO_AGENCY}') AS 기관명,
                   COUNT(*) AS 계약단위수,
                   SUM(contract_amount) AS 계약금액_계약단위
            FROM v_contracts GROUP BY 1
        ), r AS (
            SELECT COALESCE(agency_name, '{NO_AGENCY}') AS 기관명,
                   SUM(contract_amount) AS 계약금액_레코드단순합
            FROM v_payments GROUP BY 1
        )
        SELECT c.기관명, c.계약단위수, c.계약금액_계약단위, r.계약금액_레코드단순합
        FROM c JOIN r USING (기관명)
        ORDER BY c.계약금액_계약단위 DESC
        LIMIT ?""", (limit,))


def monthly_decided(conn) -> pd.DataFrame:
    """4. 월별 지급결정금액 합계 (입력일자 기준)"""
    return _df(conn, """
        SELECT substr(input_date, 1, 7)   AS 입력월,
               COUNT(*)                  AS 레코드수,
               COUNT(decided_amount)     AS 지급결정건수,
               SUM(decided_amount IS NULL) AS 지급결정금액_결측,
               SUM(decided_amount)       AS 지급결정금액합계
        FROM v_payments
        WHERE input_date IS NOT NULL
        GROUP BY 입력월
        ORDER BY 입력월""")


def by_expense_type(conn) -> pd.DataFrame:
    """5. 지출구분별 지급 현황"""
    return _df(conn, """
        SELECT expense_type              AS 지출구분명,
               COUNT(*)                  AS 레코드수,
               COUNT(decided_amount)     AS 지급결정건수,
               SUM(decided_amount)       AS 지급결정금액합계,
               CAST(ROUND(AVG(decided_amount)) AS INTEGER) AS 평균지급결정금액
        FROM v_payments
        GROUP BY expense_type
        ORDER BY 지급결정금액합계 DESC""")


def by_business_type(conn) -> pd.DataFrame:
    """6. 업무구분별 계약·지급 현황"""
    return _df(conn, """
        WITH r AS (
            SELECT business_type, COUNT(*) AS 레코드수, COUNT(decided_amount) AS 지급결정건수,
                   SUM(decided_amount) AS 지급결정금액합계
            FROM v_payments GROUP BY business_type
        ), c AS (
            SELECT business_type, COUNT(*) AS 계약단위수, SUM(contract_amount) AS 계약금액_계약단위
            FROM v_contracts GROUP BY business_type
        )
        SELECT r.business_type AS 업무구분명, r.레코드수, c.계약단위수, r.지급결정건수,
               r.지급결정금액합계, c.계약금액_계약단위
        FROM r JOIN c USING (business_type)
        ORDER BY r.레코드수 DESC""")


def contract_vs_decided(conn) -> pd.DataFrame:
    """7. 계약금액 대비 (분석 기간 내) 지급결정금액 비율 분포 — 계약 단위"""
    return _df(conn, """
        WITH c AS (
            SELECT contract_amount, decided_total,
                   CASE
                       WHEN contract_amount IS NULL OR contract_amount = 0 THEN '계약금액 결측'
                       WHEN decided_total IS NULL THEN '지급결정금액 결측'
                       WHEN decided_total = contract_amount THEN '100% (일치)'
                       WHEN decided_total < contract_amount * 0.5 THEN '50% 미만'
                       WHEN decided_total < contract_amount THEN '50% 이상 100% 미만'
                       ELSE '100% 초과'
                   END AS 구간
            FROM v_contracts
        )
        SELECT 구간, COUNT(*) AS 계약단위수,
               SUM(contract_amount) AS 계약금액합계,
               SUM(decided_total)   AS 지급결정금액합계
        FROM c
        GROUP BY 구간
        ORDER BY CASE 구간
            WHEN '50% 미만' THEN 1 WHEN '50% 이상 100% 미만' THEN 2 WHEN '100% (일치)' THEN 3
            WHEN '100% 초과' THEN 4 WHEN '지급결정금액 결측' THEN 5 ELSE 6 END""")


def top_agencies_by_decided(conn, limit=10) -> pd.DataFrame:
    """8-1. 지급결정금액 상위 기관"""
    return _df(conn, f"""
        SELECT COALESCE(agency_name, '{NO_AGENCY}') AS 기관명,
               COUNT(decided_amount) AS 지급결정건수,
               SUM(decided_amount)   AS 지급결정금액합계
        FROM v_payments
        WHERE decided_amount IS NOT NULL
        GROUP BY 1
        ORDER BY 지급결정금액합계 DESC
        LIMIT ?""", (limit,))


def top_records_by_decided(conn, limit=10) -> pd.DataFrame:
    """8-2. 지급결정금액 상위 레코드 (개인정보 컬럼 제외)"""
    return _df(conn, f"""
        SELECT input_date AS 입력일자, COALESCE(agency_name, '{NO_AGENCY}') AS 기관명,
               contract_name AS 계약건명, business_type AS 업무구분명, expense_type AS 지출구분명,
               contract_amount AS 계약금액, decided_amount AS 지급결정금액,
               has_sci_amount AS 지수표기금액여부
        FROM v_payments
        WHERE decided_amount IS NOT NULL
        ORDER BY decided_amount DESC, record_id
        LIMIT ?""", (limit,))


def mask_name(name) -> str:
    """개인 이름 마스킹: 홍길동 → 홍*동, 김철 → 김*"""
    if name is None or str(name).strip() == "":
        return ""
    s = str(name).strip()
    if len(s) <= 1:
        return "*"
    if len(s) == 2:
        return s[0] + "*"
    return s[0] + "*" * (len(s) - 2) + s[-1]


# ==================================================================== 집계 실행·저장·그래프
logger = logging.getLogger(__name__)

EOK = 100_000_000  # 1억 원
SURFACE = "#ffffff"
TEXT = "#30303a"
TEXT_MUTED = "#5f5c68"
GRID = "#ece9ee"
PRIMARY = "#8a7dd0"
EXPENSE_COLORS = {"선급금": "#8a7dd0", "일반금": "#35a08a", "준공금": "#e3874f", "기성금": "#5f97d6"}
KOREAN_FONTS = ["Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR",
                "Noto Sans KR", "Noto Sans CJK JP"]


def setup_korean_font() -> str | None:
    """설치된 한글 폰트 탐색 및 Matplotlib 기본 폰트 설정"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONTS:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
    logger.warning("한글 폰트를 찾지 못했습니다. 그래프의 한글이 깨질 수 있습니다.")
    return None


def run_aggregations(conn, top_n: int = 10) -> dict:
    return {
        "overview": overview(conn),
        "records_by_agency": records_by_agency(conn, top_n),
        "contract_amount_by_agency": contract_amount_by_agency(conn, top_n),
        "monthly_decided": monthly_decided(conn),
        "by_expense_type": by_expense_type(conn),
        "by_business_type": by_business_type(conn),
        "contract_vs_decided": contract_vs_decided(conn),
        "top_agencies_by_decided": top_agencies_by_decided(conn, top_n),
        "top_records_by_decided": top_records_by_decided(conn, top_n),
    }


def save_aggregations(results: dict, results_dir=config.RESULTS_DIR) -> list[Path]:
    out_dir = Path(results_dir) / "sql"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, value in results.items():
        if name == "overview":
            continue
        path = out_dir / f"{name}.csv"
        value.to_csv(path, index=False, encoding="utf-8-sig")
        paths.append(path)
    return paths


# ------------------------------------------------------------------ 그래프
COMMA = FuncFormatter(lambda v, _: f"{v:,.0f}")


def _style(ax, title, subtitle, value_axis="y"):
    (ax.yaxis if value_axis == "y" else ax.xaxis).set_major_formatter(COMMA)
    fig = ax.figure
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=14, fontweight="bold", color=TEXT, pad=26)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=TEXT_MUTED, va="bottom")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_MUTED, labelsize=9)
    ax.set_axisbelow(True)


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_monthly(df, path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    values = df["지급결정금액합계"].fillna(0) / EOK
    bars = ax.bar(df["입력월"], values, color=PRIMARY, width=0.55)
    ax.bar_label(bars, labels=[f"{v:,.0f}억" for v in values], padding=3, fontsize=9, color=TEXT)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_ylabel("지급결정금액 (억 원)", color=TEXT_MUTED, fontsize=9)
    _style(ax, "월별 지급결정금액 합계",
           "기준: 입력일자 월 · 지급결정금액 결측 제외 · 완전 중복 행 제외")
    return _save(fig, path)


def chart_top_agencies(df, path):
    df = df.iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    values = df["지급결정금액합계"] / EOK
    bars = ax.barh(df["기관명"], values, color=PRIMARY, height=0.6)
    ax.bar_label(bars, labels=[f"{v:,.0f}억" for v in values], padding=3, fontsize=8, color=TEXT)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_xlabel("지급결정금액 (억 원)", color=TEXT_MUTED, fontsize=9)
    _style(ax, f"지급결정금액 상위 {len(df)}개 기관",
           "기준: 2024-01~03 입력분 · 지급결정금액 결측 제외 · 완전 중복 행 제외", "x")
    return _save(fig, path)


def chart_business_distribution(conn, path):
    df = conn.execute("""
        SELECT business_type, expense_type, COUNT(*) AS n
        FROM v_payments GROUP BY business_type, expense_type
    """).fetchall()
    businesses = sorted({r[0] for r in df}, key=lambda b: -sum(r[2] for r in df if r[0] == b))
    expenses = ["선급금", "기성금", "준공금", "일반금"]
    counts = {(r[0], r[1]): r[2] for r in df}
    fig, ax = plt.subplots(figsize=(8, 4.5))
    left = [0] * len(businesses)
    for i, exp in enumerate(expenses):
        widths = [counts.get((b, exp), 0) for b in businesses]
        ax.barh(businesses, widths, left=left, color=EXPENSE_COLORS[exp], height=0.55,
                label=exp, edgecolor=SURFACE, linewidth=2)
        left = [a + b for a, b in zip(left, widths)]
    for y, total in enumerate(left):
        ax.text(total, y, f"  {total:,}건", va="center", fontsize=9, color=TEXT)
    ax.invert_yaxis()
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_xlabel("레코드 수 (건)", color=TEXT_MUTED, fontsize=9)
    ax.set_xlim(0, max(left) * 1.15)
    ax.legend(title="지출구분", frameon=False, fontsize=9, title_fontsize=9,
              loc="lower right", ncol=4, bbox_to_anchor=(1, -0.32))
    _style(ax, "업무구분별 레코드 분포", "지출구분별 누적 · 완전 중복 행 제외", "x")
    return _save(fig, path)


def chart_expense_type(df, path):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    values = df["지급결정금액합계"].fillna(0) / EOK
    bars = ax.bar(df["지출구분명"], values, color=PRIMARY, width=0.55)
    labels = [f"{v:,.0f}억\n({n:,}건)" for v, n in zip(values, df["지급결정건수"])]
    ax.bar_label(bars, labels=labels, padding=3, fontsize=8, color=TEXT)
    ax.set_ylim(0, values.max() * 1.2)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_ylabel("지급결정금액 (억 원)", color=TEXT_MUTED, fontsize=9)
    _style(ax, "지출구분별 지급결정금액", "괄호: 지급결정금액이 있는 레코드 수 · 결측 제외")
    return _save(fig, path)


def create_charts(conn, results: dict, results_dir=config.RESULTS_DIR) -> list[Path]:
    setup_korean_font()
    out = Path(results_dir) / "charts"
    return [
        chart_monthly(results["monthly_decided"], out / "01_monthly_decided.png"),
        chart_top_agencies(results["top_agencies_by_decided"], out / "02_top_agencies.png"),
        chart_business_distribution(conn, out / "03_business_distribution.png"),
        chart_expense_type(results["by_expense_type"], out / "04_expense_type.png"),
    ]
