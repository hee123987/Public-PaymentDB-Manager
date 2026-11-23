"""
CSV 로딩 및 데이터 품질 검증
[ 처리 기준 ]
- 로딩: 파일 형식·인코딩(UTF-8/CP949)·필수 컬럼 확인
- 원본 보존: 전체 컬럼 문자열 로딩, 빈 값의 NaN 변환 방지
- 품질 검증: 결측·중복·자료형·범위·업무 규칙 점검
- 검증 결과: 원본 데이터 변경 없이 결과만 보고
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

import config

logger = logging.getLogger(__name__)


class DataLoadError(ValueError):
    """CSV 로딩 실패 또는 파일 구조 오류"""


@dataclass
class LoadResult:
    df: pd.DataFrame
    path: Path
    encoding: str

    @property
    def row_count(self) -> int:
        return len(self.df)


def _read(path: Path, encoding: str) -> pd.DataFrame:
    return pd.read_csv(path, encoding=encoding, dtype=str,
                       keep_default_na=False, na_filter=False)


def load_csv(path, encoding: str | None = None,
             required_columns=config.REQUIRED_COLUMNS) -> LoadResult:
    path = Path(path)
    if not path.exists():
        raise DataLoadError(f"파일을 찾을 수 없습니다: {path}")
    if not path.is_file():
        raise DataLoadError(f"파일이 아닌 경로입니다: {path}")
    if path.suffix.lower() != ".csv":
        raise DataLoadError(f"CSV 파일(.csv)만 불러올 수 있습니다: {path.name}")
    if path.stat().st_size == 0:
        raise DataLoadError(f"빈 파일입니다: {path.name}")

    encodings = (encoding,) if encoding else config.ENCODINGS
    df, used = None, None
    for enc in encodings:
        try:
            df, used = _read(path, enc), enc
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            raise DataLoadError(f"데이터가 없는 CSV입니다: {path.name}") from None
        except pd.errors.ParserError as exc:
            raise DataLoadError(f"CSV 형식이 올바르지 않습니다: {exc}") from None
    if df is None:
        raise DataLoadError(
            f"인코딩을 판별하지 못했습니다({', '.join(encodings)} 시도). "
            "파일 인코딩을 확인해 주세요.")

    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise DataLoadError(f"필수 컬럼이 없습니다: {', '.join(missing)}")

    # 값 앞뒤 공백 제거
    df = df.apply(lambda s: s.str.strip())
    logger.info("CSV 로딩 완료: %s (%s건, %s개 컬럼, 인코딩 %s)",
                path.name, len(df), len(df.columns), used)
    return LoadResult(df=df, path=path, encoding=used)


def find_default_csv(data_dir=config.DATA_DIR) -> Path | None:
    """data 폴더의 기본 파일 또는 첫 번째 CSV 탐색"""
    data_dir = Path(data_dir)
    preferred = data_dir / config.DEFAULT_CSV_NAME
    if preferred.is_file():
        return preferred
    candidates = sorted(data_dir.glob("*.csv"))
    return candidates[0] if candidates else None


# ==================================================================== 데이터 품질 검증
INT_RE = re.compile(r"-?\d+")
INT_DOT_ZERO_RE = re.compile(r"-?\d+\.0+")
SCI_RE = re.compile(r"-?\d+(?:\.\d+)?[eE][+-]?\d+")
DATE_FORMAT = "%Y-%m-%d"

# 변환 상태 코드
OK, BLANK, SCI, INVALID = "ok", "blank", "sci", "invalid"


def is_sci_notation(text: str) -> bool:
    return bool(text) and bool(SCI_RE.fullmatch(text))


def parse_integer(text) -> tuple[int | None, str]:
    """
    문자열의 정수 변환 및 상태 코드 반환
    지수표기(예: 3.82E+11)는 정수로 변환하되 정밀도 손실 가능성을 표시
    """
    if text is None:
        return None, BLANK
    s = str(text).strip().replace(",", "")
    if s == "":
        return None, BLANK
    if INT_RE.fullmatch(s) or INT_DOT_ZERO_RE.fullmatch(s):
        return int(Decimal(s)), OK
    if SCI_RE.fullmatch(s):
        try:
            value = Decimal(s)
        except InvalidOperation:
            return None, INVALID
        if value == value.to_integral_value():
            return int(value), SCI
    return None, INVALID


def is_valid_date(text: str) -> bool:
    try:
        return datetime.strptime(text, DATE_FORMAT).strftime(DATE_FORMAT) == text
    except (TypeError, ValueError):
        return False


def numeric_frame(df: pd.DataFrame, columns=config.INTEGER_COLUMNS):
    """정수 컬럼의 변환값(Int64) 및 상태 DataFrame 반환"""
    values, statuses = {}, {}
    for col in columns:
        parsed = [parse_integer(v) for v in df[col]]
        values[col] = pd.array([p[0] for p in parsed], dtype="Int64")
        statuses[col] = [p[1] for p in parsed]
    return (pd.DataFrame(values, index=df.index),
            pd.DataFrame(statuses, index=df.index))


@dataclass
class QualityReport:
    row_count: int
    column_count: int
    column_stats: pd.DataFrame
    checks: pd.DataFrame
    missing_by_group: pd.DataFrame
    date_range: tuple[str | None, str | None]
    extras: dict = field(default_factory=dict)

    def check_value(self, item: str):
        row = self.checks.loc[self.checks["검사항목"] == item, "건수"]
        return int(row.iloc[0]) if len(row) else None

    def save(self, results_dir=config.RESULTS_DIR) -> list[Path]:
        results_dir = Path(results_dir)
        results_dir.mkdir(parents=True, exist_ok=True)
        paths = [
            results_dir / "quality_column_summary.csv",
            results_dir / "quality_checks.csv",
            results_dir / "quality_missing_by_group.csv",
        ]
        self.column_stats.to_csv(paths[0], index=False, encoding="utf-8-sig")
        self.checks.to_csv(paths[1], index=False, encoding="utf-8-sig")
        self.missing_by_group.to_csv(paths[2], index=False, encoding="utf-8-sig")
        return paths


def _column_stats(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    rows = []
    for col in df.columns:
        blank = int((df[col] == "").sum())
        rows.append({
            "컬럼": col,
            "결측건수": blank,
            "결측률(%)": round(blank / total * 100, 2) if total else 0.0,
            "고유값수": int(df.loc[df[col] != "", col].nunique()),
        })
    return pd.DataFrame(rows)


def validate(df: pd.DataFrame) -> QualityReport:
    total = len(df)
    checks: list[dict] = []

    def add(item, count, level, desc):
        checks.append({"검사항목": item, "건수": int(count), "판정": level, "설명": desc})

    add("전체 행 수", total, "정보", "원본 CSV의 데이터 행 수(헤더 제외)")

    # 1) 완전 중복 행
    full_dup = int(df.duplicated().sum())
    add("완전 중복 행", full_dup, "주의" if full_dup else "정상",
        "모든 컬럼 값이 앞 행과 동일한 행(첫 행 제외). DB에는 보존하되 분석 뷰에서 제외")

    # 2) 대금지급번호 중복
    pay = df["대금지급번호"]
    pay_dup_mask = pay.duplicated(keep=False)
    distinct_rows = df.drop_duplicates()
    differing = distinct_rows.groupby("대금지급번호").size()
    add("대금지급번호 중복 행", int(pay.duplicated().sum()), "주의",
        "같은 대금지급번호가 다시 나타난 행 수. 단독 기본키로 사용할 수 없음")
    add("대금지급번호 중복 고유값", int(pay[pay_dup_mask].nunique()), "정보",
        "두 번 이상 등장한 대금지급번호 개수")
    add("대금지급번호 동일·내용 상이", int((differing > 1).sum()), "정보",
        "번호는 같지만 금액 등 내용이 다른 경우(분할 지급 등 세부 레코드로 보고 보존)")

    # 3) 계약납품요구번호 중복 및 다중 지급 레코드
    contract = df["계약납품요구번호"]
    sci_contract = contract.map(is_sci_notation)
    valid_contract = contract[~sci_contract & (contract != "")]
    per_contract = valid_contract.value_counts()
    add("계약납품요구번호 지수표기 손상", int(sci_contract.sum()), "오류" if sci_contract.any() else "정상",
        "예: 2.02E+09. 원본 번호가 지수표기로 손상되어 계약 식별 불가 → 계약 단위 집계에서 개별 행으로 취급")
    add("다중 지급 레코드 계약 수", int((per_contract > 1).sum()), "정보",
        "정상 번호 기준 2건 이상 지급 레코드가 있는 계약. 계약금액 단순 합계 시 중복 집계 위험")

    # 4) 숫자 컬럼 자료형
    values, statuses = numeric_frame(df)
    for col in config.INTEGER_COLUMNS:
        n_sci = int((statuses[col] == SCI).sum())
        n_bad = int((statuses[col] == INVALID).sum())
        if n_sci:
            add(f"{col} 지수표기", n_sci, "주의",
                "지수표기 금액(예: 3.82E+11). 정수로 변환하되 유효숫자 손실 가능성 표시")
        add(f"{col} 숫자 형식 오류", n_bad, "오류" if n_bad else "정상",
            "숫자로 해석할 수 없는 값(DB에는 NULL로 저장하고 변환 내역에 기록)")
        n_neg = int((values[col] < 0).sum())
        add(f"{col} 음수", n_neg, "오류" if n_neg else "정상", "음수 값")

    # 5) 날짜
    dates = df[config.DATE_COLUMN]
    bad_dates = int((~dates.map(is_valid_date)).sum())
    add("입력일자 형식 오류", bad_dates, "오류" if bad_dates else "정상", "YYYY-MM-DD가 아닌 값")
    valid_dates = dates[dates.map(is_valid_date)]
    date_range = (valid_dates.min(), valid_dates.max()) if len(valid_dates) else (None, None)

    # 6) 업무 규칙 검증 (주의·참고 항목 포함)
    v = values
    both = v["지출금액"].notna() & v["지급결정금액"].notna()
    add("지출금액≠지급결정금액", int((both & (v["지출금액"] != v["지급결정금액"])).sum()), "참고",
        "두 값이 모두 있는 행 중 서로 다른 경우")
    gt_req = v["지급결정금액"].notna() & v["지급요청금액"].notna() & (v["지급결정금액"] > v["지급요청금액"])
    add("지급결정금액>지급요청금액", int(gt_req.sum()), "주의", "요청보다 많이 결정된 행")
    gt_con = v["지급결정금액"].notna() & v["계약금액"].notna() & (v["지급결정금액"] > v["계약금액"])
    add("지급결정금액>계약금액", int(gt_con.sum()), "주의",
        "한 번의 지급결정이 계약금액보다 큰 행(변경계약·단가계약 가능성)")
    gt_adv = v["선급금청산금액"].notna() & v["선급금금액"].notna() & (v["선급금청산금액"] > v["선급금금액"])
    add("선급금청산금액>선급금금액", int(gt_adv.sum()), "참고", "해당 레코드 기준 청산액이 선급금보다 큰 행")
    delay_no_penalty = (v["지체일수"] > 0) & (v["지체상금금액"] == 0)
    add("지체일수>0·지체상금 0", int(delay_no_penalty.fillna(False).sum()), "참고",
        "지체일수가 있으나 지체상금이 0원인 행(면책 등 사유 가능)")

    # 7) 지급결정금액 결측 분포
    miss = df["지급결정금액"] == ""
    by_group = (pd.DataFrame({"업무구분명": df["업무구분명"], "지출구분명": df["지출구분명"], "결측": miss})
                .groupby(["업무구분명", "지출구분명"])["결측"]
                .agg(전체건수="size", 지급결정금액_결측="sum")
                .reset_index())
    by_group["결측률(%)"] = (by_group["지급결정금액_결측"] / by_group["전체건수"] * 100).round(2)

    return QualityReport(
        row_count=total,
        column_count=len(df.columns),
        column_stats=_column_stats(df),
        checks=pd.DataFrame(checks),
        missing_by_group=by_group,
        date_range=date_range,
        extras={"full_duplicates": full_dup, "sci_contract_no": int(sci_contract.sum())},
    )
