"""python-hwpx 기반 .hwpx / .hwp 5.0 파싱, 슬롯 탐지, 서식 보존 Patch Mode 출력 엔진."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import hwpx

from app.documents.errors import DocumentParseError
from app.schemas.docs import HwpxSlot, ParsedDocument

logger = logging.getLogger(__name__)

_SLOT_CUE_RE = re.compile(
    r"(\[.*?(작성|입력|기재|요약|안내).*?\]|\(.*?(작성|입력|이내|자|페이지|매).*?\)|<\s*작성.*?|※\s*작성|여기에\s*작성|작성\s*예시)",
    re.IGNORECASE,
)
_CHAR_LIMIT_RE = re.compile(r"(\d{2,5})\s*(?:자|글자)\s*(?:이내|이하|내외)")


def _clean_text(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _extract_char_limit(text: str) -> int | None:
    m = _CHAR_LIMIT_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _table_identity_map(doc: Any) -> dict[int, int]:
    """doc.tables.with_nested의 각 표 객체 ID -> 0-based table_index 매핑."""
    mapping: dict[int, int] = {}
    for idx, tbl in enumerate(doc.tables.with_nested):
        mapping[id(tbl)] = idx
        if hasattr(tbl, "element"):
            mapping[id(tbl.element)] = idx
    return mapping


def _is_table_cell_place(place: Any) -> bool:
    """문단 위치가 표 셀 내부인지 판정한다. 파서와 패처가 반드시 같은 기준을 써야 p{idx} 슬롯이 일치한다."""
    where = str(getattr(place, "where", "body") or "body")
    return where == "cell" and getattr(place, "table", None) is not None


def _open_hwpx_or_raise(path: Path) -> Any:
    """손상·보안 거부 파일은 내부 예외 대신 DocumentParseError(→ HTTP 400)로 변환한다."""
    try:
        return hwpx.HwpxDocument.open(str(path))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise DocumentParseError(
            f"한글 문서를 열 수 없습니다({path.name}). 손상되었거나, 암호가 걸렸거나, 보안 검사에서 거부된 파일입니다: {exc}"
        ) from exc


def parse_hwpx_or_hwp(file_path: str | Path) -> ParsedDocument:
    """한컴오피스 없이 .hwpx 및 .hwp(5.0) 문서를 파싱해 본문·표·개요·입력 슬롯을 추출한다."""
    path = Path(file_path)
    ext = path.suffix.lower().lstrip(".")
    if ext not in {"hwpx", "hwp"}:
        raise DocumentParseError(f"지원하지 않는 한글 문서 포맷입니다: {path.suffix}")

    doc = _open_hwpx_or_raise(path)
    try:
        conversion_warnings: list[str] = []
        report = getattr(doc, "conversion_report", None)
        if report is not None:
            unconverted = getattr(report, "unconverted", None) or {}
            dropped = getattr(report, "dropped", None) or {}
            if unconverted:
                conversion_warnings.append(
                    f"HWP 5.0 변환 미지원 요소 감지: {unconverted} (최종 저장 시 .hwpx로 변환 권장)"
                )
            if dropped:
                conversion_warnings.append(f"HWP 5.0 로드 중 생략 요소: {dropped}")
            if not conversion_warnings:
                conversion_warnings.append("HWP 5.0 구형 포맷을 로드했습니다. 최종 출력은 .hwpx로 저장됩니다.")

        # 1. 개요(outline) 추출
        raw_outline = doc.text.outline(max_chars=12000, max_block_chars=300)
        outline_items: list[dict[str, str]] = []
        for item in raw_outline or []:
            if isinstance(item, dict):
                outline_items.append(
                    {
                        "id": str(item.get("id", "")),
                        "where": str(item.get("where", "")),
                        "text": _clean_text(str(item.get("text", ""))),
                    }
                )
            else:
                outline_items.append({"id": "", "where": "", "text": _clean_text(str(item))})

        # 2. 중첩 표 포함 전체 표 추출
        tables: list[list[list[str]]] = []
        nested_tables = list(doc.tables.with_nested)
        t_id_map = _table_identity_map(doc)

        for tbl in nested_tables:
            grid: list[list[str]] = []
            for row in tbl.rows:
                row_vals = [_clean_text(getattr(cell, "text", "")) for cell in row.cells]
                grid.append(row_vals)
            tables.append(grid)

        # 3. 전체 문단 순회 및 슬롯 탐지
        body_lines: list[str] = []
        slots: list[HwpxSlot] = []
        seen_slot_ids: set[str] = set()

        body_para_idx = 0
        for place in doc.text.iter_paragraphs():
            text = _clean_text(getattr(place.paragraph, "text", ""))

            if _is_table_cell_place(place):
                tbl = place.table
                t_idx = t_id_map.get(id(tbl))
                if t_idx is None and hasattr(tbl, "element"):
                    t_idx = t_id_map.get(id(tbl.element), 0)
                if t_idx is None:
                    t_idx = 0
                r = int(getattr(place, "row", 0) or 0)
                c = int(getattr(place, "col", 0) or 0)
                slot_id = f"t{t_idx}r{r}c{c}"

                # 좌측 라벨 셀(수평형) 또는 상단 라벨 셀(수직형)을 찾아 슬롯 문맥으로 활용
                left_label = ""
                upper_label = ""
                if 0 <= t_idx < len(tables) and 0 <= r < len(tables[t_idx]):
                    if c > 0 and c - 1 < len(tables[t_idx][r]):
                        left_label = tables[t_idx][r][c - 1]
                    if r > 0 and c < len(tables[t_idx][r - 1]):
                        upper_label = tables[t_idx][r - 1][c]
                        if not upper_label and c < len(tables[t_idx][0]):
                            upper_label = tables[t_idx][0][c]

                is_cue = bool(_SLOT_CUE_RE.search(text))
                is_empty_horizontal = text == "" and c > 0 and bool(left_label)
                is_empty_vertical = (
                    text == ""
                    and r > 0
                    and bool(upper_label)
                    and not _SLOT_CUE_RE.search(upper_label)
                )
                is_empty_after_label = is_empty_horizontal or is_empty_vertical

                if (is_cue or is_empty_after_label) and slot_id not in seen_slot_ids:
                    seen_slot_ids.add(slot_id)
                    label = left_label or upper_label or f"표{t_idx + 1} ({r + 1}행 {c + 1}열)"
                    slots.append(
                        HwpxSlot(
                            slot_id=slot_id,
                            location_label=label,
                            where="cell",
                            table_index=t_idx,
                            row=r,
                            col=c,
                            prompt_hint=text or f"{label} 작성 영역",
                            max_chars_hint=_extract_char_limit(text)
                            or _extract_char_limit(left_label)
                            or _extract_char_limit(upper_label),
                            current_text=text,
                        )
                    )
            else:
                if text:
                    body_lines.append(text)
                    if _SLOT_CUE_RE.search(text):
                        slot_id = f"p{body_para_idx}"
                        if slot_id not in seen_slot_ids:
                            seen_slot_ids.add(slot_id)
                            slots.append(
                                HwpxSlot(
                                    slot_id=slot_id,
                                    location_label=f"본문 {body_para_idx + 1}문단",
                                    where="body",
                                    paragraph_index=body_para_idx,
                                    prompt_hint=text,
                                    max_chars_hint=_extract_char_limit(text),
                                    current_text=text,
                                )
                            )
                # 빈 문단도 포함해 센다(패치 시 동일 기준으로 위치를 찾는다).
                body_para_idx += 1

        # 표 텍스트도 full_text에 포함해 검색·분석 가능하게 구성
        table_text_blocks: list[str] = []
        for idx, grid in enumerate(tables):
            rows_str = [" | ".join(cell for cell in row) for row in grid if any(row)]
            if rows_str:
                table_text_blocks.append(f"[표 {idx + 1}]\n" + "\n".join(rows_str))

        combined_text = "\n\n".join([t for t in ["\n".join(body_lines), *table_text_blocks] if t.strip()])
        is_template_form = len(slots) >= 2

        return ParsedDocument(
            file_path=str(path),
            filename=path.name,
            file_type=ext,
            page_count=max(1, len(list(doc.sections))),
            full_text=combined_text,
            tables=tables,
            outline_items=outline_items,
            slots=slots,
            conversion_warnings=conversion_warnings,
            used_vision_ocr=False,
            is_template_form=is_template_form,
        )
    finally:
        if hasattr(doc, "close"):
            try:
                doc.close()
            except Exception:
                pass


def _strip_markdown_for_hwpx(md_text: str) -> str:
    """HWPX 셀/문단 삽입용으로 과도한 마크다운 기호를 깔끔한 일반 텍스트로 정돈한다."""
    lines: list[str] = []
    for raw in (md_text or "").splitlines():
        line = raw.rstrip()
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        lines.append(line)
    return "\n".join(lines).strip()


def _populate_cell_with_markdown(tbl: Any, r: int, c: int, raw_md: str) -> None:
    """셀에 일반 다중 문단 또는 마크다운 표(|...|)가 포함된 경우 네이티브 중첩 표까지 정확히 패치한다."""
    md_lines = (raw_md or "").splitlines()
    has_md_table = any(ln.strip().startswith("|") for ln in md_lines)
    cell = tbl.rows[r].cells[c]

    if not has_md_table or not hasattr(cell, "clear") or not hasattr(cell, "add_table"):
        clean_val = _strip_markdown_for_hwpx(raw_md)
        if hasattr(cell, "set_text"):
            try:
                cell.set_text(clean_val, preserve_format=True, split_paragraphs=True)
                return
            except Exception:
                pass
        tbl.set_cell_text(r, c, clean_val)
        return

    cell.clear()
    i = 0
    while i < len(md_lines):
        stripped = md_lines[i].strip()
        if stripped.startswith("|"):
            tbl_lines: list[str] = []
            while i < len(md_lines) and md_lines[i].strip().startswith("|"):
                tbl_lines.append(md_lines[i])
                i += 1
            grid = _parse_md_table(tbl_lines)
            if grid and max(len(row) for row in grid) > 0:
                cols = max(len(row) for row in grid)
                nested_tbl = cell.add_table(len(grid), cols)
                for ri, row in enumerate(grid):
                    for ci in range(cols):
                        val = _strip_markdown_for_hwpx(row[ci]) if ci < len(row) else ""
                        nested_tbl.set_cell_text(ri, ci, val)
            continue
        clean_line = _strip_markdown_for_hwpx(stripped)
        if clean_line:
            cell.add_paragraph(clean_line)
        i += 1


def patch_hwpx(
    template_path: str | Path,
    slot_updates: dict[str, str],
    output_path: str | Path,
) -> dict[str, Any]:
    """python-hwpx Patch Mode를 사용해 수정 대상 셀/문단만 교체하고 원본 서식을 보존한다."""
    src = Path(template_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() != ".hwpx":
        out = out.with_suffix(".hwpx")

    doc = _open_hwpx_or_raise(Path(src))
    warnings: list[str] = []
    patched_slots: list[str] = []
    try:
        nested_tables = list(doc.tables.with_nested)

        # 1. 테이블 셀 슬롯(t{idx}r{row}c{col}) 패치 (다중 문단 + 마크다운 표 중첩 지원)
        for slot_id, new_val in slot_updates.items():
            clean_val = _strip_markdown_for_hwpx(new_val)
            m = re.match(r"^t(\d+)r(\d+)c(\d+)$", slot_id)
            if m:
                t_idx, r, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 0 <= t_idx < len(nested_tables):
                    tbl = nested_tables[t_idx]
                    try:
                        _populate_cell_with_markdown(tbl, r, c, new_val)
                        # 긴 텍스트 삽입 시 셀 넘침 방지를 위한 행 높이 자동 보정 (FormFit 휴리스틱)
                        line_count = max(1, clean_val.count("\n") + 1, len(clean_val) // 42 + 1)
                        estimated_height = min(45000, max(3200, line_count * 1650))
                        if hasattr(tbl, "set_row_height"):
                            try:
                                tbl.set_row_height(r, estimated_height)
                            except Exception:
                                pass
                        patched_slots.append(slot_id)
                    except Exception as exc:
                        warnings.append(f"슬롯 {slot_id} 셀 패치 중 경고: {exc}")
                continue

        # 2. 본문 문단 슬롯(p{idx}) 패치
        body_slots = {
            int(k[1:]): _strip_markdown_for_hwpx(v)
            for k, v in slot_updates.items()
            if re.match(r"^p(\d+)$", k)
        }
        if body_slots:
            # 파서(parse_hwpx_or_hwp)와 동일하게 빈 문단을 포함한 모든 본문 문단을 센다.
            # (예전에는 텍스트 있는 문단만 세어, 앞에 빈 줄이 있으면 다음 문단을 덮어썼다.)
            body_idx = 0
            for place in doc.text.iter_paragraphs():
                if _is_table_cell_place(place):
                    continue
                if body_idx in body_slots:
                    place.paragraph.text = body_slots[body_idx]
                    patched_slots.append(f"p{body_idx}")
                body_idx += 1

        # 3. 만약 슬롯 매칭이 전혀 안 된 경우 (예: 지정 양식이 아닌데 패치 요청 시) 하단에 섹션 추가
        if not patched_slots and slot_updates:
            warnings.append("원본 문서에서 일치하는 슬롯을 찾지 못해 문서 말미에 초안을 추가했습니다.")
            for key, val in slot_updates.items():
                doc.add_heading(str(key), level=2)
                for para in _strip_markdown_for_hwpx(val).split("\n"):
                    if para.strip():
                        doc.add_paragraph(para.strip())

        report = doc.save(str(out), format="hwpx", mode="patch", fallback="rebuild")
        actual_mode = str(getattr(report, "actual_mode", "patch"))
        if getattr(report, "fallback_used", False):
            warnings.append("원본 구조 변경이 포함되어 rebuild 모드로 안전 저장되었습니다.")
        return {
            "ok": True,
            "output_path": str(out),
            "mode": actual_mode,
            "patched_count": len(patched_slots),
            "warnings": warnings,
        }
    finally:
        if hasattr(doc, "close"):
            try:
                doc.close()
            except Exception:
                pass


def _parse_md_table(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.match(r"^:?-{2,}:?$", c or "-") for c in cells):
            continue
        rows.append(cells)
    return rows


def create_hwpx_from_sections(
    title: str,
    subtitle: str,
    sections: list[dict[str, str]],
    output_path: str | Path,
) -> dict[str, Any]:
    """양식이 없는 RFP/사업계획서 초안을 깔끔한 새 .hwpx 파일로 생성한다."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() != ".hwpx":
        out = out.with_suffix(".hwpx")

    doc = hwpx.HwpxDocument.new()
    try:
        doc.add_heading(title, level=1)
        if subtitle:
            doc.add_paragraph(subtitle)

        for sec in sections:
            sec_title = sec.get("title", "섹션")
            body = sec.get("content", "")
            doc.add_heading(sec_title, level=2)

            md_lines = body.splitlines()
            i = 0
            while i < len(md_lines):
                line = md_lines[i].strip()
                if line.startswith("|"):
                    tbl_lines = []
                    while i < len(md_lines) and md_lines[i].strip().startswith("|"):
                        tbl_lines.append(md_lines[i])
                        i += 1
                    grid = _parse_md_table(tbl_lines)
                    if grid and max(len(r) for r in grid) > 0:
                        cols = max(len(r) for r in grid)
                        tbl = doc.add_table(len(grid), cols)
                        for r_i, row in enumerate(grid):
                            for c_i in range(cols):
                                val = row[c_i] if c_i < len(row) else ""
                                tbl.set_cell_text(r_i, c_i, val)
                    continue
                clean_line = _strip_markdown_for_hwpx(line)
                if clean_line:
                    doc.add_paragraph(clean_line)
                i += 1

        report = doc.save(str(out), format="hwpx")
        return {
            "ok": True,
            "output_path": str(out),
            "mode": str(getattr(report, "actual_mode", "rebuild")),
            "warnings": [],
        }
    finally:
        if hasattr(doc, "close"):
            try:
                doc.close()
            except Exception:
                pass


