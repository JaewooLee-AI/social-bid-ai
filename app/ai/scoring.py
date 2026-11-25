"""2단계 AI 적합도 평가 (마스터 프로필 + 수의계약 특례 + 과거 제외 피드백 Few-shot)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.llm_client import generate_json, is_simulation_active
from app.config import load_profile, load_prompt
from app.db import get_conn

logger = logging.getLogger(__name__)


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _coerce_score(value: Any, default: float) -> int:
    """LLM이 준 점수를 0~100 정수로 안전 변환한다. "85점", "85.5", null 등도 처리하고 실패 시 기본값."""
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, (int, float)):
            num = float(value)
        else:
            m = _NUM_RE.search(str(value or ""))
            if not m:
                raise ValueError
            num = float(m.group(0))
        if num != num:  # NaN
            raise ValueError
    except (TypeError, ValueError):
        num = float(default)
    return int(round(max(0.0, min(100.0, num))))


def _coerce_risks(value: Any, default: list[str]) -> list[str]:
    """리스크 목록 정규화. 문자열 하나가 오면 글자 단위로 쪼개지지 않도록 한 항목으로 감싼다."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else list(default)
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if str(v).strip()]
        return items or list(default)
    return list(default)


def _coerce_summary(value: Any, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _recent_exclusion_feedback(limit: int = 8) -> list[dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT bid_title, agency, reason_tag, note
            FROM exclusion_feedback
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "bid_title": r["bid_title"],
            "agency": r["agency"],
            "reason_tag": r["reason_tag"],
            "note": r["note"],
        }
        for r in rows
    ]


def _heuristic_bid_score(
    bid: dict[str, Any],
    profile: dict[str, Any],
    negatives: list[dict[str, str]],
) -> dict[str, Any]:
    score = 58.0
    reasons: list[str] = []
    risks: list[str] = []

    priv = bid.get("privilege_tier", "none")
    if priv == "private_1_quote":
        score += 22.0
        reasons.append("5,000만 원 이하 사회적기업 1인 견적 수의계약 특례 구간으로 단독 수주 가능성이 높습니다.")
    elif priv == "private_2_quotes":
        score += 15.0
        reasons.append("1억 원 이하 2인 이상 견적 수의계약 특례 대상이라 일반경쟁 대비 수주 장벽이 낮습니다.")

    svi_score = float(profile.get("svi_current_score") or 0.0)
    if svi_score >= 75.0:
        score += 6.0
        reasons.append(f"보유 SVI 점수({svi_score:.0f}점)가 우수 등급 이상으로 공공기관 우선구매 120% 실적 인정 강점이 있습니다.")

    # 기업 요약/과거 실적 키워드 매칭
    title = str(bid.get("title", ""))
    profile_text = (
        str(profile.get("summary", ""))
        + " "
        + " ".join(p.get("title", "") for p in (profile.get("past_projects") or []))
        + " "
        + " ".join(profile.get("direct_production_items") or [])
    )
    matched_kw = [
        kw
        for kw in ("디지털", "돌봄", "교육", "친환경", "패키지", "홍보", "인쇄", "소프트웨어", "ESG", "사회적", "노인", "어르신", "복지")
        if kw in title and kw in profile_text
    ]
    if matched_kw:
        score += min(14.0, len(matched_kw) * 5.0)
        reasons.append(f"우리 회사 핵심 실적 키워드({', '.join(matched_kw[:3])})와 공고 과업이 직접 일치합니다.")

    # 규칙 경고 반영
    warnings = bid.get("rule_warnings") or []
    if isinstance(warnings, str):
        try:
            warnings = json.loads(warnings)
        except Exception:
            warnings = []
    for w in warnings:
        score -= 7.0
        risks.append(str(w))

    # 네거티브 피드백(과거 제외 사유) 반영
    for neg in negatives:
        tag = neg.get("reason_tag", "")
        neg_title = neg.get("bid_title", "")
        if tag == "단가 부족" and int(bid.get("estimated_price") or 0) < 20_000_000:
            score -= 8.0
            risks.append("과거 '단가 부족'으로 제외했던 소액 구간과 유사합니다.")
            break
        if any(tok in title for tok in neg_title.split() if len(tok) >= 2):
            score -= 6.0
            risks.append(f"최근 제외 이력('{tag}': {neg_title[:24]})과 유사한 과업입니다.")
            break

    if not reasons:
        reasons.append("1차 규칙 필터(지역·금액·업종)를 통과한 공고입니다. 원문 과업지시서 검토를 권장합니다.")
    if not risks:
        risks.append("최종 견적 제출 전 공고문 내 발주처 계약방법 및 세부 자격요건 원문 확인이 필요합니다.")

    final_score = max(15, min(98, int(round(score))))
    return {
        "score": final_score,
        "summary": " ".join(reasons[:2]),
        "risks": risks[:3],
    }


