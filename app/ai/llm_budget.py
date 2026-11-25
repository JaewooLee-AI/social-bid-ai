"""월간 Live LLM API 비용 상한(예산) 관리 및 호출 전 대략 비용 추정.

- 예산은 config.llm.monthly_budget_krw(원). 0이면 무제한.
- 집계 대상은 이번 달 token_usage 중 provider != 'simulation' 인 실제 과금 기록.
- 상한은 "호출 시작 전" 검사하는 소프트 한도다. 이미 시작된 호출은 끝까지 기록되므로
  마지막 호출만큼 예산을 약간 초과할 수 있다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ai.model_catalog import get_model_pricing
from app.config import load_config
from app.db import get_conn


class BudgetExceededError(RuntimeError):
    """월간 예산 초과로 신규 Live LLM 호출이 차단됨."""


# 호출 1회당 평균 토큰 가정치(대략 추정용). 실제 값은 프롬프트·RAG 근거·모델 thinking 수준에 따라 달라진다.
_ESTIMATE_PROFILES: dict[str, dict[str, int]] = {
    # 섹션 초안 1개: 시스템+사용자 프롬프트+RAG 근거 입력, 본문 출력 + thinking(MEDIUM)
    "section": {"input": 2500, "output": 2500, "thinking": 1500, "embed": 120},
    # 레드플래그 보완 1회: 섹션 전체를 입력해 재작성
    "remediate": {"input": 3500, "output": 3000, "thinking": 1500, "embed": 0},
    # 임베딩 1청크
    "embedding": {"input": 0, "output": 0, "thinking": 0, "embed": 400},
}


def _month_prefix() -> str:
    # token_usage.created_at은 로컬 시간대 ISO 문자열이므로 로컬 기준 연-월로 비교한다.
    return datetime.now().strftime("%Y-%m")


def get_month_live_cost_krw() -> float:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_krw), 0.0) AS used
            FROM token_usage
            WHERE provider != 'simulation' AND created_at LIKE ?
            """,
            (f"{_month_prefix()}%",),
        ).fetchone()
    return float(row["used"] or 0.0)


def _is_simulation(cfg: dict[str, Any]) -> bool:
    from app.ai.llm_client import is_simulation_active

    return is_simulation_active(cfg)


def ensure_budget_available(cfg: dict[str, Any] | None = None) -> None:
    """Live 모드에서 이번 달 사용액이 예산 이상이면 BudgetExceededError를 던진다."""
    cfg = cfg or load_config()
    if _is_simulation(cfg):
        return
    budget = float(cfg.get("llm", {}).get("monthly_budget_krw") or 0.0)
    if budget <= 0:
        return
    used = get_month_live_cost_krw()
    if used >= budget:
        raise BudgetExceededError(
            f"이번 달 LLM API 예산({budget:,.0f}원)을 모두 사용했습니다(사용 {used:,.0f}원). "
            "[마스터 프로필 · 외부 설정]에서 월 예산을 늘리거나 다음 달에 다시 시도하세요."
        )


def estimate_cost_krw(kind: str, calls: int, tier: str = "strong", cfg: dict[str, Any] | None = None) -> tuple[float, str]:
    """작업 종류·호출 횟수 기준 대략 비용(원)과 사용 모델명을 반환한다."""
    cfg = cfg or load_config()
    llm = cfg.get("llm", {})
    usd_to_krw = float(llm.get("usd_to_krw", 1380.0))
    profile = _ESTIMATE_PROFILES.get(kind, _ESTIMATE_PROFILES["section"])
    calls = max(0, int(calls))

    if kind == "embedding":
        model = str(llm.get("embedding_model", "gemini-embedding-001"))
    else:
        model = str(llm.get("strong_model" if tier == "strong" else "fast_model", ""))

    usd = 0.0
    rates = get_model_pricing(model, cfg) if model else None
    if rates is not None:
        in_r, out_r = rates
        usd += calls * ((profile["input"] / 1e6) * in_r + ((profile["output"] + profile["thinking"]) / 1e6) * out_r)
    if profile["embed"]:
        emb_model = str(llm.get("embedding_model", "gemini-embedding-001"))
        emb_rates = get_model_pricing(emb_model, cfg)
        if emb_rates is not None:
            usd += calls * (profile["embed"] / 1e6) * emb_rates[0]
    return usd * usd_to_krw, model


def get_budget_status(kind: str = "", calls: int = 0, tier: str = "strong") -> dict[str, Any]:
    cfg = load_config()
    budget = float(cfg.get("llm", {}).get("monthly_budget_krw") or 0.0)
    used = get_month_live_cost_krw()
    simulation = _is_simulation(cfg)
    estimate_krw, model = (0.0, "")
    if kind and calls > 0:
        estimate_krw, model = estimate_cost_krw(kind, calls, tier, cfg)
    return {
        "simulation_mode": simulation,
        "monthly_budget_krw": budget,
        "used_krw": round(used, 2),
        "remaining_krw": round(max(0.0, budget - used), 2) if budget > 0 else None,
        "exceeded": (not simulation) and budget > 0 and used >= budget,
        "estimate_krw": round(estimate_krw, 1),
        "estimate_model": model,
        "would_exceed": (not simulation) and budget > 0 and used + estimate_krw > budget,
    }
