"""문서 파싱(HWPX/HWP/PDF), 배점표, 목차 트리 및 양식 슬롯 스키마."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoringCriterion(BaseModel):
    category: str
    item: str
    max_score: float
    criteria: str = ""


class HwpxSlot(BaseModel):
    slot_id: str
    location_label: str
    where: str = "cell"
    table_index: int | None = None
    row: int | None = None
    col: int | None = None
    paragraph_index: int | None = None
    prompt_hint: str = ""
    max_chars_hint: int | None = None
    current_text: str = ""


class OutlineNode(BaseModel):
    id: str
    number: str
    title: str
    weight_score: float = 0.0
    target_chars: int = 800
    steering_note: str = ""
    slot_id: str | None = None
    children: list["OutlineNode"] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    file_path: str
    filename: str
    file_type: str  # "hwpx" | "hwp" | "pdf"
    page_count: int = 1
    full_text: str = ""
    tables: list[list[list[str]]] = Field(default_factory=list)
    outline_items: list[dict[str, str]] = Field(default_factory=list)
    slots: list[HwpxSlot] = Field(default_factory=list)
    conversion_warnings: list[str] = Field(default_factory=list)
    used_vision_ocr: bool = False
    is_template_form: bool = False


class ParsePathRequest(BaseModel):
    file_path: str
    proposal_id: int | None = None


class DocumentUploadResponse(BaseModel):
    document: ParsedDocument
    detected_scenario: str  # "A" | "B" | "C"
    scoring_criteria: list[ScoringCriterion] = Field(default_factory=list)
    suggested_outline: list[OutlineNode] = Field(default_factory=list)
    rfp_summary: dict[str, object] = Field(default_factory=dict)
