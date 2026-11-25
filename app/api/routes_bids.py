"""맞춤 공고 수집(3대 공공 API), 1차 규칙 필터, 2단계 AI 적합도 및 Human-in-the-Loop 검토 게이트 API."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.ai.job_guard import acquire_or_409, release
from app.ai.scoring import score_bids_batch
from app.api.deps import verify_csrf_token
from app.collectors.base import get_quota_statuses, update_sync_state
from app.collectors.bizinfo import extract_and_download_bizinfo_attachments, fetch_bizinfo_notices
from app.collectors.demo_seed import build_demo_bids, materialize_demo_attachments
from app.collectors.g2b import download_g2b_attachments, fetch_g2b_bids
from app.collectors.rule_filter import apply_social_enterprise_rules
from app.collectors.seis import fetch_seis_notices
from app.config import load_config, load_profile
from app.db import get_conn
from app.documents.hwpx_engine import parse_hwpx_or_hwp
from app.documents.pdf_engine import parse_pdf
from app.documents.rfp_analyzer import (
    build_outline_from_document,
    build_rfp_summary_and_gonogo,
    detect_scenario,
    extract_scoring_criteria,
)
from app.schemas.bids import (
    AttachmentInfo,
    BidDecisionRequest,
    BidDecisionResponse,
    BidItem,
    BidSyncRequest,
    BidSyncResult,
    BidSyncSourceReport,
    RfpAnalysis,
)
from app.schemas.common import QuotaStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bids", tags=["bids"], dependencies=[Depends(verify_csrf_token)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _row_to_bid_item(row: Any) -> BidItem:
    warnings = json.loads(row["rule_warnings_json"] or "[]")
    risks = json.loads(row["ai_risks_json"] or "[]")
    raw_atts = json.loads(row["attachments_json"] or "[]")
    atts = [AttachmentInfo(**a) for a in raw_atts if isinstance(a, dict)]
    rfp_raw = json.loads(row["rfp_analysis_json"] or "{}")
    rfp = RfpAnalysis(**rfp_raw) if rfp_raw and rfp_raw.get("summary") else None
    return BidItem(
        id=row["id"],
        source=row["source"],
        bid_no=row["bid_no"],
        bid_ord=row["bid_ord"],
        title=row["title"],
        agency=row["agency"],
        category=row["category"],
        contract_method=row["contract_method"],
        estimated_price=row["estimated_price"],
        deadline=row["deadline"],
        published_at=row["published_at"],
        region_limit=row["region_limit"],
        license_limit=row["license_limit"],
        is_sme_competing=bool(row["is_sme_competing"]),
        is_demo=bool(row["is_demo"]),
        url=row["url"],
        privilege_tier=row["privilege_tier"],
        privilege_label=row["privilege_label"],
        rule_passed=bool(row["rule_passed"]),
        rule_warnings=warnings,
        ai_score=row["ai_score"],
        ai_summary=row["ai_summary"],
        ai_risks=risks,
        status=row["status"],
        exclusion_reason=row["exclusion_reason"],
        exclusion_note=row["exclusion_note"],
        attachments=atts,
        rfp_analysis=rfp,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _upsert_and_score_bids(raw_items: list[dict[str, Any]], profile: dict[str, Any]) -> tuple[int, int]:
    """1차 규칙 필터 후 기존 평가 캐시는 유지하고, 신규·미채점 공고만 배치(Batch)로 채점해 단일 트랜잭션으로 저장한다."""
    if not raw_items:
        return 0, 0

    now = _now_iso()
    enriched_list = [apply_social_enterprise_rules(raw, profile) for raw in raw_items]

    # 1. 기존 DB 캐시 조회 (동기 DB I/O는 이벤트 루프 밖에서)
    existing_map = await run_in_threadpool(_load_existing_bid_map)

    to_score: list[dict[str, Any]] = []
    cached_scores: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in enriched_list:
        key = (str(item["source"]), str(item["bid_no"]), str(item.get("bid_ord", "00")))
        ex = existing_map.get(key)
        # 이미 AI 평가가 존재하고 규칙 통과 상태가 동일하면 불필요한 LLM 재호출 생략
        if ex and ex.get("ai_score") is not None and bool(ex.get("rule_passed")) == bool(item["rule_passed"]):
            cached_scores[key] = {
                "score": float(ex["ai_score"]),
                "summary": str(ex["ai_summary"] or ""),
                "risks": json.loads(ex["ai_risks_json"] or "[]"),
            }
        else:
            to_score.append(item)

    batch_scores = await score_bids_batch(to_score, profile) if to_score else {}
    all_scores = {**cached_scores, **batch_scores}

    # 3. 단일 트랜잭션 저장 (대량 UPSERT는 이벤트 루프 밖에서)
    saved = await run_in_threadpool(_write_bids, enriched_list, all_scores, existing_map, now)
    return saved, len(to_score)


def _load_existing_bid_map() -> dict[tuple[str, str, str], dict[str, Any]]:
    existing_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, source, bid_no, bid_ord, rule_passed, ai_score, ai_summary, ai_risks_json FROM bids"
        ).fetchall()
        for r in rows:
            existing_map[(r["source"], r["bid_no"], r["bid_ord"])] = dict(r)
    return existing_map


def _write_bids(
    enriched_list: list[dict[str, Any]],
    all_scores: dict[tuple[str, str, str], dict[str, Any]],
    existing_map: dict[tuple[str, str, str], dict[str, Any]],
    now: str,
) -> int:
    saved = 0
    with get_conn() as conn:
        for enriched in enriched_list:
            key = (str(enriched["source"]), str(enriched["bid_no"]), str(enriched.get("bid_ord", "00")))
            ai_res = all_scores.get(key, {"score": 50, "summary": "", "risks": []})
            ex = existing_map.get(key)
            atts_json = json.dumps(enriched.get("attachments") or [], ensure_ascii=False)
            if ex:
                conn.execute(
                    """
                    UPDATE bids
                    SET title = ?, agency = ?, category = ?, contract_method = ?, estimated_price = ?,
                        deadline = ?, published_at = ?, region_limit = ?, license_limit = ?,
                        is_sme_competing = ?, url = ?, raw_json = ?,
                        privilege_tier = ?, privilege_label = ?, rule_passed = ?, rule_warnings_json = ?,
                        ai_score = ?, ai_summary = ?, ai_risks_json = ?, is_demo = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        enriched["title"],
                        enriched["agency"],
                        enriched["category"],
                        enriched["contract_method"],
                        int(enriched.get("estimated_price") or 0),
                        enriched.get("deadline", ""),
                        enriched.get("published_at", ""),
                        enriched.get("region_limit", "전국"),
                        enriched.get("license_limit", ""),
                        1 if enriched.get("is_sme_competing") else 0,
                        enriched.get("url", ""),
                        json.dumps(enriched.get("raw_json") or {}, ensure_ascii=False),
                        enriched["privilege_tier"],
                        enriched["privilege_label"],
                        1 if enriched["rule_passed"] else 0,
                        json.dumps(enriched["rule_warnings"], ensure_ascii=False),
                        float(ai_res["score"]),
                        ai_res["summary"],
                        json.dumps(ai_res["risks"], ensure_ascii=False),
                        1 if enriched.get("is_demo") else 0,
                        now,
                        ex["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO bids (
                        source, bid_no, bid_ord, title, agency, category, contract_method,
                        estimated_price, deadline, published_at, region_limit, license_limit,
                        is_sme_competing, url, raw_json, privilege_tier, privilege_label,
                        rule_passed, rule_warnings_json, ai_score, ai_summary, ai_risks_json,
                        status, attachments_json, is_demo, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        enriched["source"],
                        enriched["bid_no"],
                        enriched.get("bid_ord", "00"),
                        enriched["title"],
                        enriched["agency"],
                        enriched["category"],
                        enriched["contract_method"],
                        int(enriched.get("estimated_price") or 0),
                        enriched.get("deadline", ""),
                        enriched.get("published_at", ""),
                        enriched.get("region_limit", "전국"),
                        enriched.get("license_limit", ""),
                        1 if enriched.get("is_sme_competing") else 0,
                        enriched.get("url", ""),
                        json.dumps(enriched.get("raw_json") or {}, ensure_ascii=False),
                        enriched["privilege_tier"],
                        enriched["privilege_label"],
                        1 if enriched["rule_passed"] else 0,
                        json.dumps(enriched["rule_warnings"], ensure_ascii=False),
                        float(ai_res["score"]),
                        ai_res["summary"],
                        json.dumps(ai_res["risks"], ensure_ascii=False),
                        atts_json,
                        1 if enriched.get("is_demo") else 0,
                        now,
                        now,
                    ),
                )
            saved += 1
    return saved


