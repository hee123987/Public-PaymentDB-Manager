
import pytest

from conftest import make_frame, write_csv, ROWS
from src import database as dbm
from src.data_quality import load_csv


def test_schema_created(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    assert {"payment_records", "import_history", "value_changes", "v_payments", "v_contracts"} <= names


def test_import_keeps_all_rows_and_flags(loaded_conn):
    c = loaded_conn
    assert dbm.record_counts(c) == {"total": 7, "analysis": 6, "duplicates_excluded": 1}
    row = c.execute("SELECT contract_no_valid FROM payment_records WHERE contract_no = '2.02E+09'").fetchone()
    assert row[0] == 0
    row = c.execute("SELECT contract_amount, has_sci_amount FROM payment_records WHERE payment_no='P-5'").fetchone()
    assert tuple(row) == (382_000_000_000, 1)
    # 결측은 0이 아닌 NULL
    row = c.execute("SELECT decided_amount, delay_days FROM payment_records WHERE payment_no='P-3'").fetchone()
    assert tuple(row) == (None, None)


def test_value_changes_logged(loaded_conn):
    df = dbm.value_changes(loaded_conn)
    assert len(df) == 1
    assert df.iloc[0]["컬럼"] == "계약금액" and df.iloc[0]["원본값"] == "3.82E+11"


def test_same_file_reimport_skipped(loaded_conn, sample_csv):
    result = dbm.import_dataframe(loaded_conn, load_csv(sample_csv))
    assert result.status == "SKIPPED" and result.rows_inserted == 0
    assert dbm.record_counts(loaded_conn)["total"] == 7


def test_same_content_other_file_not_duplicated(loaded_conn, tmp_path):
    # 인코딩이 달라 파일 해시는 다르지만 내용은 같은 경우 + 새 행 1건
    df = make_frame(ROWS + [("새 행", {"대금지급번호": "P-9", "입력일자": "2024-03-30"})])
    other = write_csv(tmp_path / "other.csv", df, encoding="utf-8-sig")
    result = dbm.import_dataframe(loaded_conn, load_csv(other))
    assert result.status == "SUCCESS"
    assert (result.rows_inserted, result.rows_ignored) == (1, 7)
    assert dbm.record_counts(loaded_conn)["total"] == 8


def test_failed_import_rolls_back(conn, sample_csv, monkeypatch):
    def broken(*args, **kwargs):
        return [(0, "h", 1, 1, 0, *([None] * 23))]  # payment_no NULL → NOT NULL 위반
    monkeypatch.setattr(dbm, "prepare_records", lambda df: (broken(), []))
    with pytest.raises(dbm.DatabaseError):
        dbm.import_dataframe(conn, load_csv(sample_csv))
    assert dbm.record_counts(conn)["total"] == 0
    statuses = [h["status"] for h in dbm.import_history(conn)]
    assert statuses == ["FAILED"]


def test_original_csv_not_modified(loaded_conn, sample_csv, tmp_path):
    before = dbm.file_sha256(sample_csv)
    dbm.import_dataframe(loaded_conn, load_csv(sample_csv))
    assert dbm.file_sha256(sample_csv) == before
