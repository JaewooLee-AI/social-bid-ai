"""범용 편집 출력 모듈: python-docx(.docx) 및 마크다운(.md) 내보내기 (MIT/BSD 계열)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Pt, RGBColor


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
    normal_style.font.name = "Malgun Gothic"
    normal_style.font.size = Pt(10.5)

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

            if raw.startswith("### "):
                doc.add_heading(_clean_md_inline(raw[4:]), level=3)
            elif raw.startswith("## "):
                doc.add_heading(_clean_md_inline(raw[3:]), level=2)
            elif raw.startswith("# "):
                doc.add_heading(_clean_md_inline(raw[2:]), level=2)
            elif raw.startswith("- ") or raw.startswith("* "):
                doc.add_paragraph(_clean_md_inline(raw[2:]), style="List Bullet")
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
