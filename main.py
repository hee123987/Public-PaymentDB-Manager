"""조달청 계약·대금지급 데이터 관리 및 분석 CLI"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

import config
from src import analysis, data_quality
from src import database as db
from src.data_quality import DataLoadError, find_default_csv, load_csv
from src.report import generate_report

MENU = """
========================================
 Public Payment DB Manager
========================================
1. CSV 데이터 검증
2. 데이터베이스 생성 및 적재
3. 계약·지급 내역 조회
4. SQL 데이터 분석
5. 분석 그래프 생성
6. HTML 보고서 생성
7. 전체 분석 실행
0. 종료
========================================"""

PAGE_SIZE = 20


def setup_logging(log_dir=config.LOG_DIR) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(Path(log_dir) / "app.log", encoding="utf-8")],
    )


def show_df(df: pd.DataFrame, out) -> None:
    if df.empty:
        out("  (결과 없음)")
        return
    def whole(col):  # 정수 또는 정수값만 가진 실수 컬럼(NULL 포함 합계 등)
        s = df[col]
        return pd.api.types.is_integer_dtype(s) or (
            pd.api.types.is_float_dtype(s) and (s.dropna() % 1 == 0).all())

    text = df.to_string(index=False, na_rep="-",
                        formatters={c: (lambda v: "-" if pd.isna(v) else f"{int(v):,}")
                                    for c in df.columns if whole(c)})
    out(text)


class App:
    def __init__(self, conn, csv_path=None, results_dir=config.RESULTS_DIR,
                 input_fn=input, output_fn=print):
        self.conn = conn
        self.csv_path = Path(csv_path) if csv_path else None
        self.results_dir = Path(results_dir)
        self.ask = lambda prompt: input_fn(prompt).strip()
        self.out = output_fn
        self.loaded = None
        self.quality = None
        self.results = None
        self.charts = None

    # ---------------------------------------------------------- 공통
    def _load(self):
        if self.loaded is not None:
            return self.loaded
        path = self.csv_path or find_default_csv()
        if path is None:
            raise DataLoadError("CSV 파일을 찾을 수 없습니다. data 폴더에 원본 CSV를 넣거나 --csv로 지정하세요.")
        self.out(f"CSV 불러오는 중: {path}")
        self.loaded = load_csv(path)
        self.out(f"  인코딩 {self.loaded.encoding} · {self.loaded.row_count:,}건 · "
                 f"{len(self.loaded.df.columns)}개 컬럼")
        return self.loaded

    def _require_data(self) -> bool:
        if db.record_counts(self.conn)["total"] == 0:
            self.out("DB에 데이터가 없습니다. 먼저 2번(데이터베이스 생성 및 적재)을 실행하세요.")
            return False
        return True

    # ---------------------------------------------------------- 1. 검증
    def validate(self):
        loaded = self._load()
        self.out("컬럼: " + ", ".join(loaded.df.columns))
        self.quality = data_quality.validate(loaded.df)
        paths = self.quality.save(self.results_dir)
        flagged = self.quality.checks[self.quality.checks["판정"].isin(["오류", "주의"])
                                      & (self.quality.checks["건수"] > 0)]
        self.out(f"\n[품질 검사] 기간 {self.quality.date_range[0]} ~ {self.quality.date_range[1]}")
        show_df(flagged[["검사항목", "건수", "판정"]], self.out)
        top_missing = self.quality.column_stats.sort_values("결측건수", ascending=False).head(8)
        self.out("\n[결측 상위 컬럼]")
        show_df(top_missing[["컬럼", "결측건수", "결측률(%)"]], self.out)
        self.out("\n저장: " + ", ".join(p.name for p in paths))

    # ---------------------------------------------------------- 2. 적재
    def load_db(self):
        loaded = self._load()
        if self.quality is None:
            self.quality = data_quality.validate(loaded.df)
            self.quality.save(self.results_dir)
        result = db.import_dataframe(self.conn, loaded)
        counts = db.record_counts(self.conn)
        label = {"SUCCESS": "적재 완료", "SKIPPED": "적재 생략"}[result.status]
        self.out(f"[{label}] {result.message}")
        self.out(f"  읽은 행 {result.rows_read:,} · 새로 적재 {result.rows_inserted:,} · "
                 f"제외 {result.rows_ignored:,} · 값 변환 {result.values_changed:,}")
        self.out(f"  DB 전체 {counts['total']:,}건 · 분석 대상 {counts['analysis']:,}건 "
                 f"(완전 중복 {counts['duplicates_excluded']:,}건 제외)")

    # ---------------------------------------------------------- 3. 조회
    def search(self):
        if not self._require_data():
            return
        self.out("검색 조건을 입력하세요. (생략하려면 Enter)")
        filters = {
            "agency": self.ask("기관명 포함 문자열: ") or None,
            "contract_name": self.ask("계약건명 포함 문자열: ") or None,
            "start_date": self.ask("입력일자 시작 (YYYY-MM-DD): ") or None,
            "end_date": self.ask("입력일자 종료 (YYYY-MM-DD): ") or None,
            "min_amount": self.ask("최소 계약금액(원): ") or None,
            "max_amount": self.ask("최대 계약금액(원): ") or None,
        }
        self.out("정렬 기준: " + " / ".join(f"{i}. {k}" for i, k in enumerate(analysis.SORT_COLUMNS, 1)))
        sort_keys = list(analysis.SORT_COLUMNS)
        choice = self.ask("정렬 번호 (기본 1): ") or "1"
        if not choice.isdigit() or not 1 <= int(choice) <= len(sort_keys):
            self.out("[입력 오류] 정렬 번호를 확인하세요. 입력일자로 정렬합니다.")
            choice = "1"
        order_by = sort_keys[int(choice) - 1]
        descending = self.ask("내림차순? (y/N): ").lower() == "y"

        total = analysis.count_records(self.conn, **filters)
        self.out(f"\n검색 결과 {total:,}건")
        if total == 0:
            return
        page = 0
        pages = (total - 1) // PAGE_SIZE + 1
        while True:
            df = analysis.search_records(self.conn, **filters, order_by=order_by, descending=descending,
                                        limit=PAGE_SIZE, offset=page * PAGE_SIZE)
            show_df(df.drop(columns=["대금지급번호", "계약납품요구번호"]), self.out)
            self.out(f"-- {page + 1}/{pages} 페이지 --")
            cmd = self.ask("n: 다음  p: 이전  s: CSV 저장  q: 종료 > ").lower()
            if cmd == "n" and page + 1 < pages:
                page += 1
            elif cmd == "p" and page > 0:
                page -= 1
            elif cmd == "s":
                full = analysis.search_records(self.conn, **filters, order_by=order_by,
                                              descending=descending, limit=None)
                path = self.results_dir / "search_result.csv"
                path.parent.mkdir(parents=True, exist_ok=True)
                full.to_csv(path, index=False, encoding="utf-8-sig")
                self.out(f"저장 완료: {path} ({len(full):,}건)")
            elif cmd in ("q", ""):
                return

    # ---------------------------------------------------------- 4. 분석
    def analyze(self, verbose=True):
        if not self._require_data():
            return False
        self.results = analysis.run_aggregations(self.conn)
        paths = analysis.save_aggregations(self.results, self.results_dir)
        if verbose:
            o = self.results["overview"]
            self.out(f"[1] 분석 대상 레코드 {o['analysis_records']:,}건 (DB 저장 {o['stored_records']:,}건), "
                     f"계약 단위 {o['contract_units']:,}건, 기관 {o['agencies']:,}곳")
            self.out(f"    지급결정금액 합계 {o['decided_sum']:,}원 "
                     f"(값 있음 {o['decided_count']:,}건 / 결측 {o['decided_missing']:,}건)")
            sections = [("[2] 기관별 레코드 건수 상위 10", "records_by_agency"),
                        ("[3] 기관별 계약금액 상위 10 (계약 단위)", "contract_amount_by_agency"),
                        ("[4] 월별 지급결정금액", "monthly_decided"),
                        ("[5] 지출구분별 지급 현황", "by_expense_type"),
                        ("[6] 업무구분별 현황", "by_business_type"),
                        ("[7] 계약금액 대비 지급결정금액(계약 단위)", "contract_vs_decided"),
                        ("[8] 지급결정금액 상위 기관", "top_agencies_by_decided")]
            for title, key in sections:
                self.out(f"\n{title}")
                show_df(self.results[key], self.out)
        self.out(f"\nSQL 집계 결과 {len(paths)}개를 저장했습니다: {self.results_dir / 'sql'}")
        return True

    # ---------------------------------------------------------- 5·6
    def charts_step(self):
        if self.results is None and not self.analyze(verbose=False):
            return False
        self.charts = analysis.create_charts(self.conn, self.results, self.results_dir)
        for p in self.charts:
            self.out(f"그래프 저장: {p}")
        return True

    def report(self):
        if self.charts is None and not self.charts_step():
            return
        if self.quality is None:
            try:
                self.quality = data_quality.validate(self._load().df)
            except DataLoadError as exc:
                self.out(f"[안내] 원본 CSV를 읽지 못해 품질 검사 결과 없이 보고서를 만듭니다. ({exc})")
        path = generate_report(self.results, self.charts, self.quality,
                               db.value_changes(self.conn), db.import_history(self.conn),
                               self.results_dir / "report.html")
        self.out(f"보고서 저장: {path}")

    def run_all(self):
        for title, step in [("1. CSV 데이터 검증", self.validate), ("2. DB 적재", self.load_db),
                            ("4. SQL 분석", lambda: self.analyze(verbose=False)),
                            ("5. 그래프", self.charts_step), ("6. 보고서", self.report)]:
            self.out(f"\n===== {title} =====")
            step()
        self.out("\n전체 분석을 완료했습니다.")

    # ---------------------------------------------------------- 메뉴
    def run_menu(self):
        handlers = {"1": self.validate, "2": self.load_db, "3": self.search, "4": self.analyze,
                    "5": self.charts_step, "6": self.report, "7": self.run_all}
        while True:
            self.out(MENU)
            try:
                choice = self.ask("메뉴를 선택하세요: ")
            except (EOFError, KeyboardInterrupt):
                self.out("\n종료합니다.")
                return
            if choice == "0":
                self.out("종료합니다.")
                return
            handler = handlers.get(choice)
            if handler is None:
                self.out("[입력 오류] 0~7 사이의 메뉴 번호를 입력하세요.")
                continue
            try:
                handler()
            except (DataLoadError, analysis.QueryError) as exc:
                self.out(f"[오류] {exc}")
            except (db.DatabaseError, sqlite3.Error) as exc:
                logging.getLogger(__name__).exception("DB 오류")
                self.out(f"[DB 오류] {exc}")
            except OSError as exc:
                self.out(f"[파일 오류] {exc}")
            except (EOFError, KeyboardInterrupt):
                self.out("\n종료합니다.")
                return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="조달청 계약·대금지급 데이터 DB 관리 및 분석")
    parser.add_argument("--csv", help="원본 CSV 경로 (기본: data 폴더의 CSV)")
    parser.add_argument("--db", default=str(config.DEFAULT_DB_PATH), help="SQLite DB 파일 경로")
    parser.add_argument("--results", default=str(config.RESULTS_DIR), help="결과 저장 폴더")
    parser.add_argument("--all", action="store_true", help="메뉴 없이 전체 분석을 실행")
    args = parser.parse_args(argv)

    setup_logging()
    try:
        conn = db.connect(args.db)
    except db.DatabaseError as exc:
        print(f"[DB 오류] {exc}")
        return 1
    app = App(conn, csv_path=args.csv, results_dir=args.results)
    try:
        if args.all:
            try:
                app.run_all()
            except (DataLoadError, db.DatabaseError) as exc:
                print(f"[오류] {exc}")
                return 1
        else:
            app.run_menu()
    finally:
        conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())