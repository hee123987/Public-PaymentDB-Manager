import pytest

import main as cli
from src import analysis as an
from src import database as dbm
from src.report import generate_report


def test_search_filters(loaded_conn):
    c = loaded_conn
    assert an.count_records(c) == 6
    assert an.count_records(c, agency="나기관") == 2
    assert an.count_records(c, contract_name="청소") == 1
    assert an.count_records(c, start_date="2024-02-01", end_date="2024-02-29") == 2
    assert an.count_records(c, min_amount="400,000", max_amount=1_000_000) == 4
    assert an.count_records(c, agency="가기관", start_date="2024-02-01") == 2


def test_search_sort_paging_and_injection(loaded_conn):
    df = an.search_records(loaded_conn, order_by="계약금액", descending=True, limit=2)
    assert len(df) == 2 and df.iloc[0]["계약금액"] == 382_000_000_000
    assert len(an.search_records(loaded_conn, limit=2, offset=4)) == 2
    assert an.count_records(loaded_conn, agency="%") == 0          # 와일드카드가 문자로 처리됨
    assert an.count_records(loaded_conn, agency="' OR 1=1 --") == 0
    assert "대표자명" not in an.search_records(loaded_conn, limit=None).columns


@pytest.mark.parametrize("kwargs", [
    {"start_date": "2024-13-01"}, {"start_date": "2024-03-01", "end_date": "2024-01-01"},
    {"min_amount": "abc"}, {"min_amount": -1}, {"min_amount": 10, "max_amount": 5},
])
def test_search_invalid_conditions(loaded_conn, kwargs):
    with pytest.raises(an.QueryError):
        an.count_records(loaded_conn, **kwargs)


def test_invalid_sort_column(loaded_conn):
    with pytest.raises(an.QueryError):
        an.search_records(loaded_conn, order_by="record_id; DROP TABLE payment_records")


def test_overview_and_aggregations(loaded_conn):
    c = loaded_conn
    o = an.overview(c)
    assert (o["stored_records"], o["analysis_records"], o["contract_units"]) == (7, 6, 4)
    assert (o["decided_count"], o["decided_missing"], o["decided_sum"]) == (5, 1, 3_350_000)
    monthly = an.monthly_decided(c)
    assert monthly["입력월"].tolist() == ["2024-01", "2024-02", "2024-03"]
    assert monthly["지급결정금액합계"].tolist() == [400_000, 650_000, 2_300_000]
    assert monthly["지급결정금액_결측"].tolist() == [0, 0, 1]


def test_contract_amount_not_double_counted(loaded_conn):
    df = an.contract_amount_by_agency(loaded_conn).set_index("기관명")
    assert df.loc["가기관", "계약금액_계약단위"] == 1_000_000      # 계약 1건
    assert df.loc["가기관", "계약금액_레코드단순합"] == 3_000_000  # 지급 레코드 3건 단순 합


def test_contract_vs_decided_buckets(loaded_conn):
    df = an.contract_vs_decided(loaded_conn).set_index("구간")["계약단위수"].to_dict()
    assert df == {"50% 미만": 1, "100% (일치)": 1, "100% 초과": 1, "지급결정금액 결측": 1}


def test_group_aggregations(loaded_conn):
    exp = an.by_expense_type(loaded_conn).set_index("지출구분명")
    assert exp.loc["준공금", "지급결정금액합계"] == 1_050_000
    biz = an.by_business_type(loaded_conn).set_index("업무구분명")
    assert biz.loc["물품", "레코드수"] == 2 and biz.loc["공사", "계약단위수"] == 1
    assert an.top_agencies_by_decided(loaded_conn).iloc[0]["기관명"] == "다기관"


def test_mask_name():
    assert [an.mask_name(n) for n in ["홍길동", "김철", "남궁민수", "", None]] == ["홍*동", "김*", "남**수", "", ""]


def test_charts_and_report_created(loaded_conn, tmp_path, sample_csv):
    from src.data_quality import load_csv, validate
    results = an.run_aggregations(loaded_conn)
    assert len(an.save_aggregations(results, tmp_path)) == 8
    charts = an.create_charts(loaded_conn, results, tmp_path)
    assert all(p.exists() and p.stat().st_size > 5000 for p in charts)
    path = generate_report(results, charts, validate(load_csv(sample_csv).df),
                           dbm.value_changes(loaded_conn), dbm.import_history(loaded_conn),
                           tmp_path / "report.html")
    text = path.read_text(encoding="utf-8")
    assert "데이터 품질 검사 결과" in text and "data:image/png;base64" in text
    assert "홍길동" not in text and "김담당" not in text   # 개인정보 미포함
    assert "http://" not in text.replace("https://www.data.go.kr", "")


def test_cli_scenario(conn, sample_csv, tmp_path):
    feed = iter(["9", "3", "1", "2", "3", "가기관", "", "", "", "", "", "", "", "s", "q", "4", "6", "0"])
    out = []

    def fake_input(_=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError from None

    app = cli.App(conn, csv_path=sample_csv, results_dir=tmp_path, input_fn=fake_input, output_fn=out.append)
    app.run_menu()
    text = "\n".join(out)
    assert "[입력 오류]" in text
    assert "DB에 데이터가 없습니다" in text
    assert "적재 완료" in text
    assert "검색 결과 3건" in text
    assert (tmp_path / "search_result.csv").exists()
    assert (tmp_path / "report.html").exists()
    assert text.rstrip().endswith("종료합니다.")


def test_report_number_and_josa_helpers():
    from src.report import josa, won
    assert won(5_685_823_806_459) == "5조 6,858억 원"
    assert won(931_803_628_260) == "9,318.0억 원"
    assert won(1_412_890) == "1,412,890원"
    assert won(None) == "-"
    assert [josa(w, "이", "가") for w in ["환경부", "선급금", "용역(설계감리)", "2024-03", "2024-02"]] == \
        ["환경부가", "선급금이", "용역(설계감리)이", "2024-03이", "2024-02가"]


def test_report_dashboard_sections(loaded_conn, tmp_path):
    results = an.run_aggregations(loaded_conn)
    charts = an.create_charts(loaded_conn, results, tmp_path)
    text = generate_report(results, charts, None, dbm.value_changes(loaded_conn),
                           dbm.import_history(loaded_conn), tmp_path / "r.html").read_text(encoding="utf-8")
    for anchor in ('id="summary"', 'id="payments"', 'id="quality"', 'id="sql"', 'id="method"'):
        assert anchor in text
    assert "FINDING 1" in text and "<details" in text
    assert "3,350,000원" in text   # KPI 지급결정금액 합계 (1억 미만은 원 단위)
