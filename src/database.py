"""
SQLite 데이터베이스 생성 및 원본 데이터 적재·이력 관리
[ 처리 기준 ]
- SQL 실행: 파라미터 바인딩을 통한 안전한 값 전달
- 트랜잭션: 파일 단위 적재 및 오류 발생 시 전체 롤백
- 중복 방지: 파일 해시 및 UNIQUE(row_hash, dup_seq) 제약 활용
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
from src import data_quality as dq
from src.data_quality import LoadResult

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DB_COLUMNS = list(config.COLUMN_MAP.values())
META_COLUMNS = ["import_id", "source_row_no", "row_hash", "dup_seq",
                "contract_no_valid", "has_sci_amount"]
INSERT_SQL = (
    f"INSERT INTO payment_records ({', '.join(META_COLUMNS + DB_COLUMNS)}) "
    f"VALUES ({', '.join('?' * (len(META_COLUMNS) + len(DB_COLUMNS)))}) "
    # 동일 내용 중복만 제외하고 기타 제약 위반 시 롤백
    "ON CONFLICT (row_hash, dup_seq) DO NOTHING"
)


class DatabaseError(RuntimeError):
    """데이터베이스 처리 중 복구 불가능한 오류"""


@dataclass
class ImportResult:
    import_id: int
    status: str
    rows_read: int
    rows_inserted: int
    rows_ignored: int
    values_changed: int
    message: str


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path=config.DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise DatabaseError(f"DB 파일을 열 수 없습니다: {db_path} ({exc})") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def row_hashes(df: pd.DataFrame) -> pd.Series:
    joined = df[config.REQUIRED_COLUMNS].agg("\x1f".join, axis=1)
    return joined.map(lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest())


def prepare_records(df: pd.DataFrame):
    """DataFrame의 INSERT용 레코드 및 값 변환 내역 생성"""
    hashes = row_hashes(df)
    dup_seq = hashes.groupby(hashes).cumcount() + 1
    values, statuses = dq.numeric_frame(df)
    text_cols = [c for c in config.REQUIRED_COLUMNS if c not in config.INTEGER_COLUMNS]

    records, changes = [], []
    for pos, idx in enumerate(df.index):
        row_no = pos + 2  # CSV 헤더 1행 제외
        raw = df.loc[idx]
        has_sci = False
        stored = {}
        for col in config.REQUIRED_COLUMNS:
            if col in text_cols:
                stored[col] = raw[col] if raw[col] != "" else None
                continue
            value = values.at[idx, col]
            value = None if pd.isna(value) else int(value)
            status = statuses.at[idx, col]
            stored[col] = value
            if status == dq.SCI:
                has_sci = True
                changes.append((row_no, col, raw[col], str(value), "지수표기 → 정수 변환(정밀도 손실 가능)"))
            elif status == dq.INVALID:
                changes.append((row_no, col, raw[col], None, "숫자 형식 오류 → NULL"))
        contract_valid = 0 if dq.is_sci_notation(raw["계약납품요구번호"]) else 1
        records.append((
            row_no, hashes.at[idx], int(dup_seq.at[idx]), contract_valid, int(has_sci),
            *[stored[c] for c in config.REQUIRED_COLUMNS],
        ))
    return records, changes


def _insert_history(conn, **fields) -> int:
    cols = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    cur = conn.execute(f"INSERT INTO import_history ({cols}) VALUES ({marks})", tuple(fields.values()))
    return cur.lastrowid


def already_imported(conn, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM import_history WHERE file_hash = ? AND status = 'SUCCESS' LIMIT 1",
        (file_hash,)).fetchone()
    return row is not None


def import_dataframe(conn: sqlite3.Connection, loaded: LoadResult) -> ImportResult:
    """CSV 데이터 적재 및 중복 파일 제외·실패 시 롤백 이력 관리"""
    started = _now()
    file_hash = file_sha256(loaded.path)
    name = loaded.path.name
    n = loaded.row_count

    if already_imported(conn, file_hash):
        msg = "이미 적재된 파일입니다. 중복 적재하지 않았습니다."
        with conn:
            iid = _insert_history(conn, file_name=name, file_hash=file_hash, encoding=loaded.encoding,
                                  started_at=started, finished_at=_now(), status="SKIPPED",
                                  rows_read=n, rows_ignored=n, message=msg)
        logger.info("적재 건너뜀(동일 파일): %s", name)
        return ImportResult(iid, "SKIPPED", n, 0, n, 0, msg)

    records, changes = prepare_records(loaded.df)
    try:
        with conn:  # 오류 발생 시 자동 롤백
            iid = _insert_history(conn, file_name=name, file_hash=file_hash, encoding=loaded.encoding,
                                  started_at=started, status="SUCCESS", rows_read=n)
            before = conn.total_changes
            conn.executemany(INSERT_SQL, [(iid, *r) for r in records])
            inserted = conn.total_changes - before

            row_to_id = dict(conn.execute(
                "SELECT source_row_no, record_id FROM payment_records WHERE import_id = ?", (iid,)
            ).fetchall())
            change_rows = [(iid, row_to_id[c[0]], *c) for c in changes if c[0] in row_to_id]
            conn.executemany(
                "INSERT INTO value_changes (import_id, record_id, source_row_no, column_name, "
                "original_value, stored_value, reason) VALUES (?, ?, ?, ?, ?, ?, ?)", change_rows)

            ignored = n - inserted
            msg = f"{inserted:,}건 적재, {ignored:,}건은 이미 존재하여 제외"
            conn.execute(
                "UPDATE import_history SET finished_at = ?, rows_inserted = ?, rows_ignored = ?, "
                "values_changed = ?, message = ? WHERE import_id = ?",
                (_now(), inserted, ignored, len(change_rows), msg, iid))
    except sqlite3.Error as exc:
        logger.error("적재 실패, 롤백: %s (%s)", name, exc)
        with conn:
            iid = _insert_history(conn, file_name=name, file_hash=file_hash, encoding=loaded.encoding,
                                  started_at=started, finished_at=_now(), status="FAILED",
                                  rows_read=n, message=f"롤백됨: {exc}")
        raise DatabaseError(f"적재 중 오류가 발생해 모든 변경을 취소했습니다: {exc}") from exc

    logger.info("적재 완료: %s (%s)", name, msg)
    return ImportResult(iid, "SUCCESS", n, inserted, ignored, len(change_rows), msg)


def record_counts(conn) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM payment_records").fetchone()[0]
    clean = conn.execute("SELECT COUNT(*) FROM v_payments").fetchone()[0]
    return {"total": total, "analysis": clean, "duplicates_excluded": total - clean}


def import_history(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT import_id, file_name, status, rows_read, rows_inserted, rows_ignored, "
        "values_changed, started_at, message FROM import_history ORDER BY import_id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def value_changes(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT source_row_no AS 원본행번호, column_name AS 컬럼, original_value AS 원본값, "
        "stored_value AS 저장값, reason AS 사유 FROM value_changes ORDER BY change_id", conn)