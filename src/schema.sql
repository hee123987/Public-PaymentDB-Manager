-- Public-PaymentDB-Manager 데이터베이스 설계
-- 조달청 CSV의 원본 23개 컬럼을 저장하고, 데이터 관리와 분석에 필요한 정보를 추가한다.

-- 1. CSV 파일 적재 이력
-- 같은 파일을 중복으로 불러오지 않도록 파일 정보와 처리 결과를 기록한다.
CREATE TABLE IF NOT EXISTS import_history (
    import_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL,
    file_hash       TEXT    NOT NULL, -- 파일의 SHA-256 해시값으로 동일 파일 여부 확인
    encoding        TEXT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT,
    status          TEXT    NOT NULL CHECK (status IN ('SUCCESS', 'SKIPPED', 'FAILED')),
    rows_read       INTEGER NOT NULL DEFAULT 0,
    rows_inserted   INTEGER NOT NULL DEFAULT 0,
    rows_ignored    INTEGER NOT NULL DEFAULT 0, -- 이미 저장되어 있어 제외한 행 수
    values_changed  INTEGER NOT NULL DEFAULT 0, -- 저장 과정에서 값이 변환된 항목 수
    message         TEXT
);

-- 2. 계약 및 대금지급 데이터
-- 원본 CSV의 23개 컬럼을 저장하고, 데이터 식별과 중복 관리에 필요한 컬럼을 추가한다.
CREATE TABLE IF NOT EXISTS payment_records (
    record_id                 INTEGER PRIMARY KEY AUTOINCREMENT, -- 각 행을 구분하는 고유 번호
    import_id                 INTEGER NOT NULL REFERENCES import_history (import_id),
    source_row_no             INTEGER NOT NULL, -- 원본 CSV의 행 번호 (첫 번째 행은 컬럼명)
    row_hash                  TEXT    NOT NULL, -- 원본 23개 값의 해시값으로 동일한 행 식별
    dup_seq                   INTEGER NOT NULL DEFAULT 1 CHECK (dup_seq >= 1), -- 동일한 행이 반복된 순서

    business_type             TEXT,     -- 업무구분명
    contract_delivery_type    TEXT,     -- 계약납품구분
    procurement_type          TEXT,     -- 조달구분
    payment_no                TEXT NOT NULL,  -- 대금지급번호 (중복값으로 기본키 사용 불가)
    payment_process_type      TEXT,     -- 대금지급처리구분
    contract_no               TEXT,     -- 계약납품요구번호
    contract_amount           INTEGER,  -- 계약금액
    contract_name             TEXT,     -- 계약건명
    company_name              TEXT,     -- 업체명
    representative_name       TEXT,     -- 대표자명 (외부 공개 시 이름 마스킹)
    agency_name               TEXT,     -- 기관명
    agency_department         TEXT,     -- 기관담당부서명
    agency_manager_name       TEXT,     -- 기관담당자명 (외부 공개 시 이름 마스킹)
    expense_type              TEXT,     -- 지출구분명
    delay_days                INTEGER,  -- 지체일수
    expense_amount            INTEGER,  -- 지출금액
    request_amount            INTEGER,  -- 지급요청금액
    decided_amount            INTEGER,  -- 지급결정금액
    deduction_amount          INTEGER,  -- 차감금액
    advance_amount            INTEGER,  -- 선급금금액
    advance_settlement_amount INTEGER,  -- 선급금청산금액
    delay_penalty_amount      INTEGER,  -- 지체상금금액
    input_date                TEXT,     -- 입력일자(YYYY-MM-DD)

    contract_no_valid         INTEGER NOT NULL DEFAULT 1 CHECK (contract_no_valid IN (0, 1)), -- 계약번호가 손상된 경우 0
    has_sci_amount            INTEGER NOT NULL DEFAULT 0 CHECK (has_sci_amount IN (0, 1)), -- 지수표기 금액이 포함된 경우 1

    UNIQUE (row_hash, dup_seq) -- 동일한 데이터의 중복 저장 방지
);

-- 3. 데이터 변환 이력
-- 원본 값을 변환한 경우 변경 전후의 값과 이유를 기록해 확인할 수 있도록 한다.
CREATE TABLE IF NOT EXISTS value_changes (
    change_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id      INTEGER NOT NULL REFERENCES import_history (import_id),
    record_id      INTEGER NOT NULL REFERENCES payment_records (record_id),
    source_row_no  INTEGER NOT NULL,
    column_name    TEXT    NOT NULL, -- 변경된 값의 컬럼명
    original_value TEXT    NOT NULL,
    stored_value   TEXT,
    reason         TEXT    NOT NULL
);

-- 4. 검색 성능을 위한 인덱스
-- 기관명, 날짜, 계약번호 등 자주 조회하는 컬럼에 인덱스를 생성한다.
CREATE INDEX IF NOT EXISTS idx_pr_agency      ON payment_records (agency_name);
CREATE INDEX IF NOT EXISTS idx_pr_input_date  ON payment_records (input_date);
CREATE INDEX IF NOT EXISTS idx_pr_contract_no ON payment_records (contract_no);
CREATE INDEX IF NOT EXISTS idx_pr_payment_no  ON payment_records (payment_no);
CREATE INDEX IF NOT EXISTS idx_pr_expense     ON payment_records (expense_type);
CREATE INDEX IF NOT EXISTS idx_pr_business    ON payment_records (business_type);
CREATE INDEX IF NOT EXISTS idx_pr_contract_amt ON payment_records (contract_amount);
CREATE INDEX IF NOT EXISTS idx_import_hash    ON import_history (file_hash, status);

-- 5. 중복을 제외한 지급 데이터 분석
-- 원본 데이터는 그대로 보관하고, 분석할 때만 완전히 중복된 행을 제외한다.
CREATE VIEW IF NOT EXISTS v_payments AS
SELECT * FROM payment_records WHERE dup_seq = 1;

-- 6. 계약별 지급 현황 분석
-- 같은 계약번호의 지급 내역을 묶어 계약별 금액과 건수를 집계한다.
-- 계약번호가 손상된 경우 다른 계약과 잘못 합쳐지지 않도록 개별 행으로 처리한다.
CREATE VIEW IF NOT EXISTS v_contracts AS
SELECT
    CASE WHEN contract_no_valid = 1 THEN contract_no ELSE 'ROW-' || record_id END AS contract_key,
    MAX(contract_no_valid)  AS contract_no_valid,
    MAX(agency_name)        AS agency_name,
    MAX(business_type)      AS business_type,
    MAX(contract_name)      AS contract_name,
    MAX(contract_amount)    AS contract_amount, -- 같은 계약에 금액이 여러 개면 최댓값 사용
    COUNT(*)                AS record_count,
    SUM(decided_amount)     AS decided_total, -- 값이 없는 항목은 제외하고 지급결정금액 합산
    COUNT(decided_amount)   AS decided_count
FROM v_payments
GROUP BY contract_key;