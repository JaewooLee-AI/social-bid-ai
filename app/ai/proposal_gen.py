"""제안서 섹션별 로컬 RAG 기반 SSE 스트리밍 생성 엔진."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from app.ai.llm_client import stream_text
from app.ai.rag_store import hybrid_search
from app.config import load_profile, load_prompt
from app.schemas.docs import OutlineNode


def _build_simulation_section_draft(
    proposal_title: str,
    node: OutlineNode,
    rfp_summary: dict[str, Any],
    profile: dict[str, Any],
    rag_hits: list[dict[str, Any]],
) -> str:
    company = profile.get("name") or "당사"
    social_type = profile.get("social_type") or "사회적기업"
    vuln_ratio = profile.get("vulnerable_employment_ratio") or 30.0
    svi_score = profile.get("svi_current_score") or 75.0
    projects = profile.get("past_projects") or []
    proj_line = (
        f"**{projects[0].get('client', '')}** 대상 「{projects[0].get('title', '')}」({projects[0].get('outcome', '')})"
        if projects
        else "다수의 공공기관·지자체 용역 수행 경험"
    )
    rag_excerpt = (
        (rag_hits[0].get("snippet") or rag_hits[0].get("content") or "")[:180].replace("\n", " ")
        if rag_hits
        else profile.get("summary", "")
    )
    steering_line = f"> **반영 지시사항**: {node.steering_note}\n\n" if node.steering_note else ""

    return (
        f"### {node.number}. {node.title}\n\n"
        f"{steering_line}"
        f"**{company}**({social_type})은 「{proposal_title}」의 핵심 목표를 달성하기 위해 "
        f"현장 중심의 실행 체계와 공공기관 우선구매 기준에 부합하는 사회적 가치 창출 구조를 제안합니다.\n\n"
        f"#### 1) 추진 방향 및 당사 수행 강점\n"
        f"- **검증된 실행 역량**: {proj_line} 등 축적된 레퍼런스를 바탕으로 착수 즉시 투입 가능한 표준 운영 매뉴얼을 보유하고 있습니다.\n"
        f"- **사회적 가치 연계 (SVI {svi_score:.0f}점)**: 취약계층 고용비율 {vuln_ratio:.1f}%를 유지하며, "
        f"과업 수행 과정에서 지역사회 공헌 및 우선구매 실적 120% 인정 요건에 부합하는 정량 KPI를 함께 달성합니다.\n"
        f"- **핵심 근거 요약**: {rag_excerpt}\n\n"
        f"#### 2) 세부 실행 계획 및 산출물\n"
        f"| 단계 | 주요 과업 내용 | 핵심 산출물 | 검증 기준 |\n"
        f"| :--- | :--- | :--- | :--- |\n"
        f"| 착수 (1주차) | 수요기관 요구사항 정밀 분석 및 전담팀 구성 | 착수보고서·WBS 일정표 | 담당자 승인 완료 |\n"
        f"| 실행 (2~8주차) | {node.title} 중심 과업 수행 및 주간 점검 | 중간 산출물·현장 운영 일지 | 주간 KPI 95%+ 달성 |\n"
        f"| 완료·사후관리 | 성과 측정·만족도 조사 및 최종 검수 | 완료보고서·SVI 성과 요약서 | 검수 합격 및 하자보증 |\n\n"
        f"#### 3) 기대 효과 및 리스크 관리\n"
        f"- 배점 **{node.weight_score:.0f}점** 비중(목표 분량 {node.target_chars:,}자 기준)에 맞춰 "
        f"발주처가 요구하는 정량·정성 지표를 착수 시점부터 체계적으로 관리하겠습니다.\n"
    )


async def stream_proposal_section(
    proposal_title: str,
    scenario: str,
    node: OutlineNode,
    rfp_summary: dict[str, Any],
    model_tier: str = "strong",
) -> tuple[list[dict[str, Any]], AsyncIterator[str]]:
    """단일 목차 노드에 대해 RAG 검색을 선행한 뒤 스트리밍 텍스트 이터레이터와 인용 출처를 반환한다."""
    profile = load_profile()
    query = f"{proposal_title} {node.title} {node.steering_note}".strip()
    # RAG 검색(질의 임베딩 네트워크 호출 포함)은 이벤트 루프를 막지 않도록 스레드에서 실행
    hits = await asyncio.to_thread(hybrid_search, query, top_k=4)
    citations = [
        {
            "doc_title": h.doc_title,
            "doc_type": h.doc_type,
            "rrf_score": h.rrf_score,
            "snippet": h.content[:140],
        }
        for h in hits
    ]

    prompt_cfg = load_prompt("proposal_section")
    sys_prompt = str(prompt_cfg.get("system_prompt") or "")

    rag_context = "\n\n".join(
        f"[출처 {i}: {h.doc_title} (RRF={h.rrf_score})]\n{h.content}" for i, h in enumerate(hits, start=1)
    )
    user_prompt = (
        f"제안서명: {proposal_title}\n"
        f"시나리오: {scenario}\n"
        f"작성 섹션: {node.number}. {node.title}\n"
        f"배점 가중치: {node.weight_score}점 / 목표 글자수: 약 {node.target_chars}자\n"
        f"사용자 섹션 지시문(Steering Note): {node.steering_note or '없음'}\n\n"
        f"[RFP 요약]\n{json.dumps(rfp_summary, ensure_ascii=False)}\n\n"
        f"[우리 회사 마스터 프로필 및 로컬 RAG 검색 근거]\n{rag_context}\n"
    )

    sim_text = _build_simulation_section_draft(proposal_title, node, rfp_summary, profile, citations)
    gen = stream_text(
        sys_prompt,
        user_prompt,
        feature=f"proposal_section:{node.id}",
        tier=model_tier,
        simulation_text=sim_text,
    )
    return citations, gen
