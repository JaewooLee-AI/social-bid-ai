"""Pain Point·수익창출 중심 소셜 사업계획서 위저드(SBMC, 단위경제성, SVI 14개 지표, 레드플래그 점검) API."""

from __future__ import annotations

import json
import re
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.ai.bizplan_gen import (
    apply_redflag_remediation_async,
    calculate_unit_economics,
    evaluate_svi,
    get_track_sections,
    run_redflag_audit,
    stream_bizplan_section,
)
from app.ai.job_guard import acquire_or_409, release
from app.ai.llm_budget import BudgetExceededError, ensure_budget_available
from app.api.deps import verify_csrf_token
from app.config import get_exports_dir
from app.db import get_conn
from app.documents.export_engine import export_docx, export_markdown
from app.documents.hwpx_engine import create_hwpx_from_sections, parse_hwpx_or_hwp, patch_hwpx
from app.schemas.bizplan import (
    AuditReport,
    BizPlanAuditRequest,
    BizPlanCanvasSaveRequest,
    BizPlanCreateRequest,
    BizPlanDetail,
    BizPlanExportRequest,
    BizPlanGenerateRequest,
    BizPlanRemediateRequest,
    BizPlanUpdateRequest,
    SocialBMCanvas,
    SviAssessmentResult,
    SviScoreRequest,
    UnitEconomicsInput,
    UnitEconomicsResult,
)
from app.schemas.proposal import ExportResult

