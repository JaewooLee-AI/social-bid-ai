"""문서 업로드, PyWebview 절대경로 직접 파싱(.hwpx/.hwp/.pdf) 및 샘플 양식 생성 API."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app.ai.llm_client import vision_ocr_page
from app.api.deps import verify_csrf_token
from app.config import get_downloads_dir, load_profile
from app.documents.hwpx_engine import create_sample_hwpx_template, parse_hwpx_or_hwp
from app.documents.pdf_engine import parse_pdf
from app.documents.rfp_analyzer import (
    build_outline_from_document,
    build_rfp_summary_and_gonogo,
    detect_scenario,
    extract_scoring_criteria,
)
from app.schemas.docs import DocumentUploadResponse, ParsePathRequest

router = APIRouter(prefix="/api/docs", tags=["docs"], dependencies=[Depends(verify_csrf_token)])


def _analyze_local_file(file_path: Path) -> DocumentUploadResponse:
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"파일을 찾을 수 없습니다: {file_path}")

    ext = file_path.suffix.lower()
    if ext in {".hwpx", ".hwp"}:
        parsed = parse_hwpx_or_hwp(file_path)
    elif ext == ".pdf":
        parsed = parse_pdf(file_path, vision_ocr_fn=vision_ocr_page)
    else:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다({ext}). .hwpx, .hwp, .pdf만 지원합니다.")

    scoring = extract_scoring_criteria(parsed)
    scenario = detect_scenario(parsed, scoring)
    outline = build_outline_from_document(parsed, scoring, scenario)
    rfp_summary = build_rfp_summary_and_gonogo(parsed, scoring, load_profile()).model_dump()

    return DocumentUploadResponse(
        document=parsed,
        detected_scenario=scenario,
        scoring_criteria=scoring,
        suggested_outline=outline,
        rfp_summary=rfp_summary,
    )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    estimated_price: int = Form(default=0),
) -> DocumentUploadResponse:
    """일반 브라우저/개발 환경용 멀티파트 파일 업로드 후 파싱."""
    fname = Path(file.filename or "uploaded_doc.hwpx").name

    def _save_and_analyze() -> DocumentUploadResponse:
        # 파일 저장·파싱·스캔 PDF Vision OCR(네트워크)은 모두 동기 작업이므로 이벤트 루프 밖에서 실행
        dest_dir = get_downloads_dir() / "manual_uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fname
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        res = _analyze_local_file(dest)
        if estimated_price > 0 and res.detected_scenario == "B":
            res.suggested_outline = build_outline_from_document(
                res.document, res.scoring_criteria, "B", estimated_price=estimated_price
            )
        return res

    return await run_in_threadpool(_save_and_analyze)


@router.post("/parse-path", response_model=DocumentUploadResponse)
def parse_document_by_path(req: ParsePathRequest) -> DocumentUploadResponse:
    """PyWebview 드래그앤드롭(pywebviewFullPath) 또는 네이티브 파일 대화상자의 절대경로를 중복 복사 없이 파싱."""
    return _analyze_local_file(Path(req.file_path))


@router.get("/sample-template", response_model=DocumentUploadResponse)
def generate_and_parse_sample_template(kind: str = Query(default="form")) -> DocumentUploadResponse:
    """테스트 및 시연용 샘플 .hwpx(지정 양식 또는 배점표 RFP)를 생성해 즉시 분석 결과를 반환한다."""
    sample_dir = get_downloads_dir() / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    fname = "sample_rfp_with_scoring.hwpx" if kind == "rfp" else "sample_designated_form.hwpx"
    target = create_sample_hwpx_template(sample_dir / fname, kind=kind)
    return _analyze_local_file(target)