_SOURCE_SPECS: dict[str, tuple[str, str]] = {
    "g2b": ("g2b_service_key", "나라장터"),
    "seis": ("seis_service_key", "한국사회적기업진흥원"),
    "bizinfo": ("bizinfo_api_key", "기업마당"),
}


@router.post("/sync", response_model=BidSyncResult)
async def sync_bids(req: BidSyncRequest) -> BidSyncResult:
    """공고 수집. 샘플(데모) 공고는 해당 소스의 API 키가 **없을 때만** 채운다.

    키가 있는데 실제 수집이 실패하면 샘플로 대체하지 않고 실패로 보고한다(가짜 공고가 실제 인박스에 섞이는 것을 방지).
    """
    cfg = load_config()["public_api"]
    profile = await run_in_threadpool(load_profile)
    demo_pool = build_demo_bids()

    reports: list[BidSyncSourceReport] = []
    collected: list[dict[str, Any]] = []
    g2b_keyword = req.keywords[0].strip() if req.keywords else ""

    job_token = acquire_or_409("bids:sync", "공고 수집")
    try:
        for src in req.sources:
            spec = _SOURCE_SPECS.get(src)
            if spec is None:
                continue
            key_field, label = spec
            has_key = bool((cfg.get(key_field) or "").strip())

            if has_key:
                try:
                    if src == "g2b":
                        items, chunks, msg = await fetch_g2b_bids(days_back=req.days_back, keyword=g2b_keyword)
                    elif src == "seis":
                        items, msg = await fetch_seis_notices()
                        chunks = 1
                    else:
                        items, msg = await fetch_bizinfo_notices()
                        chunks = 1
                except Exception as exc:
                    logger.warning("%s 수집 실패: %s", label, exc)
                    await run_in_threadpool(update_sync_state, src, f"error: {exc}", 0)
                    reports.append(
                        BidSyncSourceReport(
                            source=src,
                            failed=True,
                            message=f"{label} 수집 실패: {exc} (샘플 공고로 대체하지 않았습니다)",
                        )
                    )
                    continue
                await run_in_threadpool(update_sync_state, src, "ok", len(items))
                collected.extend(items)
                reports.append(
                    BidSyncSourceReport(
                        source=src, fetched_count=len(items), saved_count=len(items),
                        chunks_requested=chunks, used_demo=False, message=msg,
                    )
                )
                continue

            if req.use_demo_if_no_key:
                d_items = [d for d in demo_pool if d["source"] == src]
                await run_in_threadpool(update_sync_state, src, "demo", len(d_items))
                collected.extend(d_items)
                reports.append(
                    BidSyncSourceReport(
                        source=src, fetched_count=len(d_items), saved_count=len(d_items),
                        chunks_requested=1, used_demo=True,
                        message=f"{label} API 키 미설정 — 샘플 공고 {len(d_items)}건 표시(실제 공고 아님)",
                    )
                )
            else:
                reports.append(BidSyncSourceReport(source=src, message=f"{label} API 키 미설정"))

        saved, scored = await _upsert_and_score_bids(collected, profile)
    finally:
        release("bids:sync", job_token)

    return BidSyncResult(
        total_fetched=len(collected),
        total_saved=saved,
        scored_count=scored,
        reports=reports,
    )


