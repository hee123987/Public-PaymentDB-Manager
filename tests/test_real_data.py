"""실제 조달청 CSV가 data 폴더에 있을 때만 실행되는 통합 테스트."""
import pytest

from src import data_quality as dv
from src import database as dbm
from src.data_quality import find_default_csv, load_csv

REAL_CSV = find_default_csv()
pytestmark = pytest.mark.skipif(REAL_CSV is None, reason="data 폴더에 원본 CSV가 없습니다.")


@pytest.fixture(scope="module")
def real():
    return load_csv(REAL_CSV)


def test_real_shape(real):
    assert real.row_count == 48_901
    assert len(real.df.columns) == 23


def test_real_quality(real):
    report = dv.validate(real.df)
    assert report.check_value("완전 중복 행") == 146
    assert report.date_range == ("2024-01-01", "2024-03-31")


def test_real_import_twice(real, tmp_path):
    conn = dbm.connect(tmp_path / "real.db")
    try:
        first = dbm.import_dataframe(conn, real)
        second = dbm.import_dataframe(conn, real)
        assert first.rows_inserted == 48_901
        assert second.status == "SKIPPED"
        assert dbm.record_counts(conn) == {"total": 48_901, "analysis": 48_755, "duplicates_excluded": 146}
    finally:
        conn.close()
