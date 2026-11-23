import pytest

from conftest import make_frame, write_csv
from src import data_quality as dv
from src.data_quality import DataLoadError, find_default_csv, load_csv


def test_load_cp949_csv(sample_csv):
    result = load_csv(sample_csv)
    assert result.encoding == "cp949"
    assert result.row_count == 7
    assert len(result.df.columns) == 23
    assert result.df.loc[0, "기관명"] == "가기관"


def test_load_utf8_csv(tmp_path):
    path = write_csv(tmp_path / "utf8.csv", make_frame(), encoding="utf-8-sig")
    assert load_csv(path).encoding == "utf-8-sig"


def test_values_kept_as_text_and_blank_not_nan(sample_csv):
    df = load_csv(sample_csv).df
    assert df.loc[4, "지급결정금액"] == ""          # 결측은 빈 문자열 그대로
    assert df.loc[6, "계약금액"] == "3.82E+11"       # 원본 표기 보존


def test_missing_required_column(tmp_path):
    path = write_csv(tmp_path / "bad.csv", make_frame().drop(columns=["지급결정금액"]))
    with pytest.raises(DataLoadError, match="필수 컬럼"):
        load_csv(path)


@pytest.mark.parametrize("name, content", [("x.txt", "a,b\n1,2\n"), ("empty.csv", "")])
def test_bad_extension_or_empty(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    with pytest.raises(DataLoadError):
        load_csv(path)


def test_missing_file(tmp_path):
    with pytest.raises(DataLoadError, match="찾을 수 없습니다"):
        load_csv(tmp_path / "none.csv")


def test_find_default_csv(tmp_path, sample_csv):
    assert find_default_csv(tmp_path) == sample_csv
    assert find_default_csv(tmp_path / "empty_dir") is None


@pytest.mark.parametrize("text, expected", [
    ("1000", (1000, dv.OK)), ("1,000", (1000, dv.OK)), ("12.0", (12, dv.OK)),
    ("", (None, dv.BLANK)), ("3.82E+11", (382_000_000_000, dv.SCI)),
    ("abc", (None, dv.INVALID)), ("1.5", (None, dv.INVALID)),
])
def test_parse_integer(text, expected):
    assert dv.parse_integer(text) == expected


def test_quality_report_counts():
    report = dv.validate(make_frame())
    assert report.row_count == 7 and report.column_count == 23
    assert report.check_value("완전 중복 행") == 1
    assert report.check_value("대금지급번호 중복 행") == 2
    assert report.check_value("대금지급번호 동일·내용 상이") == 1
    assert report.check_value("계약납품요구번호 지수표기 손상") == 1
    assert report.check_value("계약금액 지수표기") == 1
    assert report.check_value("입력일자 형식 오류") == 0
    assert report.date_range == ("2024-01-05", "2024-03-20")


def test_missing_values_counted_not_filled():
    report = dv.validate(make_frame())
    stats = report.column_stats.set_index("컬럼")
    assert stats.loc["지급결정금액", "결측건수"] == 1
    assert stats.loc["지급결정금액", "결측률(%)"] == round(1 / 7 * 100, 2)


def test_wrong_types_detected():
    df = make_frame()
    df.loc[0, "지출금액"] = "백만원"
    df.loc[1, "차감금액"] = "-10"
    df.loc[2, "입력일자"] = "2024/02/10"
    report = dv.validate(df)
    assert report.check_value("지출금액 숫자 형식 오류") == 1
    assert report.check_value("차감금액 음수") == 1
    assert report.check_value("입력일자 형식 오류") == 1


def test_business_rule_checks():
    df = make_frame()
    df.loc[0, "지급결정금액"] = "2000000"   # 계약금액 1,000,000 초과, 요청 400,000 초과
    report = dv.validate(df)
    assert report.check_value("지급결정금액>계약금액") == 1
    assert report.check_value("지급결정금액>지급요청금액") == 1


def test_save_outputs(tmp_path):
    paths = dv.validate(make_frame()).save(tmp_path)
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)