def create_sample_hwpx_template(output_path: str | Path, kind: str = "form") -> Path:
    """테스트 및 즉시 시연용 실제 유효 .hwpx 양식/RFP 파일을 생성한다."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = hwpx.HwpxDocument.new()
    try:
        if kind == "rfp":
            doc.add_heading("[제안요청서] 2026년 취약계층 디지털 돌봄 매니저 운영 용역", level=1)
            doc.add_paragraph("1. 사업 개요: 성동구 내 독거어르신 및 취약계층 대상 디지털 역량강화 교육 및 돌봄 연계 용역 (추정가격 48,000,000원, 사회적기업 1인 견적 수의계약 우선검토 대상)")
            doc.add_paragraph("2. 제안서 작성 목차: (1) 사업 이해 및 추진 전략 (2) 세부 과업 수행 방안 (3) 투입 인력 및 추진 일정 (4) 사회적 가치 및 사후관리 계획")
            doc.add_heading("3. 기술능력 평가항목 및 배점표", level=2)
            tbl = doc.add_table(5, 3)
            headers = ["평가항목", "배점", "세부 평가 기준"]
            rows = [
                ["1. 사업 이해 및 추진 전략", "25", "공고 목적 부합성 및 지역사회 문제해결 이해도"],
                ["2. 세부 과업 수행 방안", "35", "교육 커리큘럼 구체성 및 현장 운영 매뉴얼"],
                ["3. 투입 인력 및 추진 일정", "20", "전담 인력 전문성 및 단계별 마일스톤 적정성"],
                ["4. 사회적 가치 및 사후관리", "20", "취약계층 고용 연계, SVI 성과 및 하자보수 계획"],
            ]
            for c_i, h in enumerate(headers):
                tbl.set_cell_text(0, c_i, h)
            for r_i, row in enumerate(rows, start=1):
                for c_i, val in enumerate(row):
                    tbl.set_cell_text(r_i, c_i, val)
        elif kind == "vertical_form":
            doc.add_heading("[지정 양식 · 상하형] 2026 사회적기업 지원사업 계획서", level=1)
            doc.add_paragraph("항목 제목 아래의 빈 셀에 내용을 입력합니다.")
            tbl = doc.add_table(8, 1)
            titles = [
                "1. 사회문제(Pain Point) 및 배경",
                "2. 문제해결 솔루션 및 차별성",
                "3. 수익모델 및 단위 경제성(BEP)",
                "4. SVI 지표 연계 및 사회적 성과",
            ]
            for idx, title in enumerate(titles):
                tbl.set_cell_text(idx * 2, 0, title)
                tbl.set_cell_text(idx * 2 + 1, 0, "")
        else:
            doc.add_heading("[지정 양식] 2026 사회적기업 지원사업 신청서", level=1)
            doc.add_paragraph("※ 파란색/괄호 안내 문구 위치에 내용을 작성하세요. 표 테두리와 제목 서식은 유지됩니다.")
            tbl = doc.add_table(5, 2)
            tbl.set_cell_text(0, 0, "작성 항목")
            tbl.set_cell_text(0, 1, "내용 작성란 (서식 보존 Patch Mode 대상)")
            tbl.set_cell_text(1, 0, "1. 사회문제 및 Pain Point")
            tbl.set_cell_text(1, 1, "[작성 안내: 수혜자와 실제 결제 고객의 고통을 600자 이내로 작성하세요]")
            tbl.set_cell_text(2, 0, "2. 문제해결 메커니즘")
            tbl.set_cell_text(2, 1, "[작성 안내: 제품·서비스 솔루션 및 차별성을 700자 이내로 작성하세요]")
            tbl.set_cell_text(3, 0, "3. 수익창출 구조 및 BEP")
            tbl.set_cell_text(3, 1, "[작성 안내: 객단가×고객수×전환율 및 손익분기점 근거를 600자 이내로 작성하세요]")
            tbl.set_cell_text(4, 0, "4. SVI 연계 사회적 성과")
            tbl.set_cell_text(4, 1, "[작성 안내: 취약계층 고용 및 이윤 사회적 환원 계획을 500자 이내로 작성하세요]")

        doc.save(str(out), format="hwpx")
        return out
    finally:
        if hasattr(doc, "close"):
            try:
                doc.close()
            except Exception:
                pass
