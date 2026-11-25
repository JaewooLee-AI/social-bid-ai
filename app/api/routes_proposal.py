"""제안서 워크스페이스 API: 시나리오 A/B/C 목차 관리, SSE 실시간 스트리밍, HWPX 패치·DOCX·MD 내보내기."""

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

from app.ai.job_guard import acquire_or_409, release
from app.ai.llm_budget import BudgetExceededError, ensure_budget_available
from app.ai.proposal_gen import stream_proposal_section
from app.api.deps import verify_csrf_token
from app.config import get_exports_dir, load_profile
from app.db import get_conn
from app.documents.export_engine import export_docx, export_markdown
from app.documents.hwpx_engine import create_hwpx_from_sections, parse_hwpx_or_hwp, patch_hwpx
from app.documents.pdf_engine import parse_pdf
from app.documents.rfp_analyzer import (
    build_outline_from_document,
    build_rfp_summary_and_gonogo,
    detect_scenario,
    extract_scoring_criteria,
)
from app.schemas.docs import OutlineNode, ParsedDocument, ScoringCriterion
from app.schemas.proposal import (
    ExportResult,
    ProposalCreateRequest,
    ProposalDetail,
    ProposalExportRequest,
    ProposalGenerateRequest,
    ProposalOutlineRequest,
    ProposalUpdateRequest,
)

router = APIRouter(prefix="/api/proposal", tags=["proposal"], dependencies=[Depends(verify_csrf_token)])


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w가-힣\-_. ]", "_", name).strip()
    return cleaned[:60] or "proposal"


def _row_to_proposal(row: Any) -> ProposalDetail:
    return ProposalDetail(
        id=row["id"],
        bid_id=row["bid_id"],
        title=row["title"],
        scenario=row["scenario"],
        source_file_path=row["source_file_path"],
        source_file_type=row["source_file_type"],
        conversion_warnings=json.loads(row["conversion_warnings_json"] or "[]"),
        rfp_summary=json.loads(row["rfp_summary_json"] or "{}"),
        scoring_table=[ScoringCriterion(**s) for s in json.loads(row["scoring_table_json"] or "[]")],
        outline=[OutlineNode(**o) for o in json.loads(row["outline_json"] or "[]")],
        sections_content=json.loads(row["sections_content_json"] or "{}"),
        citations=json.loads(row["citations_json"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[ProposalDetail])
def list_proposals() -> list[ProposalDetail]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM proposals ORDER BY updated_at DESC, id DESC").fetchall()
    return [_row_to_proposal(r) for r in rows]


@router.post("", response_model=ProposalDetail)
def create_proposal(req: ProposalCreateRequest) -> ProposalDetail:
    now = _now_iso()
    scenario = req.scenario
    source_path = req.source_file_path
    source_type = Path(source_path).suffix.lower().lstrip(".") if source_path else ""
    warnings: list[str] = []
    rfp_summary: dict[str, Any] = {}
    scoring_table: list[dict[str, Any]] = []
    outline: list[dict[str, Any]] = []

    if source_path and Path(source_path).exists():
        p = Path(source_path)
        parsed = parse_hwpx_or_hwp(p) if p.suffix.lower() in {".hwpx", ".hwp"} else parse_pdf(p)
        warnings = parsed.conversion_warnings
        scoring = extract_scoring_criteria(parsed)
        scenario = detect_scenario(parsed, scoring)
        outline_nodes = build_outline_from_document(parsed, scoring, scenario)
        rfp_summary = build_rfp_summary_and_gonogo(parsed, scoring, load_profile()).model_dump()
        scoring_table = [s.model_dump() for s in scoring]
        outline = [o.model_dump() for o in outline_nodes]
    else:
        dummy_doc = ParsedDocument(file_path="", filename=req.title, file_type="none")
        outline_nodes = build_outline_from_document(dummy_doc, [], scenario or "B")
        outline = [o.model_dump() for o in outline_nodes]

    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO proposals (
                bid_id, title, scenario, source_file_path, source_file_type,
                conversion_warnings_json, rfp_summary_json, scoring_table_json,
                outline_json, sections_content_json, citations_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?)
            """,
            (
                req.bid_id,
                req.title,
                scenario,
                source_path,
                source_type,
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(rfp_summary, ensure_ascii=False),
                json.dumps(scoring_table, ensure_ascii=False),
                json.dumps(outline, ensure_ascii=False),
                now,
                now,
            ),
        )
        new_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (new_id,)).fetchone()
    return _row_to_proposal(row)


@router.get("/{proposal_id}", response_model=ProposalDetail)
def get_proposal(proposal_id: int) -> ProposalDetail:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="제안서를 찾을 수 없습니다.")
    return _row_to_proposal(row)


@router.put("/{proposal_id}", response_model=ProposalDetail)
def update_proposal(proposal_id: int, req: ProposalUpdateRequest) -> ProposalDetail:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="제안서를 찾을 수 없습니다.")
        title = req.title if req.title is not None else row["title"]
        scenario = req.scenario if req.scenario is not None else row["scenario"]
        outline_json = (
            json.dumps([o.model_dump() for o in req.outline], ensure_ascii=False)
            if req.outline is not None
            else row["outline_json"]
        )
        sections_json = (
            json.dumps(req.sections_content, ensure_ascii=False)
            if req.sections_content is not None
            else row["sections_content_json"]
        )
        conn.execute(
            """
            UPDATE proposals
            SET title = ?, scenario = ?, outline_json = ?, sections_content_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, scenario, outline_json, sections_json, _now_iso(), proposal_id),
        )
        updated = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _row_to_proposal(updated)