@router.post("/seed-demo", response_model=BidSyncResult)
async def seed_demo_bids() -> BidSyncResult:
    profile = await run_in_threadpool(load_profile)
    demo_pool = build_demo_bids()
    job_token = acquire_or_409("bids:sync", "공고 수집")
    try:
        saved, scored = await _upsert_and_score_bids(demo_pool, profile)
    finally:
        release("bids:sync", job_token)
    for src in ("g2b", "seis", "bizinfo"):
        cnt = sum(1 for d in demo_pool if d["source"] == src)
        update_sync_state(src, "demo", cnt)
    return BidSyncResult(
        total_fetched=len(demo_pool),
        total_saved=saved,
        scored_count=scored,
        reports=[
            BidSyncSourceReport(
                source="all",
                fetched_count=len(demo_pool),
                saved_count=saved,
                chunks_requested=1,
                used_demo=True,
                message="2026 사회적기업 맞춤 샘플 공고 12건 로드 완료",
            )
        ],
    )


@router.get("/quota", response_model=list[QuotaStatus])
def list_api_quotas() -> list[QuotaStatus]:
    return [QuotaStatus(**q) for q in get_quota_statuses()]


@router.get("", response_model=list[BidItem])
def list_bids(
    source: str = Query(default="all"),
    status: str = Query(default="all"),
    privilege_only: bool = Query(default=False),
    q: str = Query(default=""),
) -> list[BidItem]:
    clauses: list[str] = []
    params: list[Any] = []
    if source != "all":
        clauses.append("source = ?")
        params.append(source)
    if status != "all":
        clauses.append("status = ?")
        params.append(status)
    if privilege_only:
        clauses.append("privilege_tier IN ('private_1_quote', 'private_2_quotes')")
    if q.strip():
        clauses.append("(title LIKE ? OR agency LIKE ? OR bid_no LIKE ?)")
        pat = f"%{q.strip()}%"
        params.extend([pat, pat, pat])

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM bids {where_sql} ORDER BY rule_passed DESC, COALESCE(ai_score, 0) DESC, id DESC",
            params,
        ).fetchall()
    return [_row_to_bid_item(r) for r in rows]


