"""RFP 분석기: 시나리오(A/B/C) 판별, 평가 배점표 추출, 배점 비례 분량 계산, Go/No-Go 체크리스트."""

from __future__ import annotations

import re
from typing import Any

from app.collectors.region import extract_region_limit_from_text, is_nationwide, region_matches
from app.schemas.bids import GoNoGoItem, RfpAnalysis
from app.schemas.docs import OutlineNode, ParsedDocument, ScoringCriterion


_SCORE_NUM_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*점?")


def extract_scoring_criteria(doc: ParsedDocument) -> list[ScoringCriterion]:
    """파싱된 표에서 평가항목·배점표를 구조화 추출한다."""
    criteria: list[ScoringCriterion] = []
    for grid in doc.tables:
        if len(grid) < 2:
            continue
        header_row = " ".join(grid[0])
        if not any(k in header_row for k in ("배점", "평가", "항목", "심사")):
            continue

        # 열 인덱스 추정
        score_col = -1
        item_col = 0
        crit_col = -1
        for c_i, cell in enumerate(grid[0]):
            if any(k in cell for k in ("배점", "점수", "가중치")):
                score_col = c_i
            elif any(k in cell for k in ("기준", "세부", "내용", "평가요소")):
                crit_col = c_i
            elif any(k in cell for k in ("항목", "구분", "영역")):
                item_col = c_i

        if score_col == -1 and len(grid[0]) >= 2:
            score_col = 1
        if crit_col == -1 and len(grid[0]) >= 3:
            crit_col = 2

        for row in grid[1:]:
            if len(row) <= max(item_col, score_col):
                continue
            item_name = row[item_col].strip()
            if not item_name or any(k in item_name for k in ("합계", "총점", "소계")):
                continue
            score_raw = row[score_col].strip() if 0 <= score_col < len(row) else ""
            m = _SCORE_NUM_RE.search(score_raw)
            if not m:
                continue
            score_val = float(m.group(1))
            if score_val <= 0 or score_val > 100:
                continue
            crit_text = row[crit_col].strip() if 0 <= crit_col < len(row) else ""
            criteria.append(
                ScoringCriterion(
                    category="기술능력평가",
                    item=item_name,
                    max_score=score_val,
                    criteria=crit_text,
                )
            )
    return criteria


def detect_scenario(doc: ParsedDocument, scoring: list[ScoringCriterion]) -> str:
    """시나리오 A(RFP 목차/배점표 명시), B(목차 없음/수의계약), C(지정 HWPX 양식 슬롯)를 판별한다."""
    if doc.is_template_form or len(doc.slots) >= 2:
        return "C"
    if scoring or "제안서 작성 목차" in doc.full_text or "목차" in doc.full_text:
        return "A"
    return "B"


