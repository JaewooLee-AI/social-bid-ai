"""python-hwpx 읽기/슬롯 탐지/Patch Mode 서식 보존 + pdfplumber/pypdfium2 + DOCX/MD 내보내기 검증."""

from __future__ import annotations

from pathlib import Path
from PIL import Image

from app.documents.export_engine import export_docx, export_markdown
from app.documents.hwpx_engine import (
    create_hwpx_from_sections,
    create_sample_hwpx_template,
    parse_hwpx_or_hwp,
    patch_hwpx,
)
from app.documents.pdf_engine import parse_pdf
from app.documents.rfp_analyzer import build_outline_from_document, detect_scenario, extract_scoring_criteria


def _build_minimal_text_pdf(path: Path) -> None:
    """외부 폰트 의존 없이 ASCII/숫자 포함 유효한 텍스트 PDF를 생성한다."""
    stream = (
        b"BT /F1 12 Tf 50 750 Td (RFP Evaluation Table & Requirements 2026) Tj "
        b"0 -24 Td (1. Technical Plan 40pt / 2. Social Value 30pt / 3. Budget 30pt) Tj "
        b"0 -24 Td (Direct Production Certificate required above 10M KRW.) Tj ET"
    )
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n".encode("ascii") + stream + b"\nendstream endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(out))


def test_hwpx_rfp_parse_and_scoring_outline(tmp_path: Path) -> None:
    rfp_path = create_sample_hwpx_template(tmp_path / "sample_rfp.hwpx", kind="rfp")
    parsed = parse_hwpx_or_hwp(rfp_path)
    assert parsed.file_type == "hwpx"
    assert len(parsed.tables) >= 1

    scoring = extract_scoring_criteria(parsed)
    assert len(scoring) == 4
    assert sum(s.max_score for s in scoring) == 100.0
    assert detect_scenario(parsed, scoring) == "A"

    outline = build_outline_from_document(parsed, scoring, "A")
    assert len(outline) == 4
    # 배점 35점 섹션(index 1)이 배점 20점 섹션(index 2)보다 목표 분량이 커야 한다
    assert outline[1].target_chars > outline[2].target_chars


def test_hwpx_patch_mode_preserves_headers_and_fills_slots(tmp_path: Path) -> None:
    form_path = create_sample_hwpx_template(tmp_path / "form.hwpx", kind="form")
    parsed = parse_hwpx_or_hwp(form_path)
    assert parsed.is_template_form is True
    assert len(parsed.slots) == 4
    assert detect_scenario(parsed, []) == "C"

    slot_updates = {
        parsed.slots[0].slot_id: "**사회문제**: 고령층 디지털 소외 해소 및 취약계층 일자리 창출",
        parsed.slots[1].slot_id: "전담 매니저 파견 메커니즘 적용",
    }
    patched_path = tmp_path / "patched_out.hwpx"
    res = patch_hwpx(form_path, slot_updates, patched_path)
    assert res["ok"] is True
    assert patched_path.exists()

    reopened = parse_hwpx_or_hwp(patched_path)
    # 수정하지 않은 헤더/라벨 셀 보존 확인
    assert "작성 항목" in reopened.full_text
    assert "1. 사회문제 및 Pain Point" in reopened.full_text
    # 패치된 값 반영 확인
    assert "고령층 디지털 소외 해소" in reopened.full_text
    assert "전담 매니저 파견 메커니즘" in reopened.full_text


def test_pdf_text_and_scanned_vision_fallback(tmp_path: Path) -> None:
    text_pdf = tmp_path / "text.pdf"
    _build_minimal_text_pdf(text_pdf)
    parsed_text = parse_pdf(text_pdf)
    assert parsed_text.used_vision_ocr is False
    assert "Evaluation Table" in parsed_text.full_text

    # 이미지 전용 스캔 PDF 생성 -> Vision 폴백 트리거 검증
    scan_pdf = tmp_path / "scanned.pdf"
    img = Image.new("RGB", (600, 800), color="white")
    img.save(scan_pdf, "PDF")

    vision_calls = []

    def fake_vision(img_bytes: bytes, page_num: int) -> str:
        vision_calls.append((len(img_bytes), page_num))
        return "| 평가항목 | 배점 |\n| --- | --- |\n| 수행계획 | 50 |"

    parsed_scan = parse_pdf(scan_pdf, vision_ocr_fn=fake_vision)
    assert parsed_scan.used_vision_ocr is True
    assert len(vision_calls) == 1
    assert "수행계획" in parsed_scan.full_text


def test_export_hwpx_docx_and_markdown(tmp_path: Path) -> None:
    sections = [
        {"title": "1. 사업 이해", "content": "핵심 전략 설명\n\n| 구분 | 내용 |\n| --- | --- |\n| 목표 | 100% 달성 |"},
        {"title": "2. 사회적 가치", "content": "- 취약계층 고용 38%\n- SVI 78점 달성"},
    ]
    h_res = create_hwpx_from_sections("제안서 테스트", "부제", sections, tmp_path / "out.hwpx")
    d_res = export_docx("제안서 테스트", "부제", sections, tmp_path / "out.docx")
    m_res = export_markdown("제안서 테스트", "부제", sections, tmp_path / "out.md")
    assert Path(h_res["output_path"]).exists()
    assert Path(d_res["output_path"]).exists()
    assert Path(m_res["output_path"]).exists()


def test_hwpx_vertical_form_slots_and_nested_table_patch(tmp_path: Path) -> None:
    v_path = create_sample_hwpx_template(tmp_path / "vertical_form.hwpx", kind="vertical_form")
    parsed = parse_hwpx_or_hwp(v_path)
    assert parsed.is_template_form is True
    assert len(parsed.slots) == 4
    assert all(s.col == 0 for s in parsed.slots)
    assert "사회문제" in parsed.slots[0].location_label

    # 마크다운 표가 포함된 내용을 셀 안에 중첩 표로 패치
    md_with_table = (
        "수익모델 요약입니다.\n\n"
        "| 항목 | 산출식 | 금액 |\n"
        "| :--- | :--- | :--- |\n"
        "| 월 매출 | 20건 × 12만 | 240만원 |"
    )
    out_path = tmp_path / "vertical_patched.hwpx"
    res = patch_hwpx(
        v_path,
        {
            parsed.slots[0].slot_id: "첫째 줄 설명\n둘째 줄 설명",
            parsed.slots[2].slot_id: md_with_table,
        },
        out_path,
    )
    assert res["ok"] is True
    assert res["patched_count"] == 2

    reopened = parse_hwpx_or_hwp(out_path)
    # 기본 1개 표 + 셀 내부 중첩 표 1개 = 총 2개 이상의 표가 존재해야 함
    assert len(reopened.tables) >= 2
    assert "240만원" in reopened.full_text
    assert "1. 사회문제(Pain Point) 및 배경" in reopened.full_text