@router.delete("/{proposal_id}")
def delete_proposal(proposal_id: int) -> dict[str, bool]:
    with get_conn() as conn:
        conn.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
    return {"ok": True}


@router.post("/outline", response_model=list[OutlineNode])
def regenerate_outline(req: ProposalOutlineRequest) -> list[OutlineNode]:
    """시나리오(A/B/C) 또는 추정가격(5천만 이하 수의계약 경량) 기준으로 목차를 자동 재구성한다."""
    src = req.source_file_path
    scoring: list[ScoringCriterion] = []
    parsed: ParsedDocument
    if src and Path(src).exists():
        p = Path(src)
        parsed = parse_hwpx_or_hwp(p) if p.suffix.lower() in {".hwpx", ".hwp"} else parse_pdf(p)
        scoring = extract_scoring_criteria(parsed)
    else:
        parsed = ParsedDocument(file_path="", filename=req.title or "제안서", file_type="none")

    scenario = req.scenario if req.scenario in {"A", "B", "C"} else detect_scenario(parsed, scoring)
    nodes = build_outline_from_document(
        parsed,
        scoring,
        scenario,
        estimated_price=req.estimated_price,
        category=req.category,
    )

    if req.proposal_id is not None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE proposals SET scenario = ?, outline_json = ?, updated_at = ? WHERE id = ?",
                (scenario, json.dumps([n.model_dump() for n in nodes], ensure_ascii=False), _now_iso(), req.proposal_id),
            )
    return nodes


def _sse_event(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/generate")
async def generate_proposal_sse(req: ProposalGenerateRequest) -> StreamingResponse:
    """확정된 목차의 섹션 단위로 로컬 RAG 검색 + SSE 실시간 스트리밍 생성을 실행한다."""
    # DB 조회·예산 집계는 동기 I/O이므로 이벤트 루프 밖에서 실행
    prop = await run_in_threadpool(get_proposal, req.proposal_id)
    target_nodes = [
        n for n in prop.outline if (not req.section_ids or n.id in req.section_ids)
    ]

    await run_in_threadpool(ensure_budget_available)  # 예산 초과 시 402로 즉시 거절
    job_key = f"proposal:{prop.id}"
    job_token = acquire_or_409(job_key, "이 제안서의 AI 초안 생성")

    async def event_stream() -> AsyncIterator[str]:
        try:
            async with aclosing(_proposal_event_stream(prop, target_nodes, req.model_tier)) as gen:
                async for chunk in gen:
                    yield chunk
        except BudgetExceededError as exc:
            # 생성 도중 예산에 도달: 완료된 섹션까지만 저장된 상태로 중단을 알린다.
            yield _sse_event("error", {"message": str(exc), "code": "budget_exceeded"})
        finally:
            # 정상 종료·예외·클라이언트 연결 끊김(CancelledError) 모두에서 잠금 해제
            release(job_key, job_token)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", background=BackgroundTask(release, job_key, job_token)
    )


def _save_proposal_sections(
    proposal_id: int, sections_content: dict[str, str], citations_map: dict[str, Any]
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE proposals
            SET sections_content_json = ?, citations_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(sections_content, ensure_ascii=False),
                json.dumps(citations_map, ensure_ascii=False),
                _now_iso(),
                proposal_id,
            ),
        )


