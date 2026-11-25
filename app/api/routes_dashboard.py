"""대시보드 & 인사이트 요약 API 라우터."""

from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends

from app.ai.bizplan_gen import evaluate_svi
from app.ai.llm_client import is_simulation_active
from app.api.deps import verify_csrf_token
from app.collectors.base import get_quota_statuses
from app.config import load_profile
from app.db import get_conn, is_vec_available
from app.schemas.common import DashboardSummary, QuotaStatus, RecentWorkItem, UrgentBidItem

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(verify_csrf_token)])


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary() -> DashboardSummary:
    profile = load_profile()
    svi_cur = float(profile.get("svi_current_score") or 75.0)
    svi_target = float(profile.get("svi_target_score") or 85.0)

    # SVI 등급 라벨 조회
    svi_eval = evaluate_svi({})
    # 프로필 점수로 등급 판정
    from app.config import load_svi_rubric

    rubric = load_svi_rubric()
    grade_label = svi_eval.grade_label
    for g in rubric.get("grades", []):
        if float(g["min_score"]) <= svi_cur <= float(g["max_score"]) + 0.01:
            grade_label = str(g["label"])
            break

    month_prefix = datetime.now().strftime("%Y-%m")
    with get_conn() as conn:
        pending_cnt = conn.execute("SELECT COUNT(*) AS c FROM bids WHERE status = 'pending'").fetchone()["c"]
        approved_cnt = conn.execute("SELECT COUNT(*) AS c FROM bids WHERE status = 'approved'").fetchone()["c"]
        prop_cnt = conn.execute("SELECT COUNT(*) AS c FROM proposals").fetchone()["c"]
        biz_cnt = conn.execute("SELECT COUNT(*) AS c FROM bizplans").fetchone()["c"]

        tok_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens + output_tokens + thinking_tokens), 0) AS toks,
                COALESCE(SUM(cost_usd), 0.0) AS usd,
                COALESCE(SUM(cost_krw), 0.0) AS krw
            FROM token_usage
            WHERE created_at LIKE ?
            """,
            (f"{month_prefix}%",),
        ).fetchone()

        urgent_rows = conn.execute(
            """
            SELECT id, source, bid_no, title, agency, estimated_price, deadline,
                   privilege_tier, privilege_label, ai_score, status
            FROM bids
            WHERE status IN ('pending', 'approved')
            ORDER BY COALESCE(ai_score, 0) DESC, id DESC
            LIMIT 5
            """
        ).fetchall()

        prop_rows = conn.execute(
            "SELECT id, title, scenario, updated_at FROM proposals ORDER BY updated_at DESC LIMIT 4"
        ).fetchall()
        biz_rows = conn.execute(
            "SELECT id, title, output_track, updated_at FROM bizplans ORDER BY updated_at DESC LIMIT 4"
        ).fetchall()

    urgent_bids = [
        UrgentBidItem(
            id=r["id"],
            source=r["source"],
            bid_no=r["bid_no"],
            title=r["title"],
            agency=r["agency"],
            estimated_price=r["estimated_price"],
            deadline=r["deadline"],
            privilege_tier=r["privilege_tier"],
            privilege_label=r["privilege_label"],
            ai_score=r["ai_score"],
            status=r["status"],
        )
        for r in urgent_rows
    ]

    recent_works: list[RecentWorkItem] = []
    for r in prop_rows:
        recent_works.append(
            RecentWorkItem(
                kind="proposal",
                id=r["id"],
                title=r["title"],
                subtitle=f"제안서 · 시나리오 {r['scenario']}",
                updated_at=r["updated_at"],
            )
        )
    for r in biz_rows:
        track_lbl = "PSST 표준" if r["output_track"] == "psst" else ("예비사회적기업 8대 영역" if r["output_track"] == "pre_social" else "HWPX 양식 패치")
        recent_works.append(
            RecentWorkItem(
                kind="bizplan",
                id=r["id"],
                title=r["title"],
                subtitle=f"사업계획서 · {track_lbl}",
                updated_at=r["updated_at"],
            )
        )
    recent_works.sort(key=lambda x: x.updated_at, reverse=True)

    quotas = [QuotaStatus(**q) for q in get_quota_statuses()]

    return DashboardSummary(
        urgent_bids_count=len(urgent_bids),
        pending_review_count=int(pending_cnt),
        approved_bids_count=int(approved_cnt),
        active_proposals_count=int(prop_cnt),
        active_bizplans_count=int(biz_cnt),
        monthly_cost_krw=round(float(tok_row["krw"]), 1),
        monthly_cost_usd=round(float(tok_row["usd"]), 4),
        monthly_tokens=int(tok_row["toks"]),
        svi_current_score=svi_cur,
        svi_target_score=svi_target,
        svi_grade_label=grade_label,
        priority_purchase_120=svi_cur >= 75.0,
        simulation_mode=is_simulation_active(),
        vec_available=is_vec_available(),
        quotas=quotas,
        urgent_bids=urgent_bids,
        recent_works=recent_works[:6],
    )
