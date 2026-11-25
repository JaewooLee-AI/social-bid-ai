"""Pain Point·수익창출 중심 소셜 사업계획서 엔진: 단위 경제성 계산기, SVI 14개 지표, 레드플래그 5종 자동 점검."""

from __future__ import annotations

import asyncio
import math
import re
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.ai.llm_client import is_simulation_active, stream_text
from app.ai.rag_store import hybrid_search
from app.config import load_profile, load_prompt, load_svi_rubric
from app.schemas.bizplan import (
    AuditReport,
    RedFlagItem,
    SocialBMCanvas,
    SviAssessmentResult,
    SviIndicatorScore,
    UnitEconomicsInput,
    UnitEconomicsResult,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def calculate_unit_economics(inp: UnitEconomicsInput) -> UnitEconomicsResult:
    """객단가 × 목표 고객 수 × 전환율 = 예상 매출 및 고정비·변동비 기반 BEP를 자동 산출한다."""
    paying = max(0, int(round(inp.target_customers_monthly * (inp.conversion_rate_pct / 100.0))))
    monthly_rev = paying * max(0, inp.unit_price)
    annual_rev = monthly_rev * 12
    cm_unit = inp.unit_price - inp.variable_cost_per_unit
    cm_ratio = (cm_unit / inp.unit_price * 100.0) if inp.unit_price > 0 else 0.0
    monthly_gross = paying * cm_unit
    monthly_op = monthly_gross - inp.fixed_cost_monthly

    if cm_unit > 0:
        bep_customers: int | None = math.ceil(inp.fixed_cost_monthly / cm_unit)
        bep_revenue: int | None = bep_customers * inp.unit_price
    else:
        bep_customers = None
        bep_revenue = None

    formula_text = (
        f"월 유료고객 {paying:,}명({inp.target_customers_monthly:,}명 × {inp.conversion_rate_pct:.1f}%) "
        f"× 객단가 {inp.unit_price:,}원 = 월 매출 {monthly_rev:,}원 (연 {annual_rev:,}원) | "
        + (
            f"BEP {bep_customers:,}명 (월 매출 {bep_revenue:,}원)"
            if bep_customers is not None and bep_revenue is not None
            else "단위 공헌이익이 0 이하로 단가/원가 조정 필요"
        )
    )

    bep_str = f"{bep_customers:,}건 ({bep_revenue:,}원/월)" if bep_customers and bep_revenue else "산출 불가(공헌이익 적자)"
    markdown_table = (
        "| 재무 산출 항목 | 산출 공식 및 근거 | 산출 결과 |\n"
        "| :--- | :--- | :--- |\n"
        f"| 월 유료 결제 고객 수 | 잠재고객 {inp.target_customers_monthly:,}명 × 전환율 {inp.conversion_rate_pct:.1f}% | {paying:,}건/월 |\n"
        f"| 예상 월 매출 / 연 매출 | 유료고객 {paying:,}건 × 객단가 {inp.unit_price:,}원 (×12개월) | {monthly_rev:,}원 / 연 {annual_rev:,}원 |\n"
        f"| 단위당 공헌이익(률) | 객단가 {inp.unit_price:,}원 - 변동비 {inp.variable_cost_per_unit:,}원 | {cm_unit:,}원 ({cm_ratio:.1f}%) |\n"
        f"| 월 영업이익 (추정) | 월 공헌이익 {monthly_gross:,}원 - 월 고정비 {inp.fixed_cost_monthly:,}원 | {monthly_op:,}원 |\n"
        f"| 손익분기점 (BEP) | 월 고정비 {inp.fixed_cost_monthly:,}원 ÷ 단위 공헌이익 {cm_unit:,}원 | {bep_str} |\n"
    )

    return UnitEconomicsResult(
        paying_customers_monthly=paying,
        monthly_revenue=monthly_rev,
        annual_revenue=annual_rev,
        contribution_margin_per_unit=cm_unit,
        contribution_margin_ratio_pct=round(cm_ratio, 1),
        monthly_gross_profit=monthly_gross,
        monthly_operating_profit=monthly_op,
        bep_customers=bep_customers,
        bep_revenue=bep_revenue,
        formula_text=formula_text,
        markdown_table=markdown_table,
    )


def evaluate_svi(scores: dict[str, float]) -> SviAssessmentResult:
    """외부 svi_rubric.json 기준 14개 지표를 합산해 5단계 등급과 75점 이상 120% 우선구매 인정 여부를 판정한다."""
    rubric = load_svi_rubric()
    indicators_out: list[SviIndicatorScore] = []
    domain_sums = {"social": 0.0, "economic": 0.0, "innovation": 0.0}

    for ind in rubric.get("indicators", []):
        ind_id = str(ind["id"])
        max_s = float(ind["max_score"])
        raw_val = scores.get(ind_id)
        val = max(0.0, min(max_s, float(raw_val))) if raw_val is not None else round(max_s * 0.7, 1)
        domain = str(ind["domain"])
        domain_sums[domain] = domain_sums.get(domain, 0.0) + val
        indicators_out.append(
            SviIndicatorScore(
                id=ind_id,
                no=int(ind["no"]),
                domain=domain,
                domain_label=str(ind["domain_label"]),
                name=str(ind["name"]),
                max_score=max_s,
                score=round(val, 1),
                type=str(ind["type"]),
            )
        )

    total = round(sum(domain_sums.values()), 1)
    grade_match = rubric["grades"][-1]
    for g in rubric["grades"]:
        if float(g["min_score"]) <= total <= float(g["max_score"]) + 0.01:
            grade_match = g
            break

    return SviAssessmentResult(
        total_score=total,
        social_score=round(domain_sums["social"], 1),
        economic_score=round(domain_sums["economic"], 1),
        innovation_score=round(domain_sums["innovation"], 1),
        grade_key=str(grade_match["key"]),
        grade_label=str(grade_match["label"]),
        priority_purchase_120=total >= 75.0,
        grade_summary=str(grade_match["summary"]),
        indicators=indicators_out,
    )


def get_track_sections(output_track: str) -> list[dict[str, str]]:
    """출력 트랙별 사업계획서 섹션 구조를 반환한다."""
    if output_track == "pre_social":
        rubric = load_svi_rubric()
        areas = rubric.get("pre_social_8_areas", [])
        return [{"key": f"area_{i}", "title": a} for i, a in enumerate(areas, start=1)]

    return [
        {"key": "problem", "title": "1. P — Problem (사회문제·Pain Point 및 이중 고객 정의)"},
        {"key": "solution", "title": "2. S — Solution (문제해결 메커니즘 및 경쟁력)"},
        {"key": "scaleup", "title": "3. S — Scale-up (내재형 수익모델 · 단위 경제성 · 판로 확장)"},
        {"key": "team", "title": "4. T — Team (팀 역량 · 민주적 의사결정 · SVI 14개 지표 로드맵)"},
    ]


def _build_simulation_bizplan_section(
    title: str,
    section_key: str,
    section_title: str,
    canvas: SocialBMCanvas,
    ue: UnitEconomicsResult,
    svi: SviAssessmentResult,
    profile: dict[str, Any],
    rag_hits: list[dict[str, Any]],
) -> str:
    company = profile.get("name") or "당사"
    rag_snippet = rag_hits[0]["content"][:160].replace("\n", " ") if rag_hits else profile.get("summary", "")

    if section_key in {"problem", "area_1", "area_2", "area_3"}:
        return (
            f"### {section_title}\n\n"
            f"- **핵심 Pain Point**: {canvas.problem_statement or '현장 취약계층 및 공공 수요처의 미충족 서비스 공백 해소 필요'}\n"
            f"- **이중 고객 구조 분리**:\n"
            f"  - **수혜자(Beneficiary)**: {canvas.beneficiary or '취약계층 근로자 및 지역사회 돌봄 대상자'}\n"
            f"  - **실제 결제자(Payer)**: {canvas.payer or '지자체·공공기관 구매담당자 및 기업 ESG 담당자'} "
            f"({'수혜자=결제자 동일' if canvas.beneficiary_equals_payer else '수혜자-결제자 분리형 B2G/B2B 구조'})\n"
            f"- **기존 대안의 한계**: {canvas.existing_alternatives_limit or '기존 일반 영리업체는 단가 중심 운영으로 취약계층 고용 및 사후관리 지속성이 낮음'}\n"
            f"- **목표 시장 규모 (TAM-SAM-SOM)**:\n"
            f"  - TAM: {canvas.tam_desc or '국내 공공 우선구매 및 사회서비스 전체 시장 (출처: 기획재정부·고용노동부 공시)'}\n"
            f"  - SAM: {canvas.sam_desc or '수도권 지자체·공공기관 5,000만 원 이하 수의계약 진입 가능 영역'}\n"
            f"  - SOM: {canvas.som_desc or '초기 1차년도 직접 타겟 30개 기관 (연간 계약 규모 산출 근거 하단 표 참조)'}\n"
            f"- **객관적 근거**: {canvas.evidence_sources or rag_snippet}\n"
        )

    if section_key in {"solution", "area_4"}:
        return (
            f"### {section_title}\n\n"
            f"- **문제해결 메커니즘**: {canvas.solution_mechanism or '취약계층 직무 표준화 매뉴얼 + 디지털 품질관리 플랫폼 결합'}\n"
            f"- **실행 증거(Evidence)**: {canvas.execution_evidence_notes or rag_snippet}\n\n"
            f"#### 경쟁사·대체재 비교 분석\n"
            f"| 구분 | 주요 대상(대체재) | 강점 | 한계점 | {company} 차별화 우위 |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"| 일반 영리 대행사 | 중견·일반 B2G 사업자 | 대형 인력풀 | 취약계층 연계·우선구매 특례 부재 | 5천만 1인 견적·1억 2인 견적 수의계약 진입 |\n"
            f"| 비영리·복지관 자체 수행 | 지자체 산하 수행기관 | 현장 신뢰성 | 상시 제품 고도화·B2B 확장 한계 | 내재형 영업수익 모델로 자생적 확장 |\n"
            f"| 당사 ({company}) | {canvas.competitor_notes or '사회적가치+품질 동시 검증 모델'} | SVI {svi.total_score:.0f}점({svi.grade_label}) | 초기 영업 레퍼런스 집중 필요 | 우선구매 실적 120% 가중 인정 활용 |\n"
        )

    if section_key in {"scaleup", "area_5", "area_8"}:
        channels_str = ", ".join(canvas.revenue_channels or ["B2G 공공우선구매(5천만 수의계약)", "B2B ESG 납품"])
        formula_sentence = (
            canvas.revenue_formula_sentence
            or f"우리는 [{canvas.payer or '공공기관·기업 ESG 담당자'}]에게 [{canvas.solution_mechanism or '표준화된 사회서비스·제품'}]을 제공하고, "
            f"[건당 {canvas.unit_price:,}원 정기·프로젝트 과금]으로 수익을 창출하여 [{canvas.beneficiary or '취약계층 일자리 창출'}]을 지속합니다."
        )
        return (
            f"### {section_title}\n\n"
            f"- **내재형 수익 창출 공식**: {formula_sentence}\n"
            f"- **핵심 판로 채널**: {channels_str}\n"
            f"- **공공시장 진입 레버리지**: 보조금에 의존하지 않고, 요건 충족 사회적기업 특례(1인 견적 5,000만 원 이하, 2인 이상 1억 원 이하) 및 "
            f"SVI {svi.total_score:.0f}점({svi.grade_label})에 따른 우선구매 120% 가중 인정을 영업 무기로 활용합니다.\n\n"
            f"#### 단위 경제성 및 손익분기점(BEP) 산출 근거\n\n"
            f"{ue.markdown_table}\n"
        )

    # team / area_6 / area_7
    roadmap_rows = canvas.roadmap_3yr or [
        {"year": "1차년도 (예비지정)", "hiring": "취약계층 35% 이상 고용 유지", "revenue": f"연 {ue.annual_revenue:,}원 달성", "svi": "SVI 75점(우수) 이상 확보"},
        {"year": "2차년도 (성장기)", "hiring": "전담 매니저 4명 추가 채용", "revenue": f"연 {int(ue.annual_revenue * 1.4):,}원", "svi": "이윤 2/3 사회적 환원 집행"},
        {"year": "3차년도 (정식인증)", "hiring": "취약계층 정규직 10명 이상", "revenue": f"연 {int(ue.annual_revenue * 2.0):,}원", "svi": "고용노동부 사회적기업 정식 인증"},
    ]
    rm_table = "\n".join(
        f"| {r.get('year', '')} | {r.get('hiring', '')} | {r.get('revenue', '')} | {r.get('svi', '')} |"
        for r in roadmap_rows
    )
    return (
        f"### {section_title}\n\n"
        f"- **사회적 목적 실현 유형**: {canvas.social_purpose_type}\n"
        f"- **SVI 자가 진단 결과**: 총점 **{svi.total_score:.1f}점 / 100점 ({svi.grade_label})** "
        f"(사회적 성과 {svi.social_score:.1f} / 경제적 성과 {svi.economic_score:.1f} / 혁신 성과 {svi.innovation_score:.1f})\n"
        f"- **민주적 의사결정구조**: {canvas.democratic_governance_plan or '근로자 대표·외부 사회서비스 전문가·지역 이해관계자가 참여하는 월 1회 운영위원회 및 분기별 이사회 회의록 증빙'}\n"
        f"- **이윤의 사회적 환원 계획**: {canvas.profit_reinvestment_plan}\n\n"
        f"#### 3개년 정식 인증 및 SVI 고도화 로드맵\n"
        f"| 연차 | 취약계층 고용·인력 계획 | 영업 매출 목표 | SVI·인증 마일스톤 |\n"
        f"| :--- | :--- | :--- | :--- |\n"
        f"{rm_table}\n"
    )


async def stream_bizplan_section(
    title: str,
    output_track: str,
    section_key: str,
    section_title: str,
    canvas: SocialBMCanvas,
    model_tier: str = "strong",
) -> AsyncIterator[str]:
    """소셜 사업계획서의 단일 섹션을 스트리밍 생성한다."""
    profile = load_profile()
    ue = calculate_unit_economics(
        UnitEconomicsInput(
            unit_price=canvas.unit_price,
            target_customers_monthly=canvas.target_customers_monthly,
            conversion_rate_pct=canvas.conversion_rate_pct,
            variable_cost_per_unit=canvas.variable_cost_per_unit,
            fixed_cost_monthly=canvas.fixed_cost_monthly,
        )
    )
    svi = evaluate_svi(canvas.svi_scores)
    # RAG 검색(질의 임베딩 네트워크 호출 포함)은 이벤트 루프를 막지 않도록 스레드에서 실행
    hits = await asyncio.to_thread(
        hybrid_search, f"{title} {section_title} {canvas.problem_statement} {canvas.solution_mechanism}", top_k=3
    )

    prompt_name = "bizplan_pre_se" if output_track == "pre_social" else "bizplan_psst"
    prompt_cfg = load_prompt(prompt_name)
    sys_prompt = str(prompt_cfg.get("system_prompt") or "")

    rag_ctx = "\n".join(f"- [{h.doc_title}] {h.content[:220]}" for h in hits)
    user_prompt = (
        f"사업계획서 제목: {title}\n"
        f"트랙: {output_track}\n"
        f"작성 섹션: {section_title}\n"
        f"Pain Point: {canvas.problem_statement}\n"
        f"수혜자: {canvas.beneficiary} / 실제 결제자: {canvas.payer}\n"
        f"해결 메커니즘: {canvas.solution_mechanism}\n"
        f"수익 공식: {canvas.revenue_formula_sentence}\n"
        f"단위 경제성 요약: {ue.formula_text}\n"
        f"SVI 총점: {svi.total_score}점 ({svi.grade_label})\n"
        f"로컬 RAG 근거:\n{rag_ctx}\n"
    )
    sim_text = _build_simulation_bizplan_section(
        title,
        section_key,
        section_title,
        canvas,
        ue,
        svi,
        profile,
        [{"content": h.content} for h in hits],
    )
    async with aclosing(
        stream_text(
            sys_prompt,
            user_prompt,
            feature=f"bizplan:{output_track}:{section_key}",
            tier=model_tier,
            simulation_text=sim_text,
        )
    ) as text_stream:
        async for ch in text_stream:
            yield ch


def run_redflag_audit(
    canvas: SocialBMCanvas,
    sections_content: dict[str, str],
) -> AuditReport:
    """심사관 레드플래그 5종 자동 점검 Critic 로직."""
    flags: list[RedFlagItem] = []
    full_text = "\n".join(sections_content.values())

    # 1. 양식·분량 위반 점검 (최소 섹션 수/길이 및 과도한 길이)
    if not sections_content or sum(len(v.strip()) for v in sections_content.values()) < 400:
        flags.append(
            RedFlagItem(
                code="format_length",
                title="양식·분량 미달 또는 필수 목차 누락",
                severity="high",
                section_key=next(iter(sections_content.keys()), "problem"),
                evidence_excerpt="작성된 본문 분량이 심사 기준 대비 부족하거나 섹션이 비어 있습니다.",
                recommendation="각 핵심 섹션에 최소 300자 이상, 목표 분량 범위 내로 내용을 채우거나 압축 정돈하세요.",
                action_label="분량 기준 맞춤 보강",
            )
        )
    else:
        for k, v in sections_content.items():
            if len(v.strip()) > 3800:
                flags.append(
                    RedFlagItem(
                        code="format_length",
                        title="섹션 분량 과다 (가독성·페이지 제한 위험)",
                        severity="medium",
                        section_key=k,
                        evidence_excerpt=f"'{k}' 섹션이 {len(v.strip()):,}자로 권장 한도(3,500자)를 초과했습니다.",
                        recommendation="핵심 수치와 표 위주로 문단을 압축(Summarize to fit)하세요.",
                        action_label="목표 분량 압축",
                    )
                )
                break

    # 2. 근거 없는 숫자("뇌피셜") 탐지
    has_revenue_formula = any(tok in full_text for tok in ("객단가", "전환율", "손익분기점", "BEP", "×"))
    has_big_number = bool(re.search(r"(\d+\s*억|\d{2,3}%\s*성장|매출\s*\d+)", full_text))
    if (has_big_number and not has_revenue_formula) or (not has_revenue_formula):
        flags.append(
            RedFlagItem(
                code="unsubstantiated_number",
                title='근거 없는 숫자("뇌피셜") 위험',
                severity="high",
                section_key="scaleup" if "scaleup" in sections_content else next(iter(sections_content.keys()), "scaleup"),
                evidence_excerpt="매출·시장 규모 수치 옆에 산출식(객단가×고객수×전환율) 또는 단위 경제성 표가 누락되었습니다.",
                recommendation="단위 경제성 계산기 결과 표와 [출처 확인 필요] 표기를 본문에 삽입하세요.",
                action_label="단위 경제성 표 삽입",
            )
        )

    # 3. "경쟁사 없음" 서술 탐지
    no_comp_match = re.search(r"(경쟁사[가]?\s*없|독점적|대체재가\s*전혀\s*없|유일무이)", full_text)
    if no_comp_match or ("비교" not in full_text and "경쟁" not in full_text):
        flags.append(
            RedFlagItem(
                code="no_competitor",
                title='"경쟁사 없음" 서술 또는 대체재 비교 누락',
                severity="high",
                section_key="solution" if "solution" in sections_content else next(iter(sections_content.keys()), "solution"),
                evidence_excerpt=no_comp_match.group(0) if no_comp_match else "간접 경쟁자·대체재 비교표가 본문에 없습니다.",
                recommendation="간접 경쟁자 및 기존 대체재(일반 영리업체·자체 수행) 비교표를 자동 삽입하세요.",
                action_label="경쟁사·대체재 비교표 삽입",
            )
        )

    # 4. 실행 증거(Evidence) 부재 탐지
    evidence_keywords = ("실적", "프로토타입", "설문", "구매의향", "회의록", "만족도", "계약", "LoI")
    if not any(k in full_text for k in evidence_keywords):
        flags.append(
            RedFlagItem(
                code="missing_evidence",
                title="실행 증거(Evidence) 부재",
                severity="medium",
                section_key="team" if "team" in sections_content else next(iter(sections_content.keys()), "team"),
                evidence_excerpt="과거 수행 실적, 고객 설문, 구매의향서(LoI) 또는 이사회 회의록 증빙 언급이 없습니다.",
                recommendation="로컬 RAG 마스터 프로필에서 실제 수행 실적과 증빙 계획을 검색해 삽입하세요.",
                action_label="RAG 실적·증빙 삽입",
            )
        )

    # 5. 보조금 의존형 수익구조 탐지
    subsidy_mentions = len(re.findall(r"(보조금|지원금|기부금|인건비\s*지원)", full_text))
    commercial_mentions = len(re.findall(r"(우선구매|수의계약|납품|객단가|영업\s*매출|B2B|B2G|과금)", full_text))
    if subsidy_mentions > commercial_mentions or commercial_mentions < 2:
        flags.append(
            RedFlagItem(
                code="subsidy_dependency",
                title="보조금 의존형 수익구조 우려",
                severity="high",
                section_key="scaleup" if "scaleup" in sections_content else next(iter(sections_content.keys()), "scaleup"),
                evidence_excerpt="정부지원금·보조금 언급 비중 대비 자생적 영업활동(B2G/B2B) 수익 모델 설명이 부족합니다.",
                recommendation="사회적기업 5천만 원 수의계약 특례 기반 B2G 우선구매 및 B2B ESG 납품 트랙을 보강하세요.",
                action_label="B2G·B2B 수익 트랙 보강",
            )
        )

    penalty = sum(16 if f.severity == "high" else 10 for f in flags)
    overall = max(25, 100 - penalty)
    return AuditReport(
        passed=len(flags) == 0,
        overall_score=overall,
        red_flags=flags,
        checked_at=_now_iso(),
    )


def apply_redflag_remediation(
    flag_code: str,
    section_key: str,
    canvas: SocialBMCanvas,
    sections_content: dict[str, str],
) -> dict[str, str]:
    """레드플래그 항목을 원클릭으로 본문에 보완 반영한다."""
    updated = dict(sections_content)
    target_key = section_key or next(iter(updated.keys()), "scaleup")
    cur_text = updated.get(target_key, "")
    profile = load_profile()

    if flag_code == "format_length":
        if len(cur_text) > 3500:
            lines = [ln for ln in cur_text.splitlines() if ln.strip()]
            updated[target_key] = "\n".join(lines[:28]) + "\n\n*(분량 기준에 맞춰 핵심 요지로 압축 정돈됨)*"
        elif len(cur_text.strip()) < 300:
            ue = calculate_unit_economics(
                UnitEconomicsInput(
                    unit_price=canvas.unit_price,
                    target_customers_monthly=canvas.target_customers_monthly,
                    conversion_rate_pct=canvas.conversion_rate_pct,
                    variable_cost_per_unit=canvas.variable_cost_per_unit,
                    fixed_cost_monthly=canvas.fixed_cost_monthly,
                )
            )
            svi = evaluate_svi(canvas.svi_scores)
            updated[target_key] = _build_simulation_bizplan_section(
                profile.get("name", "사회적기업"), target_key, target_key, canvas, ue, svi, profile, []
            )

    elif flag_code == "unsubstantiated_number":
        ue = calculate_unit_economics(
            UnitEconomicsInput(
                unit_price=canvas.unit_price,
                target_customers_monthly=canvas.target_customers_monthly,
                conversion_rate_pct=canvas.conversion_rate_pct,
                variable_cost_per_unit=canvas.variable_cost_per_unit,
                fixed_cost_monthly=canvas.fixed_cost_monthly,
            )
        )
        addition = (
            "\n\n#### [자동 보완] 단위 경제성 및 매출 산출 근거\n"
            f"- 산출 공식: {ue.formula_text}\n"
            f"- 외부 통계/설문 수치: [출처 확인 필요: 통계청 KOSIS 및 수요기관 자체 수요조사 반영]\n\n"
            f"{ue.markdown_table}\n"
        )
        if "[자동 보완] 단위 경제성" not in cur_text:
            updated[target_key] = cur_text + addition

    elif flag_code == "no_competitor":
        cleaned = re.sub(r"(경쟁사[가]?\s*없[다음을]?|독점적[인]?|대체재가\s*전혀\s*없[다음을]?)", "기존 대체재 대비 차별화된", cur_text)
        comp_table = (
            "\n\n#### [자동 보완] 간접 경쟁자 및 기존 대체재 비교표\n"
            "| 비교 기준 | 일반 영리업체 (경쟁사) | 기관 자체 수행 (대체재) | 당사 (사회적기업 솔루션) |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| 공공 우선구매 특례 | 미적용 (2천만 원 일반 한도) | 해당 없음 | **5천만 원 1인 견적·1억 2인 견적 수의계약 진입** |\n"
            "| 사회적 가치(SVI) | 미측정 | 내부 행정 부담 | **SVI 75점+ 우선구매 실적 120% 가중 인정** |\n"
            "| 품질·사후관리 | 단가 위주 계약 | 전문 인력 부족 | 표준 매뉴얼 + 취약계층 전담 매니저 상시 관리 |\n"
        )
        updated[target_key] = cleaned if "[자동 보완] 간접 경쟁자" in cleaned else cleaned + comp_table

    elif flag_code == "missing_evidence" and "[자동 보완] 실행 증거" not in cur_text:
        hits = hybrid_search("수행 실적 인증 만족도 성과", top_k=2)
        rag_lines = "\n".join(f"- **{h.doc_title}**: {h.content[:150].replace(chr(10), ' ')}" for h in hits)
        if not rag_lines:
            rag_lines = "- 당사 보유 실적 및 시범사업 만족도 조사 결과(계약서·완료보고서 증빙)"
        updated[target_key] = (
            cur_text
            + "\n\n#### [자동 보완] 실행 증거(Evidence) 및 증빙 계획\n"
            + f"{rag_lines}\n"
            + "- 거버넌스 증빙: 이사회·운영위원회 정기 회의록 및 수요기관 구매의향서(LoI) 첨부 예정\n"
        )

    elif flag_code == "subsidy_dependency" and "[자동 보완] 보조금 비의존" not in cur_text:
        updated[target_key] = (
            cur_text
            + "\n\n#### [자동 보완] 보조금 비의존 영업수익(B2G·B2B) 자생 전략\n"
            "- **트랙 1 (B2G 공공기관 우선구매)**: 요건 충족 사회적기업 수의계약 특례(물품·용역 1인 견적 5,000만 원 이하, "
            "2인 이상 1억 원 이하)와 SVI 우수등급 120% 실적 인정을 활용해 지자체·공공기관 반복 수주 확보\n"
            "- **트랙 2 (B2B ESG 조달 납품)**: 대기업·중견기업 ESG 사회공헌 및 협력사 공급망 납품을 통한 상업 객단가·영업 매출 확대\n"
        )

    return updated


async def apply_redflag_remediation_async(
    flag_code: str,
    section_key: str,
    canvas: SocialBMCanvas,
    sections_content: dict[str, str],
) -> dict[str, str]:
    """하이브리드 Critic Agent: 필수 근거 표 보강 후 Live LLM 모드 시 redflag_critic.yaml 프롬프트로 문맥 정밀 교정."""
    # 규칙 기반 보완에도 RAG 검색(임베딩)이 포함될 수 있어 스레드에서 실행
    updated = await asyncio.to_thread(apply_redflag_remediation, flag_code, section_key, canvas, sections_content)
    if is_simulation_active():
        return updated

    target_key = section_key or next(iter(updated.keys()), "scaleup")
    draft = updated.get(target_key, "")
    if not draft.strip():
        return updated

    prompt_cfg = load_prompt("redflag_critic")
    sys_prompt = (
        str(prompt_cfg.get("system_prompt") or "")
        + "\n요청된 레드플래그 코드가 해소되도록 아래 섹션 본문을 자연스러운 한국어 마크다운(표 유지)으로 교정·재작성한 최종 본문만 출력하세요."
    )
    user_prompt = (
        f"해소할 레드플래그 코드: {flag_code}\n"
        f"대상 섹션({target_key}) 초안:\n{draft}\n"
    )
    parts: list[str] = []
    async for ch in stream_text(
        sys_prompt,
        user_prompt,
        feature=f"bizplan_remediate:{flag_code}",
        tier="strong",
        simulation_text=draft,
    ):
        parts.append(ch)
    refined = "".join(parts).strip()
    if refined:
        updated[target_key] = refined
    return updated