async def score_bid(bid: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """단일 공고의 AI 적합도 점수(0~100), 추천 사유 2줄, 핵심 리스크를 산출한다."""
    profile = profile or load_profile()
    negatives = _recent_exclusion_feedback()
    heuristic = _heuristic_bid_score(bid, profile, negatives)

    if is_simulation_active():
        return heuristic

    prompt_cfg = load_prompt("bid_scoring")
    sys_prompt = str(prompt_cfg.get("system_prompt") or "")
    user_prompt = json.dumps(
        {
            "company_profile": {
                "name": profile.get("name"),
                "social_type": profile.get("social_type"),
                "vulnerable_employment_ratio": profile.get("vulnerable_employment_ratio"),
                "svi_current_score": profile.get("svi_current_score"),
                "direct_production_items": profile.get("direct_production_items"),
                "summary": profile.get("summary"),
                "past_projects": profile.get("past_projects"),
            },
            "recent_exclusion_feedback": negatives,
            "bid": {
                "source": bid.get("source"),
                "title": bid.get("title"),
                "agency": bid.get("agency"),
                "category": bid.get("category"),
                "contract_method": bid.get("contract_method"),
                "estimated_price": bid.get("estimated_price"),
                "region_limit": bid.get("region_limit"),
                "privilege_label": bid.get("privilege_label"),
                "rule_warnings": bid.get("rule_warnings"),
            },
        },
        ensure_ascii=False,
    )
    res = await generate_json(
        sys_prompt,
        user_prompt,
        feature="bid_scoring",
        tier="fast",
        simulation_payload=heuristic,
    )
    if not isinstance(res, dict):
        return heuristic
    return {
        "score": _coerce_score(res.get("score"), heuristic["score"]),
        "summary": _coerce_summary(res.get("summary"), heuristic["summary"]),
        "risks": _coerce_risks(res.get("risks"), heuristic["risks"]),
    }


async def score_bids_batch(
    bids: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
    batch_size: int = 10,
    max_llm_bids: int = 30,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """
    다수의 공고를 batch_size 단위로 묶어 경량 LLM에 일괄(Batch) 평가 요청한다.
    API 호출 횟수와 레이턴시를 대폭 절감하며, 시뮬레이션 모드 또는 오류 시 휴리스틱 점수로 안전 폴백한다.
    """
    if not bids:
        return {}
    profile = profile or load_profile()
    negatives = _recent_exclusion_feedback()

    heuristics_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    for b in bids:
        key = (str(b.get("source", "")), str(b.get("bid_no", "")), str(b.get("bid_ord", "00")))
        if b.get("rule_passed", True):
            heuristics_map[key] = _heuristic_bid_score(b, profile, negatives)
        else:
            heuristics_map[key] = {
                "score": 25,
                "summary": "1차 규칙 필터(지역·업종·금액) 조건에 맞지 않아 하위 순위로 분류되었습니다.",
                "risks": list(b.get("rule_warnings") or []),
            }

    if is_simulation_active():
        return heuristics_map

    pass_bids = [b for b in bids if b.get("rule_passed", True)]
    if max_llm_bids > 0:
        pass_bids.sort(
            key=lambda b: heuristics_map[
                (str(b.get("source", "")), str(b.get("bid_no", "")), str(b.get("bid_ord", "00")))
            ]["score"],
            reverse=True,
        )
        pass_bids = pass_bids[:max_llm_bids]
    else:
        pass_bids = []
    if not pass_bids:
        return heuristics_map

    prompt_cfg = load_prompt("bid_scoring")
    sys_prompt = (
        str(prompt_cfg.get("system_prompt") or "")
        + "\n배치 모드: 입력된 bids 배열 각각을 평가해 "
        '{"scores": [{"bid_no": "...", "score": int, "summary": str, "risks": [str, ...]}]} JSON으로 반환하세요.'
    )

    for i in range(0, len(pass_bids), batch_size):
        chunk = pass_bids[i : i + batch_size]
        fallback_chunk = {
            "scores": [
                {
                    "bid_no": str(b.get("bid_no", "")),
                    **heuristics_map[(str(b.get("source", "")), str(b.get("bid_no", "")), str(b.get("bid_ord", "00")))],
                }
                for b in chunk
            ]
        }
        user_prompt = json.dumps(
            {
                "company_profile": {
                    "name": profile.get("name"),
                    "social_type": profile.get("social_type"),
                    "vulnerable_employment_ratio": profile.get("vulnerable_employment_ratio"),
                    "svi_current_score": profile.get("svi_current_score"),
                    "direct_production_items": profile.get("direct_production_items"),
                    "summary": profile.get("summary"),
                    "past_projects": profile.get("past_projects"),
                },
                "recent_exclusion_feedback": negatives,
                "bids": [
                    {
                        "bid_no": b.get("bid_no"),
                        "source": b.get("source"),
                        "title": b.get("title"),
                        "agency": b.get("agency"),
                        "category": b.get("category"),
                        "contract_method": b.get("contract_method"),
                        "estimated_price": b.get("estimated_price"),
                        "region_limit": b.get("region_limit"),
                        "privilege_label": b.get("privilege_label"),
                        "rule_warnings": b.get("rule_warnings"),
                    }
                    for b in chunk
                ],
            },
            ensure_ascii=False,
        )
        res = await generate_json(
            sys_prompt,
            user_prompt,
            feature="bid_scoring_batch",
            tier="fast",
            simulation_payload=fallback_chunk,
        )
        raw_scores = res.get("scores") if isinstance(res, dict) else None
        scored_by_no = {
            str(item.get("bid_no", "")): item
            for item in (raw_scores if isinstance(raw_scores, list) else [])
            if isinstance(item, dict) and item.get("bid_no")
        }
        for b in chunk:
            bkey = (str(b.get("source", "")), str(b.get("bid_no", "")), str(b.get("bid_ord", "00")))
            match = scored_by_no.get(str(b.get("bid_no", "")))
            if match:
                base = heuristics_map[bkey]
                heuristics_map[bkey] = {
                    "score": _coerce_score(match.get("score"), base["score"]),
                    "summary": _coerce_summary(match.get("summary"), base["summary"]),
                    "risks": _coerce_risks(match.get("risks"), base["risks"]),
                }

    return heuristics_map