@router.get("/{bid_id}", response_model=BidItem)
def get_bid(bid_id: int) -> BidItem:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다.")
    return _row_to_bid_item(row)


@router.post("/{bid_id}/decision", response_model=BidDecisionResponse)
async def decide_bid(bid_id: int, req: BidDecisionRequest) -> BidDecisionResponse:
    """Human-in-the-Loop 게이트: 승인 건만 온디맨드 첨부파일 다운로드 + RFP Go/No-Go 파싱을 수행한다."""
    if req.status not in {"pending", "approved", "hold", "excluded"}:
        raise HTTPException(status_code=400, detail="유효하지 않은 검토 상태입니다.")

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="공고를 찾을 수 없습니다.")

    now = _now_iso()
    downloaded_paths: list[str] = []
    created_proposal_id: int | None = None
    existing_atts = json.loads(row["attachments_json"] or "[]")
    rfp_analysis_dict = json.loads(row["rfp_analysis_json"] or "{}")

    if req.status == "excluded":
        reason = req.exclusion_reason.strip() or "기타 사유"
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE bids
                SET status = 'excluded', exclusion_reason = ?, exclusion_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, req.exclusion_note, now, bid_id),
            )
            conn.execute(
                """
                INSERT INTO exclusion_feedback (bid_id, bid_title, agency, reason_tag, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bid_id, row["title"], row["agency"], reason, req.exclusion_note, now),
            )
            updated = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
        return BidDecisionResponse(bid=_row_to_bid_item(updated))

    if req.status in {"pending", "hold"}:
        with get_conn() as conn:
            conn.execute(
                "UPDATE bids SET status = ?, updated_at = ? WHERE id = ?",
                (req.status, now, bid_id),
            )
            updated = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()
        return BidDecisionResponse(bid=_row_to_bid_item(updated))

    # req.status == "approved": 온디맨드 첨부 다운로드 + Go/No-Go 파싱 + 제안서 초안 공간 준비
    # 샘플 RFP 생성은 앱 내장 샘플 공고에만 적용한다. (예전에는 http 첨부가 없는 실제 공고도
    # 샘플로 취급해 가짜 RFP로 Go/No-Go·목차를 만들었다.)
    if bool(row["is_demo"]):
        new_atts = await run_in_threadpool(materialize_demo_attachments, row["bid_no"], existing_atts)
    elif row["source"] == "g2b":
        new_atts = await download_g2b_attachments(row["bid_no"], row["bid_ord"], existing_atts)
    elif row["source"] == "bizinfo":
        new_atts = await extract_and_download_bizinfo_attachments(row["bid_no"], row["url"])
    else:
        new_atts = existing_atts

    # 첨부 RFP 파싱·Go/No-Go 분석·DB 저장은 동기 I/O·CPU 작업이므로 이벤트 루프 밖에서 실행
    return await run_in_threadpool(_finalize_bid_approval, row, bid_id, new_atts, rfp_analysis_dict, now)

def _finalize_bid_approval(
    row: Any, bid_id: int, new_atts: list[dict[str, Any]], rfp_analysis_dict: dict[str, Any], now: str
) -> BidDecisionResponse:
    """승인 공고의 첨부 RFP를 파싱해 Go/No-Go 분석을 저장하고, 연결 제안서가 없으면 생성한다."""
    created_proposal_id: int | None = None
    primary_file = next((a.get("local_path") for a in new_atts if a.get("local_path") and Path(a["local_path"]).exists()), "")
    downloaded_paths = [a["local_path"] for a in new_atts if a.get("local_path")]

    profile = load_profile()
    parsed_doc = None
    scoring_list = []
    outline = []
    scenario = "B"

    if primary_file:
        ext = Path(primary_file).suffix.lower()
        try:
            if ext in {".hwpx", ".hwp"}:
                parsed_doc = parse_hwpx_or_hwp(primary_file)
            elif ext == ".pdf":
                parsed_doc = parse_pdf(primary_file)
            if parsed_doc:
                scoring_list = extract_scoring_criteria(parsed_doc)
                scenario = detect_scenario(parsed_doc, scoring_list)
                outline = build_outline_from_document(
                    parsed_doc,
                    scoring_list,
                    scenario,
                    estimated_price=int(row["estimated_price"] or 0),
                    category=row["category"],
                )
                rfp_obj = build_rfp_summary_and_gonogo(
                    parsed_doc,
                    scoring_list,
                    profile,
                    bid_meta={
                        "estimated_price": row["estimated_price"],
                        "region_limit": row["region_limit"],
                        "is_sme_competing": bool(row["is_sme_competing"]),
                    },
                )
                rfp_analysis_dict = rfp_obj.model_dump()
        except Exception as exc:
            logger.warning("승인 공고 RFP 파싱 경고(%s): %s", primary_file, exc)

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE bids
            SET status = 'approved', attachments_json = ?, rfp_analysis_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(new_atts, ensure_ascii=False),
                json.dumps(rfp_analysis_dict, ensure_ascii=False),
                now,
                bid_id,
            ),
        )
        # 이미 이 공고에 연결된 제안서가 없으면 생성
        ex_prop = conn.execute("SELECT id FROM proposals WHERE bid_id = ?", (bid_id,)).fetchone()
        if ex_prop:
            created_proposal_id = int(ex_prop["id"])
        else:
            p_cur = conn.execute(
                """
                INSERT INTO proposals (
                    bid_id, title, scenario, source_file_path, source_file_type,
                    conversion_warnings_json, rfp_summary_json, scoring_table_json,
                    outline_json, sections_content_json, citations_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
                """,
                (
                    bid_id,
                    row["title"],
                    scenario,
                    primary_file,
                    Path(primary_file).suffix.lower().lstrip(".") if primary_file else "",
                    json.dumps(parsed_doc.conversion_warnings if parsed_doc else [], ensure_ascii=False),
                    json.dumps(rfp_analysis_dict, ensure_ascii=False),
                    json.dumps([s.model_dump() for s in scoring_list], ensure_ascii=False),
                    json.dumps([o.model_dump() for o in outline], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            created_proposal_id = int(p_cur.lastrowid)
        updated = conn.execute("SELECT * FROM bids WHERE id = ?", (bid_id,)).fetchone()

    return BidDecisionResponse(
        bid=_row_to_bid_item(updated),
        downloaded_files=downloaded_paths,
        created_proposal_id=created_proposal_id,
    )