def build_outline_from_document(
    doc: ParsedDocument,
    scoring: list[ScoringCriterion],
    scenario: str,
    estimated_price: int = 0,
    category: str = "용역",
) -> list[OutlineNode]:
    """시나리오별 최적 목차 트리와 배점 비례 목표 글자수(target_chars)를 산출한다."""
    # 시나리오 C: 업로드된 HWPX 양식 슬롯 기반 아웃라인
    if scenario == "C" and doc.slots:
        nodes: list[OutlineNode] = []
        for idx, slot in enumerate(doc.slots, start=1):
            nodes.append(
                OutlineNode(
                    id=f"sec_{idx}",
                    number=str(idx),
                    title=slot.location_label,
                    weight_score=round(100.0 / len(doc.slots), 1),
                    target_chars=slot.max_chars_hint or 700,
                    steering_note=slot.prompt_hint,
                    slot_id=slot.slot_id,
                    children=[],
                )
            )
        return nodes

    # 시나리오 A: 배점표 비례 분량 배분
    if scenario == "A" and scoring:
        total_weight = sum(s.max_score for s in scoring) or 100.0
        total_chars_budget = 6500
        nodes = []
        for idx, s in enumerate(scoring, start=1):
            ratio = s.max_score / total_weight
            target_chars = max(500, min(2800, int(round(total_chars_budget * ratio / 50.0) * 50)))
            clean_title = re.sub(r"^\d+[\.\)]\s*", "", s.item).strip()
            nodes.append(
                OutlineNode(
                    id=f"sec_{idx}",
                    number=f"{idx}",
                    title=clean_title,
                    weight_score=s.max_score,
                    target_chars=target_chars,
                    steering_note=s.criteria,
                    slot_id=None,
                    children=[],
                )
            )
        return nodes

    # 시나리오 B: 5,000만 원 이하 수의계약 경량 템플릿 vs 일반 B2G 표준 템플릿
    if 0 < estimated_price <= 50_000_000:
        lightweight_sections = [
            ("1", "핵심 납품·과업 제안 요약 (발주처 니즈 및 해결 방안)", 30.0, 900, "핵심 제품·서비스 스펙 및 즉각 도입 효과 강조"),
            ("2", "사회적기업 우선구매·SVI 성과 증빙", 25.0, 800, "사회적기업 인증, 취약계층 고용, SVI 우수등급(120% 실적 인정) 근거 삽입"),
            ("3", "세부 실행 계획 및 납기·검수 기준", 25.0, 900, "납기 준수 일정표, 품질 검수 항목, 하자보수 체계 명시"),
            ("4", "유사 수행 실적 및 견적 합리성", 20.0, 700, "유사 공공기관 납품 실적 및 비용 효율성 강조"),
        ]
        return [
            OutlineNode(
                id=f"sec_{i}",
                number=num,
                title=title,
                weight_score=w,
                target_chars=chars,
                steering_note=note,
                children=[],
            )
            for i, (num, title, w, chars, note) in enumerate(lightweight_sections, start=1)
        ]

    standard_sections = [
        ("1", "사업 이해 및 추진 배경", 20.0, 1100, f"{category} 사업의 정책적 배경과 수요기관 핵심 과제 분석"),
        ("2", "사회적 가치 연계 전략 (SVI·고용·지역공헌)", 20.0, 1100, "취약계층 일자리·ESG 연계 및 SVI 성과지표 반영"),
        ("3", "세부 과업 수행 방안 및 방법론", 30.0, 1800, "단계별 실행 프로세스, 품질관리 및 차별화 전략"),
        ("4", "추진 일정 및 투입 인력·조직", 15.0, 900, "WBS 마일스톤 표 및 전담 인력 역할 분담"),
        ("5", "유사 수행 실적 및 사후관리(유지보수) 계획", 15.0, 900, "과거 선정 실적 인용 및 장애 대응·보고 체계"),
    ]
    return [
        OutlineNode(
            id=f"sec_{i}",
            number=num,
            title=title,
            weight_score=w,
            target_chars=chars,
            steering_note=note,
            children=[],
        )
        for i, (num, title, w, chars, note) in enumerate(standard_sections, start=1)
    ]