async def _proposal_event_stream(
    prop: ProposalDetail, target_nodes: list[OutlineNode], model_tier: str
) -> AsyncIterator[str]:
    sections_content = dict(prop.sections_content)
    citations_map = dict(prop.citations)

    for node in target_nodes:
        yield _sse_event("section_start", {"section_id": node.id, "title": f"{node.number}. {node.title}"})
        cites, stream = await stream_proposal_section(
            prop.title,
            prop.scenario,
            node,
            prop.rfp_summary,
            model_tier=model_tier,
        )
        citations_map[node.id] = cites
        yield _sse_event("citations", {"section_id": node.id, "citations": cites})

        buf: list[str] = []
        async with aclosing(stream) as sec_stream:
            async for chunk in sec_stream:
                buf.append(chunk)
                yield _sse_event("delta", {"section_id": node.id, "text": chunk})

        full_section = "".join(buf)
        sections_content[node.id] = full_section
        await run_in_threadpool(_save_proposal_sections, prop.id, dict(sections_content), dict(citations_map))
        yield _sse_event("section_done", {"section_id": node.id, "length": len(full_section)})

    yield _sse_event("done", {"proposal_id": prop.id})


def _build_export_sections(prop: ProposalDetail) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for node in prop.outline:
        content = prop.sections_content.get(node.id) or f"[{node.number}. {node.title} — 초안 미작성]"
        out.append({"title": f"{node.number}. {node.title}", "content": content})
    return out


@router.post("/export-hwpx", response_model=ExportResult)
def export_proposal_hwpx(req: ProposalExportRequest) -> ExportResult:
    """시나리오 C(지정 양식)일 경우 Patch Mode로 원본 서식을 보존하며 저장하고, 그 외에는 새 HWPX를 생성한다."""
    prop = get_proposal(req.proposal_id)
    out_path = req.output_path.strip() or str(get_exports_dir() / f"{_safe_filename(prop.title)}.hwpx")

    # 원본 HWPX/HWP가 있고 슬롯 매핑(slot_id)이 존재하면 Patch Mode 실행
    slot_updates: dict[str, str] = {}
    for node in prop.outline:
        if node.slot_id and prop.sections_content.get(node.id):
            slot_updates[node.slot_id] = prop.sections_content[node.id]

    if prop.source_file_path and Path(prop.source_file_path).exists() and slot_updates and Path(prop.source_file_path).suffix.lower() in {".hwpx", ".hwp"}:
        res = patch_hwpx(prop.source_file_path, slot_updates, out_path)
        return ExportResult(
            ok=True,
            output_path=res["output_path"],
            format="hwpx",
            mode=res.get("mode", "patch"),
            warnings=res.get("warnings", []),
        )

    res = create_hwpx_from_sections(
        prop.title,
        f"시나리오 {prop.scenario} | 사회적기업 공공입찰 제안서 초안",
        _build_export_sections(prop),
        out_path,
    )
    return ExportResult(
        ok=True,
        output_path=res["output_path"],
        format="hwpx",
        mode=res.get("mode", "rebuild"),
        warnings=res.get("warnings", []),
    )


@router.post("/export-docx", response_model=ExportResult)
def export_proposal_docx(req: ProposalExportRequest) -> ExportResult:
    prop = get_proposal(req.proposal_id)
    out_path = req.output_path.strip() or str(get_exports_dir() / f"{_safe_filename(prop.title)}.docx")
    res = export_docx(
        prop.title,
        f"시나리오 {prop.scenario} | 사회적기업 공공입찰 제안서 초안",
        _build_export_sections(prop),
        out_path,
    )
    return ExportResult(ok=True, output_path=res["output_path"], format="docx", mode="docx")


@router.post("/export-md", response_model=ExportResult)
def export_proposal_md(req: ProposalExportRequest) -> ExportResult:
    prop = get_proposal(req.proposal_id)
    out_path = req.output_path.strip() or str(get_exports_dir() / f"{_safe_filename(prop.title)}.md")
    res = export_markdown(
        prop.title,
        f"시나리오 {prop.scenario} | 사회적기업 공공입찰 제안서 초안",
        _build_export_sections(prop),
        out_path,
    )
    return ExportResult(ok=True, output_path=res["output_path"], format="md", mode="markdown")
