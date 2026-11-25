"""범용 편집 출력 모듈: python-docx(.docx) 및 마크다운(.md) 내보내기 (MIT/BSD 계열)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

_KOREAN_FONT = "Malgun Gothic"
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")


def _set_korean_font(style: Any, name: str = _KOREAN_FONT) -> None:
    """font.name은 라틴 글꼴만 바꾸므로 한글(eastAsia) 글꼴도 명시한다. 없으면 Word가 MS Mincho 등으로 대체."""
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), name)
    # 테마 글꼴 지정이 있으면 eastAsia 지정보다 우선하므로 제거
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]


def _clean_md_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _parse_md_table(lines: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(re.match(r"^:?-{2,}:?$", c or "-") for c in cells):
            continue
        rows.append([_clean_md_inline(c) for c in cells])
    return rows


def export_docx(
    title: str,
    subtitle: str,
    sections: list[dict[str, str]],
    output_path: str | Path,
) -> dict[str, Any]:
    """마크다운 섹션 목록을 서식화된 Word(.docx) 문서로 내보낸다."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() != ".docx":
        out = out.with_suffix(".docx")

    doc = Document()
    normal_style = doc.styles["Normal"]
    _set_korean_font(normal_style)
    normal_style.font.size = Pt(10.5)
    for style_name in ["Title", *[f"Heading {n}" for n in range(1, 7)], "List Bullet", "List Number"]:
        try:
            _set_korean_font(doc.styles[style_name])
        except KeyError:
            pass

    title_p = doc.add_heading(title, level=0)
    for run in title_p.runs:
        run.font.color.rgb = RGBColor(0x0F, 0x29, 0x42)

    if subtitle:
        sub_p = doc.add_paragraph(subtitle)
        for run in sub_p.runs:
            run.font.size = Pt(10)
            run.font.color.rgb = RGBColor(0x47, 0x55, 0x69)

    for sec in sections:
        sec_title = sec.get("title", "")
        content = sec.get("content", "")
        h = doc.add_heading(sec_title, level=1)
        for run in h.runs:
            run.font.color.rgb = RGBColor(0x0F, 0x29, 0x42)

        lines = content.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i].strip()
            if not raw:
                i += 1
                continue
            if raw.startswith("|"):
                tbl_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    tbl_lines.append(lines[i])
                    i += 1
                grid = _parse_md_table(tbl_lines)
                if grid:
                    cols = max(len(r) for r in grid)
                    table = doc.add_table(rows=len(grid), cols=cols)
                    table.style = "Table Grid"
                    for r_i, row in enumerate(grid):
                        for c_i in range(cols):
                            table.cell(r_i, c_i).text = row[c_i] if c_i < len(row) else ""
                continue

            heading = _MD_HEADING_RE.match(raw)
            if heading:
                # 섹션 제목이 Heading 1이므로 본문 '#'·'##'은 2단계부터, 최대 6단계
                level = min(6, max(2, len(heading.group(1))))
                doc.add_heading(_clean_md_inline(heading.group(2)), level=level)
            elif raw.startswith(("- ", "* ", "• ")):
                doc.add_paragraph(_clean_md_inline(raw[2:]), style="List Bullet")
            elif raw.startswith(">"):
                q = doc.add_paragraph(_clean_md_inline(raw.lstrip("> ")))
                for run in q.runs:
                    run.italic = True
            else:
                doc.add_paragraph(_clean_md_inline(raw))
            i += 1

    doc.save(str(out))
    return {"ok": True, "output_path": str(out), "mode": "docx", "warnings": []}


def export_markdown(
    title: str,
    subtitle: str,
    sections: list[dict[str, str]],
    output_path: str | Path,
) -> dict[str, Any]:
    """마크다운(.md) 파일로 내보낸다."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() != ".md":
        out = out.with_suffix(".md")

    parts = [f"# {title}\n"]
    if subtitle:
        parts.append(f"> {subtitle}\n")
    for sec in sections:
        parts.append(f"\n## {sec.get('title', '')}\n\n{sec.get('content', '').strip()}\n")

    out.write_text("\n".join(parts), encoding="utf-8")
    return {"ok": True, "output_path": str(out), "mode": "markdown", "warnings": []}
