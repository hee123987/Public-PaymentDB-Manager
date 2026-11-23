"""프로젝트 경로 및 데이터 컬럼 설정"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE_DIR = BASE_DIR / "database"
RESULTS_DIR = BASE_DIR / "results"
LOG_DIR = BASE_DIR / "logs"

DEFAULT_DB_PATH = DATABASE_DIR / "payment.db"
DEFAULT_CSV_NAME = "조달청_대금종결집계내역_계약_20240802 (1).csv"

SOURCE_INFO = {
    "provider": "조달청",
    "dataset": "조달청_대금종결집계내역_계약",
    "portal": "공공데이터포털",
    "url": "https://www.data.go.kr/data/15131015/fileData.do",
}

ENCODINGS = ("utf-8-sig", "cp949")

# 원본 컬럼명 → DB 컬럼명 (원본 순서 유지)
COLUMN_MAP = {
    "업무구분명": "business_type",
    "계약납품구분": "contract_delivery_type",
    "조달구분": "procurement_type",
    "대금지급번호": "payment_no",
    "대금지급처리구분": "payment_process_type",
    "계약납품요구번호": "contract_no",
    "계약금액": "contract_amount",
    "계약건명": "contract_name",
    "업체명": "company_name",
    "대표자명": "representative_name",
    "기관명": "agency_name",
    "기관담당부서명": "agency_department",
    "기관담당자명": "agency_manager_name",
    "지출구분명": "expense_type",
    "지체일수": "delay_days",
    "지출금액": "expense_amount",
    "지급요청금액": "request_amount",
    "지급결정금액": "decided_amount",
    "차감금액": "deduction_amount",
    "선급금금액": "advance_amount",
    "선급금청산금액": "advance_settlement_amount",
    "지체상금금액": "delay_penalty_amount",
    "입력일자": "input_date",
}
REQUIRED_COLUMNS = list(COLUMN_MAP)

AMOUNT_COLUMNS = [
    "계약금액", "지출금액", "지급요청금액", "지급결정금액",
    "차감금액", "선급금금액", "선급금청산금액", "지체상금금액",
]
INTEGER_COLUMNS = AMOUNT_COLUMNS + ["지체일수"]
ID_COLUMNS = ["대금지급번호", "계약납품요구번호"]
DATE_COLUMN = "입력일자"

# 공개 산출물(보고서·내보내기·로그)에서 마스킹할 개인정보성 컬럼
PERSONAL_COLUMNS = ["대표자명", "기관담당자명"]