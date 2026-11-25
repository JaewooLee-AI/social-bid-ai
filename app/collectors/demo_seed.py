"""2026년 사회적기업 맞춤형 데모 공고 12건 시드 및 승인 시 샘플 RFP HWPX 생성."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import get_downloads_dir
from app.documents.hwpx_engine import create_sample_hwpx_template


def build_demo_bids() -> list[dict[str, Any]]:
    now = datetime.now()

    def dt_str(days_offset: int) -> str:
        return (now + timedelta(days=days_offset)).strftime("%Y-%m-%d 18:00")

    def pub_str(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%d 09:00")

    return [
        {
            "source": "g2b",
            "bid_no": "20261118001",
            "bid_ord": "00",
            "title": "성동구 취약계층 어르신 디지털 돌봄 매니저 운영 용역",
            "agency": "서울특별시 성동구",
            "category": "용역",
            "contract_method": "수의계약(사회적기업 우선구매)",
            "estimated_price": 48_000_000,
            "deadline": dt_str(4),
            "published_at": pub_str(2),
            "region_limit": "서울특별시",
            "license_limit": "사회적기업(1469)",
            "is_sme_competing": False,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "성동구_디지털돌봄_제안요청서.hwpx", "url": "demo://rfp"}],
        },
        {
            "source": "g2b",
            "bid_no": "20261119014",
            "bid_ord": "00",
            "title": "공공기관 ESG 친환경 웰컴 키트 및 홍보물 제작 납품 (중소기업자간 경쟁제품)",
            "agency": "한국환경산업기술원",
            "category": "물품",
            "contract_method": "제한경쟁(우선구매 가점)",
            "estimated_price": 86_000_000,
            "deadline": dt_str(6),
            "published_at": pub_str(1),
            "region_limit": "전국",
            "license_limit": "인쇄물 제작(1468)",
            "is_sme_competing": True,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "ESG_홍보물_규격서.hwpx", "url": "demo://form"}],
        },
        {
            "source": "g2b",
            "bid_no": "20261120007",
            "bid_ord": "00",
            "title": "경력보유여성 디지털 실무 교육훈련 위탁서비스",
            "agency": "서울특별시여성능력개발원",
            "category": "용역",
            "contract_method": "수의(소액)-1인견적",
            "estimated_price": 44_500_000,
            "deadline": dt_str(3),
            "published_at": pub_str(1),
            "region_limit": "서울특별시",
            "license_limit": "교육서비스(5312)",
            "is_sme_competing": False,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "교육훈련_과업지시서.hwpx", "url": "demo://rfp"}],
        },
        {
            "source": "g2b",
            "bid_no": "20261117029",
            "bid_ord": "00",
            "title": "사회적경제 온라인 판로지원 포털 유지보수 및 기능 고도화",
            "agency": "서울특별시 사회적경제지원센터",
            "category": "용역",
            "contract_method": "2인 이상 견적 수의계약",
            "estimated_price": 92_000_000,
            "deadline": dt_str(8),
            "published_at": pub_str(3),
            "region_limit": "전국",
            "license_limit": "소프트웨어사업자(1468)",
            "is_sme_competing": True,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "포털고도화_RFP.hwpx", "url": "demo://rfp"}],
        },
        {
            "source": "g2b",
            "bid_no": "20261115088",
            "bid_ord": "00",
            "title": "관내 초등학교 친환경 급식용 생분해 식판 세척·배송 용역",
            "agency": "경기도교육청",
            "category": "용역",
            "contract_method": "제한경쟁",
            "estimated_price": 145_000_000,
            "deadline": dt_str(10),
            "published_at": pub_str(5),
            "region_limit": "경기도",
            "license_limit": "",
            "is_sme_competing": False,
            "url": "https://www.g2b.go.kr",
            "attachments": [],
        },
        {
            "source": "g2b",
            "bid_no": "20261121003",
            "bid_ord": "00",
            "title": "노인복지관 스마트 치매예방 콘텐츠 키트 보급 사업",
            "agency": "보건복지부 노인인력개발원",
            "category": "물품",
            "contract_method": "협상에 의한 계약",
            "estimated_price": 78_000_000,
            "deadline": dt_str(5),
            "published_at": pub_str(1),
            "region_limit": "전국",
            "license_limit": "",
            "is_sme_competing": False,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "치매예방_콘텐츠_제안요청서.hwpx", "url": "demo://rfp"}],
        },
        {
            "source": "seis",
            "bid_no": "SEIS-2026-041",
            "bid_ord": "00",
            "title": "2026년 사회적기업 사업개발비 지원사업 (지정 양식 패치 제출)",
            "agency": "한국사회적기업진흥원",
            "category": "지원사업",
            "contract_method": "공모·심사",
            "estimated_price": 50_000_000,
            "deadline": dt_str(7),
            "published_at": pub_str(2),
            "region_limit": "전국",
            "license_limit": "(예비)사회적기업",
            "is_sme_competing": False,
            "url": "https://www.socialenterprise.or.kr",
            "attachments": [{"filename": "사업개발비_신청서_지정양식.hwpx", "url": "demo://form"}],
        },
        {
            "source": "seis",
            "bid_no": "SEIS-2026-052",
            "bid_ord": "00",
            "title": "2026년 사회적가치지표(SVI) 측정 및 우수기업 판로 연계 지원",
            "agency": "한국사회적기업진흥원",
            "category": "컨설팅·판로",
            "contract_method": "공모",
            "estimated_price": 30_000_000,
            "deadline": dt_str(12),
            "published_at": pub_str(3),
            "region_limit": "전국",
            "license_limit": "사회적경제기업",
            "is_sme_competing": False,
            "url": "https://www.socialenterprise.or.kr",
            "attachments": [{"filename": "SVI_측정신청_양식.hwpx", "url": "demo://form"}],
        },
        {
            "source": "seis",
            "bid_no": "SEIS-2026-063",
            "bid_ord": "00",
            "title": "2026년 사회적기업 일자리창출사업 전문인력 지원 공고",
            "agency": "고용노동부·한국사회적기업진흥원",
            "category": "인력지원",
            "contract_method": "공모",
            "estimated_price": 42_000_000,
            "deadline": dt_str(9),
            "published_at": pub_str(4),
            "region_limit": "서울특별시",
            "license_limit": "인증·예비사회적기업",
            "is_sme_competing": False,
            "url": "https://www.socialenterprise.or.kr",
            "attachments": [],
        },
        {
            "source": "bizinfo",
            "bid_no": "BIZ-2026-8812",
            "bid_ord": "00",
            "title": "[서울] 2026년 소셜벤처·사회적경제 ESG 스케일업 바우처 지원사업",
            "agency": "중소벤처기업부·서울경제진흥원",
            "category": "창업·경영",
            "contract_method": "정부지원사업 공모",
            "estimated_price": 45_000_000,
            "deadline": dt_str(5),
            "published_at": pub_str(1),
            "region_limit": "서울특별시",
            "license_limit": "사회적기업·소셜벤처",
            "is_sme_competing": False,
            "url": "https://www.bizinfo.go.kr",
            "attachments": [{"filename": "ESG_스케일업_사업계획서_양식.hwpx", "url": "demo://form"}],
        },
        {
            "source": "bizinfo",
            "bid_no": "BIZ-2026-8904",
            "bid_ord": "00",
            "title": "[전국] 2026년 초기창업패키지 소셜임팩트 트랙 참여기업 모집 (PSST)",
            "agency": "창업진흥원",
            "category": "창업",
            "contract_method": "PSST 서면·발표평가",
            "estimated_price": 95_000_000,
            "deadline": dt_str(14),
            "published_at": pub_str(2),
            "region_limit": "전국",
            "license_limit": "창업 3년 이내 기업",
            "is_sme_competing": False,
            "url": "https://www.bizinfo.go.kr",
            "attachments": [{"filename": "초기창업패키지_PSST_양식.hwpx", "url": "demo://form"}],
        },
        {
            "source": "g2b",
            "bid_no": "20261121099",
            "bid_ord": "00",
            "title": "공공청사 LED 조명기구 교체 납품 (직접생산확인 의무)",
            "agency": "조달청 서울지방조달청",
            "category": "물품",
            "contract_method": "소액수의(1인견적)",
            "estimated_price": 32_000_000,
            "deadline": dt_str(2),
            "published_at": pub_str(1),
            "region_limit": "서울특별시",
            "license_limit": "",
            "is_sme_competing": True,
            "url": "https://www.g2b.go.kr",
            "attachments": [{"filename": "LED_물품규격서.hwpx", "url": "demo://rfp"}],
        },
    ]


def materialize_demo_attachments(bid_no: str, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """데모 공고 승인 시 온디맨드로 실제 유효한 .hwpx 파일을 downloads/<bid_no>/에 생성한다."""
    target_dir = get_downloads_dir() / bid_no
    target_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    if not attachments:
        attachments = [{"filename": f"{bid_no}_공고_과업지시서.hwpx", "url": "demo://rfp"}]

    for att in attachments:
        fname = Path(str(att.get("filename") or f"{bid_no}.hwpx")).name
        if not fname.endswith(".hwpx"):
            fname = f"{Path(fname).stem}.hwpx"
        local_path = target_dir / fname
        kind = "form" if "form" in str(att.get("url", "")) else "rfp"
        create_sample_hwpx_template(local_path, kind=kind)
        out.append(
            {
                "filename": fname,
                "url": str(att.get("url") or "demo://local"),
                "local_path": str(local_path),
                "size_bytes": local_path.stat().st_size,
                "file_type": "hwpx",
            }
        )
    return out