router = APIRouter(prefix="/api/bizplan", tags=["bizplan"], dependencies=[Depends(verify_csrf_token)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_filename(name: str) -> str:
    return (re.sub(r"[^\w가-힣\-_. ]", "_", name).strip()[:60] or "bizplan")


def _default_canvas() -> SocialBMCanvas:
    return SocialBMCanvas(
        problem_statement="고령자·취약계층의 디지털 소외 및 공공 복지기관의 현장 운영 인력 부족",
        beneficiary="독거어르신 및 취약계층 교육 참여자(취약계층 매니저 정규 고용 포함)",
        payer="지자체·공공기관 구매담당자 및 대기업 ESG 사회공헌 담당자",
        beneficiary_equals_payer=False,
        existing_alternatives_limit="일반 영리 교육업체는 1회성 행사 후 사후관리 부재, 지자체 자체 운영은 전담 인력 부족",
        evidence_sources="통계청 2025 고령자 통계, 성동구청 시범사업 만족도 96.4점(2025)",
        tam_desc="국내 공공 우선구매 및 사회서비스 시장 약 2.4조 원",
        sam_desc="지자체·공공기관 5천만 원 이하 수의계약 진입 가능 돌봄·교육 시장 약 1,800억 원",
        som_desc="수도권 30개 공공기관·기업 ESG 팀 연간 반복 계약(객단가 12만 원 기준)",
        solution_mechanism="취약계층(경력보유여성)을 디지털 돌봄 매니저로 양성·고용하여 공공기관 현장에 표준 커리큘럼과 함께 파견",
        revenue_formula_sentence="우리는 [공공기관·기업 ESG 담당자]에게 [취약계층 매니저 기반 디지털 돌봄 패키지]를 제공하고, [1인당 월 120,000원 구독·위탁 과금]으로 수익을 창출하여 [취약계층 정규직 일자리 확대]를 지속합니다.",
        revenue_channels=["B2G 공공우선구매(5천만 수의계약)", "B2B ESG 납품"],
        unit_price=120_000,
        target_customers_monthly=80,
        conversion_rate_pct=25.0,
        variable_cost_per_unit=45_000,
        fixed_cost_monthly=4_500_000,
        social_purpose_type="job_creation",
        svi_scores={
            "svi_01": 9, "svi_02": 9, "svi_03": 8, "svi_04": 4, "svi_05": 4,
            "svi_06": 4, "svi_07": 4, "svi_08": 4, "svi_09": 4, "svi_10": 8,
            "svi_11": 8, "svi_12": 4, "svi_13": 4, "svi_14": 8,
        },
        democratic_governance_plan="근로자 대표 2인·지역 복지 전문가 1인·경영진 2인으로 구성된 월례 운영위원회 및 분기별 이사회 회의록 증빙",
        execution_evidence_notes="성동구청 시범사업 4,800만 원 수행 완료(만족도 96.4점), 수요기관 구매의향서(LoI) 3건 확보",
        competitor_notes="일반 영리 교육업체 vs 지자체 자체 수행 vs 당사(우선구매 120% 인정 + 취약계층 전담)",
    )


def _row_to_bizplan(row: Any) -> BizPlanDetail:
    canvas_raw = json.loads(row["canvas_json"] or "{}")
    canvas = SocialBMCanvas(**canvas_raw) if canvas_raw else _default_canvas()
    ue_raw = json.loads(row["unit_economics_json"] or "{}")
    svi_raw = json.loads(row["svi_assessment_json"] or "{}")
    audit_raw = json.loads(row["audit_report_json"] or "{}")

    ue = UnitEconomicsResult(**ue_raw) if ue_raw else calculate_unit_economics(
        UnitEconomicsInput(
            unit_price=canvas.unit_price,
            target_customers_monthly=canvas.target_customers_monthly,
            conversion_rate_pct=canvas.conversion_rate_pct,
            variable_cost_per_unit=canvas.variable_cost_per_unit,
            fixed_cost_monthly=canvas.fixed_cost_monthly,
        )
    )
    svi = SviAssessmentResult(**svi_raw) if svi_raw else evaluate_svi(canvas.svi_scores)
    audit = AuditReport(**audit_raw) if audit_raw and "overall_score" in audit_raw else None

    return BizPlanDetail(
        id=row["id"],
        title=row["title"],
        output_track=row["output_track"],
        template_hwpx_path=row["template_hwpx_path"],
        canvas=canvas,
        unit_economics=ue,
        svi_assessment=svi,
        sections_content=json.loads(row["sections_content_json"] or "{}"),
        audit_report=audit,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[BizPlanDetail])
def list_bizplans() -> list[BizPlanDetail]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM bizplans ORDER BY updated_at DESC, id DESC").fetchall()
    return [_row_to_bizplan(r) for r in rows]


@router.post("", response_model=BizPlanDetail)
def create_bizplan(req: BizPlanCreateRequest) -> BizPlanDetail:
    now = _now_iso()
    canvas = _default_canvas()
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

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO bizplans (
                title, output_track, template_hwpx_path, canvas_json,
                unit_economics_json, svi_assessment_json, sections_content_json,
                audit_report_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
            """,
            (
                req.title,
                req.output_track,
                req.template_hwpx_path,
                json.dumps(canvas.model_dump(), ensure_ascii=False),
                json.dumps(ue.model_dump(), ensure_ascii=False),
                json.dumps(svi.model_dump(), ensure_ascii=False),
                now,
                now,
            ),
        )
        new_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM bizplans WHERE id = ?", (new_id,)).fetchone()
    return _row_to_bizplan(row)


@router.get("/{bizplan_id}", response_model=BizPlanDetail)
def get_bizplan(bizplan_id: int) -> BizPlanDetail:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bizplans WHERE id = ?", (bizplan_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="사업계획서를 찾을 수 없습니다.")
    return _row_to_bizplan(row)


@router.put("/{bizplan_id}", response_model=BizPlanDetail)
def update_bizplan(bizplan_id: int, req: BizPlanUpdateRequest) -> BizPlanDetail:
    """사용자가 직접 수정한 사업계획서 섹션 본문·캔버스를 저장하고 레드플래그 점수를 즉시 재계산한다."""
    bp = get_bizplan(bizplan_id)
    next_title = req.title if req.title is not None else bp.title
    next_track = req.output_track if req.output_track is not None else bp.output_track
    next_tpl = req.template_hwpx_path if req.template_hwpx_path is not None else bp.template_hwpx_path
    next_canvas = req.canvas if req.canvas is not None else bp.canvas
    next_sections = req.sections_content if req.sections_content is not None else bp.sections_content

    ue = calculate_unit_economics(
        UnitEconomicsInput(
            unit_price=next_canvas.unit_price,
            target_customers_monthly=next_canvas.target_customers_monthly,
            conversion_rate_pct=next_canvas.conversion_rate_pct,
            variable_cost_per_unit=next_canvas.variable_cost_per_unit,
            fixed_cost_monthly=next_canvas.fixed_cost_monthly,
        )
    )
    svi = evaluate_svi(next_canvas.svi_scores)
    audit = run_redflag_audit(next_canvas, next_sections) if next_sections else bp.audit_report

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE bizplans
            SET title = ?, output_track = ?, template_hwpx_path = ?,
                canvas_json = ?, unit_economics_json = ?, svi_assessment_json = ?,
                sections_content_json = ?, audit_report_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_title,
                next_track,
                next_tpl,
                json.dumps(next_canvas.model_dump(), ensure_ascii=False),
                json.dumps(ue.model_dump(), ensure_ascii=False),
                json.dumps(svi.model_dump(), ensure_ascii=False),
                json.dumps(next_sections, ensure_ascii=False),
                json.dumps(audit.model_dump(), ensure_ascii=False) if audit else "{}",
                _now_iso(),
                bizplan_id,
            ),
        )
    return get_bizplan(bizplan_id)


@router.delete("/{bizplan_id}")
def delete_bizplan(bizplan_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        conn.execute("DELETE FROM bizplans WHERE id = ?", (bizplan_id,))
    return {"ok": True}


@router.post("/unit-economics", response_model=UnitEconomicsResult)
def preview_unit_economics(inp: UnitEconomicsInput) -> UnitEconomicsResult:
    return calculate_unit_economics(inp)


@router.post("/svi-score", response_model=SviAssessmentResult)
def preview_svi_score(req: SviScoreRequest) -> SviAssessmentResult:
    return evaluate_svi(req.scores)


@router.post("/canvas", response_model=BizPlanDetail)
def save_bizplan_canvas(req: BizPlanCanvasSaveRequest) -> BizPlanDetail:
    bp = get_bizplan(req.bizplan_id)
    ue = calculate_unit_economics(
        UnitEconomicsInput(
            unit_price=req.canvas.unit_price,
            target_customers_monthly=req.canvas.target_customers_monthly,
            conversion_rate_pct=req.canvas.conversion_rate_pct,
            variable_cost_per_unit=req.canvas.variable_cost_per_unit,
            fixed_cost_monthly=req.canvas.fixed_cost_monthly,
        )
    )
    svi = evaluate_svi(req.canvas.svi_scores)

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE bizplans
            SET title = ?, output_track = ?, template_hwpx_path = ?,
                canvas_json = ?, unit_economics_json = ?, svi_assessment_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                req.title if req.title is not None else bp.title,
                req.output_track if req.output_track is not None else bp.output_track,
                req.template_hwpx_path if req.template_hwpx_path is not None else bp.template_hwpx_path,
                json.dumps(req.canvas.model_dump(), ensure_ascii=False),
                json.dumps(ue.model_dump(), ensure_ascii=False),
                json.dumps(svi.model_dump(), ensure_ascii=False),
                _now_iso(),
                bp.id,
            ),
        )
    return get_bizplan(bp.id)


@router.post("/generate")
async def generate_bizplan_sse(req: BizPlanGenerateRequest) -> StreamingResponse:
    # DB 조회·예산 집계·HWPX 양식 파싱은 동기 I/O이므로 이벤트 루프 밖에서 실행
    bp = await run_in_threadpool(get_bizplan, req.bizplan_id)
    track = req.output_track or bp.output_track
    await run_in_threadpool(ensure_budget_available)  # 예산 초과 시 402로 즉시 거절
    job_key = f"bizplan:{bp.id}"
    job_token = acquire_or_409(job_key, "이 사업계획서의 AI 생성·보완")
    try:
        sections = await run_in_threadpool(_resolve_generate_sections, bp, track, req.section_keys)
    except Exception:
        release(job_key, job_token)
        raise

    async def event_stream() -> AsyncIterator[str]:
        try:
            async with aclosing(_bizplan_event_stream(bp, track, sections, req.model_tier)) as gen:
                async for chunk in gen:
                    yield chunk
        except BudgetExceededError as exc:
            # 생성 도중 예산에 도달: 완료된 섹션까지만 저장된 상태로 중단을 알린다.
            yield f"event: error\ndata: {json.dumps({'message': str(exc), 'code': 'budget_exceeded'}, ensure_ascii=False)}\n\n"
        finally:
            # 정상 종료·예외·클라이언트 연결 끊김(CancelledError) 모두에서 잠금 해제
            release(job_key, job_token)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", background=BackgroundTask(release, job_key, job_token)
    )


def _resolve_generate_sections(bp: BizPlanDetail, track: str, section_keys: list[str] | None) -> list[dict[str, str]]:
    if track == "hwpx_patch" and bp.template_hwpx_path and Path(bp.template_hwpx_path).exists():
        parsed = parse_hwpx_or_hwp(bp.template_hwpx_path)
        sections = [
            {"key": s.slot_id, "title": f"{s.location_label} ({s.prompt_hint[:40]})"}
            for s in parsed.slots
        ] or get_track_sections("psst")
    else:
        sections = get_track_sections(track)

    if section_keys:
        sections = [s for s in sections if s["key"] in section_keys]
    return sections


def _save_bizplan_sections(bizplan_id: int, track: str, sections_content: dict[str, str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE bizplans SET output_track = ?, sections_content_json = ?, updated_at = ? WHERE id = ?",
            (track, json.dumps(sections_content, ensure_ascii=False), _now_iso(), bizplan_id),
        )


def _audit_and_save(bizplan_id: int, canvas: SocialBMCanvas, sections_content: dict[str, str]) -> AuditReport:
    audit = run_redflag_audit(canvas, sections_content)
    with get_conn() as conn:
        conn.execute(
            "UPDATE bizplans SET audit_report_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(audit.model_dump(), ensure_ascii=False), _now_iso(), bizplan_id),
        )
    return audit


async def _bizplan_event_stream(
    bp: BizPlanDetail, track: str, sections: list[dict[str, str]], model_tier: str
) -> AsyncIterator[str]:
    sections_content = dict(bp.sections_content)
    for sec in sections:
        key = sec["key"]
        title = sec["title"]
        yield f"event: section_start\ndata: {json.dumps({'section_key': key, 'title': title}, ensure_ascii=False)}\n\n"
        buf: list[str] = []
        async with aclosing(
            stream_bizplan_section(bp.title, track, key, title, bp.canvas, model_tier=model_tier)
        ) as sec_stream:
            async for ch in sec_stream:
                buf.append(ch)
                yield f"event: delta\ndata: {json.dumps({'section_key': key, 'text': ch}, ensure_ascii=False)}\n\n"
        sections_content[key] = "".join(buf)
        await run_in_threadpool(_save_bizplan_sections, bp.id, track, dict(sections_content))
        yield f"event: section_done\ndata: {json.dumps({'section_key': key}, ensure_ascii=False)}\n\n"

    # 생성 완료 직후 심사관 레드플래그 자동 스캔
    audit = await run_in_threadpool(_audit_and_save, bp.id, bp.canvas, dict(sections_content))
    yield f"event: audit\ndata: {json.dumps(audit.model_dump(), ensure_ascii=False)}\n\n"
    yield f"event: done\ndata: {json.dumps({'bizplan_id': bp.id}, ensure_ascii=False)}\n\n"

@router.post("/audit", response_model=AuditReport)
def audit_bizplan(req: BizPlanAuditRequest) -> AuditReport:
    bp = get_bizplan(req.bizplan_id)
    audit = run_redflag_audit(bp.canvas, bp.sections_content)
    with get_conn() as conn:
        conn.execute(
            "UPDATE bizplans SET audit_report_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(audit.model_dump(), ensure_ascii=False), _now_iso(), bp.id),
        )
    return audit


@router.post("/remediate", response_model=BizPlanDetail)
async def remediate_bizplan_flag(req: BizPlanRemediateRequest) -> BizPlanDetail:
    bp = await run_in_threadpool(get_bizplan, req.bizplan_id)
    await run_in_threadpool(ensure_budget_available)
    job_key = f"bizplan:{bp.id}"
    job_token = acquire_or_409(job_key, "이 사업계획서의 AI 생성·보완")
    try:
        updated_sections = await apply_redflag_remediation_async(
            req.flag_code, req.section_key, bp.canvas, bp.sections_content
        )
        await run_in_threadpool(_save_remediation, bp.id, bp.canvas, updated_sections)
    finally:
        release(job_key, job_token)
    return await run_in_threadpool(get_bizplan, bp.id)


def _save_remediation(bizplan_id: int, canvas: SocialBMCanvas, updated_sections: dict[str, str]) -> None:
    audit = run_redflag_audit(canvas, updated_sections)
    with get_conn() as conn:
        conn.execute(
            "UPDATE bizplans SET sections_content_json = ?, audit_report_json = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(updated_sections, ensure_ascii=False),
                json.dumps(audit.model_dump(), ensure_ascii=False),
                _now_iso(),
                bizplan_id,
            ),
        )


def _map_sections_to_hwpx_slots(template_path: str, sections_content: dict[str, str]) -> dict[str, str]:
    """psst/pre_social 섹션 키를 지정 HWPX 양식의 슬롯 ID(t0r1c1 등)로 의미 기반 자동 매핑한다."""
    slot_key_re = re.compile(r"^(?:t\d+r\d+c\d+|p\d+)$")
    if any(slot_key_re.match(k) for k in sections_content.keys()):
        return {k: v for k, v in sections_content.items() if slot_key_re.match(k)}

    parsed = parse_hwpx_or_hwp(template_path)
    if not parsed.slots:
        return sections_content

    keyword_buckets = [
        (("문제", "고통", "pain", "배경", "필요성", "현황"), ("problem", "area_3", "area_2", "area_1")),
        (("해결", "메커니즘", "솔루션", "아이템", "차별", "solution"), ("solution", "area_4")),
        (("수익", "매출", "경제성", "bep", "시장", "성장", "자금", "판로", "scale"), ("scaleup", "area_5", "area_8")),
        (("팀", "조직", "svi", "사회적", "고용", "거버넌스", "의사결정", "환원", "로드맵", "team"), ("team", "area_6", "area_7")),
    ]

    slot_updates: dict[str, str] = {}
    used_section_keys: set[str] = set()

    for slot in parsed.slots:
        hint_text = f"{slot.location_label} {slot.prompt_hint}".lower()
        matched_key: str | None = None
        for kw_tuple, candidate_keys in keyword_buckets:
            if any(kw in hint_text for kw in kw_tuple):
                for ck in candidate_keys:
                    if ck in sections_content and sections_content[ck].strip():
                        matched_key = ck
                        break
            if matched_key:
                break
        if matched_key:
            slot_updates[slot.slot_id] = sections_content[matched_key]
            used_section_keys.add(matched_key)

    # 키워드 매칭이 안 된 슬롯은 남은 섹션을 순서대로 매핑
    remaining_sections = [
        (k, v) for k, v in sections_content.items() if k not in used_section_keys and v.strip()
    ]
    unfilled_slots = [s for s in parsed.slots if s.slot_id not in slot_updates]
    for slot, (_, content) in zip(unfilled_slots, remaining_sections):
        slot_updates[slot.slot_id] = content

    return slot_updates or sections_content


@router.post("/export", response_model=ExportResult)
def export_bizplan(req: BizPlanExportRequest) -> ExportResult:
    bp = get_bizplan(req.bizplan_id)
    ext = req.format if req.format in {"hwpx", "docx", "md"} else "hwpx"
    out_path = req.output_path.strip() or str(get_exports_dir() / f"{_safe_filename(bp.title)}.{ext}")
    sections_list = [{"title": k, "content": v} for k, v in bp.sections_content.items()]
    if not sections_list:
        raise HTTPException(status_code=400, detail="내보낼 사업계획서 초안이 없습니다. 먼저 초안을 생성하세요.")

    if ext == "hwpx":
        if bp.template_hwpx_path and Path(bp.template_hwpx_path).exists():
            slot_updates = _map_sections_to_hwpx_slots(bp.template_hwpx_path, bp.sections_content)
            res = patch_hwpx(bp.template_hwpx_path, slot_updates, out_path)
            return ExportResult(
                ok=True, output_path=res["output_path"], format="hwpx", mode=res.get("mode", "patch"), warnings=res.get("warnings", [])
            )
        res = create_hwpx_from_sections(bp.title, f"트랙: {bp.output_track}", sections_list, out_path)
        return ExportResult(ok=True, output_path=res["output_path"], format="hwpx", mode=res.get("mode", "rebuild"))
    if ext == "docx":
        res = export_docx(bp.title, f"트랙: {bp.output_track}", sections_list, out_path)
        return ExportResult(ok=True, output_path=res["output_path"], format="docx", mode="docx")
    res = export_markdown(bp.title, f"트랙: {bp.output_track}", sections_list, out_path)
    return ExportResult(ok=True, output_path=res["output_path"], format="md", mode="markdown")
