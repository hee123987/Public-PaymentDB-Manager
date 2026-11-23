"""테스트 공용 픽스처: 원본과 같은 23개 컬럼을 가진 소형 CSV를 임시 폴더에 만든다."""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from src import database as dbm  # noqa: E402

BASE = {
    "업무구분명": "공사", "계약납품구분": "계약", "조달구분": "자체조달",
    "대금지급번호": "P-1", "대금지급처리구분": "직불", "계약납품요구번호": "C-1",
    "계약금액": "1000000", "계약건명": "도로 보수공사", "업체명": "가나건설",
    "대표자명": "홍길동", "기관명": "가기관", "기관담당부서명": "재무과", "기관담당자명": "김담당",
    "지출구분명": "준공금", "지체일수": "0", "지출금액": "400000", "지급요청금액": "400000",
    "지급결정금액": "400000", "차감금액": "0", "선급금금액": "0", "선급금청산금액": "0",
    "지체상금금액": "0", "입력일자": "2024-01-05",
}

# (설명, 변경값)
ROWS = [
    ("정상 1", {}),
    ("정상 1과 완전히 같은 행", {}),
    ("같은 계약 두 번째 지급", {"대금지급번호": "P-2", "지급결정금액": "600000", "지출금액": "600000",
                         "지급요청금액": "600000", "입력일자": "2024-02-10"}),
    ("지급번호 중복·내용 상이", {"대금지급번호": "P-2", "지급결정금액": "50000", "지출금액": "50000",
                          "지급요청금액": "50000", "입력일자": "2024-02-10"}),
    ("다른 기관·금액 결측", {"대금지급번호": "P-3", "계약납품요구번호": "C-2", "기관명": "나기관",
                       "계약건명": "청사 청소 용역", "업무구분명": "용역(설계감리)", "계약금액": "500000",
                       "지출금액": "", "지급요청금액": "", "지급결정금액": "", "차감금액": "",
                       "지체일수": "", "지출구분명": "기성금", "입력일자": "2024-03-02"}),
    ("지수표기 계약번호", {"대금지급번호": "P-4", "계약납품요구번호": "2.02E+09", "기관명": "나기관",
                     "업무구분명": "물품", "지출구분명": "일반금", "계약금액": "300000",
                     "지급결정금액": "300000", "지출금액": "300000", "지급요청금액": "300000",
                     "입력일자": "2024-03-15"}),
    ("지수표기 금액", {"대금지급번호": "P-5", "계약납품요구번호": "C-3", "기관명": "다기관",
                  "업무구분명": "물품", "지출구분명": "일반금", "계약금액": "3.82E+11",
                  "지급결정금액": "2000000", "지출금액": "2000000", "지급요청금액": "2000000",
                  "입력일자": "2024-03-20"}),
]


def make_frame(rows=ROWS) -> pd.DataFrame:
    return pd.DataFrame([{**BASE, **change} for _, change in rows], columns=config.REQUIRED_COLUMNS)


def write_csv(path: Path, df: pd.DataFrame, encoding="cp949") -> Path:
    df.to_csv(path, index=False, encoding=encoding)
    return path


@pytest.fixture
def sample_csv(tmp_path) -> Path:
    return write_csv(tmp_path / "sample.csv", make_frame())


@pytest.fixture
def conn(tmp_path):
    c = dbm.connect(tmp_path / "test.db")  # 원본 DB와 분리된 테스트 전용 DB
    yield c
    c.close()


@pytest.fixture
def loaded_conn(conn, sample_csv):
    from src.data_quality import load_csv
    dbm.import_dataframe(conn, load_csv(sample_csv))
    return conn