def build_rfp_summary_and_gonogo(
    doc: ParsedDocument,
    scoring: list[ScoringCriterion],
    profile: dict[str, Any],
    bid_meta: dict[str, Any] | None = None,
) -> RfpAnalysis:
    """파싱된 RFP와 회사 마스터 프로필을 대조해 Go/No-Go 체크리스트와 요약을 생성한다."""
    text = doc.full_text
    bid_meta = bid_meta or {}
    est_price = int(bid_meta.get("estimated_price") or 0)
    region_limit = str(bid_meta.get("region_limit") or "").strip()
    region_from_text = False
    if not region_limit or is_nationwide(region_limit):
        # 업로드 RFP는 공고 메타가 없으므로 참가자격 문장에서 지역 제한을 찾는다
        extracted = extract_region_limit_from_text(text)
        if extracted and not region_limit:
            region_limit, region_from_text = extracted, True
    region_limit = region_limit or "전국"
    is_sme = bool(bid_meta.get("is_sme_competing") or ("중소기업자간 경쟁제품" in text))

    go_nogo: list[GoNoGoItem] = []

    # 1. 사회적기업 인증 / 취약계층 고용비율
    is_se = bool(profile.get("is_certified_social"))
    vuln_ratio = float(profile.get("vulnerable_employment_ratio") or 0.0)
    go_nogo.append(
        GoNoGoItem(
            criterion="사회적기업 우선구매·수의계약 요건",
            required_value="인증/예비사회적기업 (지방계약 시 취약계층 30% 이상 권장)",
            company_value=f"{profile.get('social_type', '')} (취약계층 {vuln_ratio:.1f}%)",
            status="pass" if is_se and vuln_ratio >= 30.0 else "warn",
            note="요건 충족 시 5천만 원 1인 견적 / 1억 원 2인 이상 견적 특례 적용 가능(발주처 지침 확인 필요)",
        )
    )

    # 2. 직접생산확인증명서 (중소기업자간 경쟁제품 1,000만 원 이상)
    dp_items = profile.get("direct_production_items") or []
    if is_sme and est_price >= 10_000_000:
        go_nogo.append(
            GoNoGoItem(
                criterion="직접생산확인증명서 의무 (중기간 경쟁제품 1천만 원+)",
                required_value="해당 물품·용역 세부품명번호 직접생산확인 보유 필수",
                company_value=", ".join(dp_items) if dp_items else "등록 품목 없음",
                status="warn" if not dp_items else "check",
                note="중소기업제품 구매촉진법 제9조에 따라 사회적기업도 직접생산확인이 필수입니다.",
            )
        )

    # 3. 지역제한 확인
    comp_region = str(profile.get("region") or "")
    include_nationwide = bool(profile.get("include_nationwide", True))
    region_ok = region_matches(comp_region, region_limit)
    if region_ok is True:
        region_status, region_note = "pass", ""
    elif region_ok is False:
        region_status, region_note = "warn", "본사 소재지가 공고 지역 제한과 다릅니다."
    else:
        region_status, region_note = "check", "본사 소재지 또는 공고 지역을 시·도로 해석하지 못했습니다. 직접 확인하세요."
    if region_from_text:
        region_note = (region_note + " " if region_note else "") + "RFP 본문 참가자격 문장에서 추출한 지역입니다(원문 확인 필요)."
        if region_status == "pass":
            region_status = "check"
    go_nogo.append(
        GoNoGoItem(
            criterion="참가 가능 지역 제한",
            required_value=region_limit,
            company_value=f"{comp_region} ({'전국 포함' if include_nationwide else '지역 한정'})",
            status=region_status,
            note=region_note,
        )
    )

    # 4. 유사 실적 / 신용등급 언급 탐지
    if any(k in text for k in ("신용등급", "신용평가")):
        go_nogo.append(
            GoNoGoItem(
                criterion="신용평가등급 확인서 제출",
                required_value="RFP 내 신용평가등급 기준 확인 필요",
                company_value="보유 인증: " + ", ".join(profile.get("certifications") or []),
                status="check",
                note="공고문 내 적격심사 신용등급 기준(예: B- 이상)을 원문에서 대조하세요.",
            )
        )

    if any(k in text for k in ("유사실적", "수행실적", "납품실적")):
        projects = profile.get("past_projects") or []
        go_nogo.append(
            GoNoGoItem(
                criterion="최근 3년 유사 사업 수행 실적",
                required_value="유사 용역·납품 실적증명서 제출",
                company_value=f"등록 실적 {len(projects)}건 보유",
                status="pass" if projects else "warn",
                note=projects[0].get("title", "") if projects else "마스터 프로필에 실적을 추가하세요.",
            )
        )

    # 핵심 과업 추출
    key_tasks: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if 10 <= len(s) <= 120 and any(k in s for k in ("과업", "목적", "사업", "지원", "교육", "제작", "구축")):
            key_tasks.append(s)
        if len(key_tasks) >= 4:
            break
    if not key_tasks:
        key_tasks = [f"총 {doc.page_count}페이지 분량의 문서({doc.filename}) 파싱 완료"]

    summary_lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 15][:3]
    summary = " / ".join(summary_lines)[:360] if summary_lines else doc.filename

    return RfpAnalysis(
        summary=summary,
        key_tasks=key_tasks,
        go_nogo=go_nogo,
        scoring_criteria=[s.model_dump() for s in scoring],
        primary_file_path=doc.file_path,
    )
