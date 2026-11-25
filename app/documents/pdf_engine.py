"""pdfplumber(MIT) + pypdfium2(Apache-2.0) 기반 상용 안전 한국어 PDF 파싱 & 스캔 PDF Vision 폴백."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Callable

import pdfplumber
import pypdfium2 as pdfium

from app.documents.errors import DocumentParseError
from app.schemas.docs import ParsedDocument

logger = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE_FOR_TEXT_PDF = 60


def parse_pdf(
    file_path: str | Path,
    vision_ocr_fn: Callable[[bytes, int], str] | None = None,
    max_vision_pages: int = 8,
) -> ParsedDocument:
    """
    텍스트 PDF는 pypdfium2(줄바꿈·목차) + pdfplumber(정밀 표 추출)로 파싱하고,
    페이지당 평균 텍스트가 기준치 미만인 스캔 PDF는 pypdfium2 200 DPI 렌더링 후 Vision LLM 폴백을 수행한다.
    """
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        raise DocumentParseError(f"PDF 파일이 아닙니다: {path.suffix}")

    pages_text: list[str] = []
    outline_items: list[dict[str, str]] = []
    page_count = 1

    # 1. pypdfium2 고속 텍스트 및 TOC 추출
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise DocumentParseError(
            f"PDF를 열 수 없습니다({path.name}). 손상되었거나 암호가 걸린 파일입니다: {exc}"
        ) from exc
    try:
        page_count = max(1, len(pdf))
        try:
            for item in pdf.get_toc():
                title = getattr(item, "title", "")
                if title:
                    outline_items.append({"id": f"toc_{len(outline_items)}", "where": "목차", "text": title})
        except Exception:
            pass

        for idx in range(len(pdf)):
            page = pdf[idx]
            try:
                textpage = page.get_textpage()
                try:
                    txt = textpage.get_text_range() or ""
                    pages_text.append(txt.strip())
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        pdf.close()

    # 2. pdfplumber 정밀 표 추출
    tables: list[list[list[str]]] = []
    plumber_texts: list[str] = []
    try:
        with pdfplumber.open(str(path)) as plumber_pdf:
            for p in plumber_pdf.pages:
                ptxt = (p.extract_text() or "").strip()
                if ptxt:
                    plumber_texts.append(ptxt)
                for raw_tbl in p.extract_tables() or []:
                    cleaned = [
                        [(cell or "").replace("\n", " ").strip() for cell in row]
                        for row in raw_tbl
                        if row and any((c or "").strip() for c in row)
                    ]
                    if cleaned:
                        tables.append(cleaned)
    except Exception as exc:
        logger.warning("pdfplumber 추출 경고: %s", exc)

    raw_total_chars = sum(len(t) for t in pages_text)
    avg_chars = raw_total_chars / max(1, page_count)
    used_vision = False
    warnings: list[str] = []

    # 3. 스캔/이미지 PDF 자동 판별 및 200 DPI 렌더링 -> Vision 폴백
    if avg_chars < MIN_CHARS_PER_PAGE_FOR_TEXT_PDF:
        if vision_ocr_fn is not None:
            used_vision = True
            ocr_chunks: list[str] = []
            pdf_render = pdfium.PdfDocument(str(path))
            try:
                limit = min(len(pdf_render), max_vision_pages)
                for idx in range(limit):
                    page = pdf_render[idx]
                    try:
                        bitmap = page.render(scale=200 / 72)
                        pil_img = bitmap.to_pil()
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        ocr_text = vision_ocr_fn(buf.getvalue(), idx + 1)
                        if ocr_text.strip():
                            ocr_chunks.append(f"## [페이지 {idx + 1} OCR]\n{ocr_text.strip()}")
                    finally:
                        page.close()
                if len(pdf_render) > max_vision_pages:
                    warnings.append(
                        f"스캔 PDF 총 {len(pdf_render)}페이지 중 상위 {max_vision_pages}페이지만 Vision 변환했습니다."
                    )
            finally:
                pdf_render.close()
            if ocr_chunks:
                pages_text = ocr_chunks
        else:
            warnings.append("텍스트가 적은 스캔 PDF로 감지되었으나 Vision OCR 함수가 연결되지 않았습니다.")

    # pypdfium2 텍스트가 비었으면 pdfplumber 텍스트 활용
    if not any(pages_text) and plumber_texts:
        pages_text = plumber_texts

    table_blocks: list[str] = []
    for idx, grid in enumerate(tables):
        rows_str = [" | ".join(row) for row in grid]
        if rows_str:
            table_blocks.append(f"[추출 표 {idx + 1}]\n" + "\n".join(rows_str))

    full_text = "\n\n".join([x for x in ["\n\n".join(pages_text), *table_blocks] if x.strip()])

    return ParsedDocument(
        file_path=str(path),
        filename=path.name,
        file_type="pdf",
        page_count=page_count,
        full_text=full_text,
        tables=tables,
        outline_items=outline_items,
        slots=[],
        conversion_warnings=warnings,
        used_vision_ocr=used_vision,
        is_template_form=False,
    )
