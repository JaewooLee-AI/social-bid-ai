"""제안서 워크스페이스(시나리오 A/B/C, 스트리밍 생성, HWPX 패치/DOCX/MD 내보내기) 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from app.schemas.docs import OutlineNode, ScoringCriterion


class ProposalCreateRequest(BaseModel):
    title: str
    bid_id: int | None = None
    scenario: str = "B"
    source_file_path: str = ""


class ProposalDetail(BaseModel):
    id: int
    bid_id: int | None = None
    title: str
    scenario: str = "B"
    source_file_path: str = ""
    source_file_type: str = ""
    conversion_warnings: list[str] = Field(default_factory=list)
    rfp_summary: dict[str, Any] = Field(default_factory=dict)
    scoring_table: list[ScoringCriterion] = Field(default_factory=list)
    outline: list[OutlineNode] = Field(default_factory=list)
    sections_content: dict[str, str] = Field(default_factory=dict)
    citations: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class ProposalUpdateRequest(BaseModel):
    title: str | None = None
    scenario: str | None = None
    outline: list[OutlineNode] | None = None
    sections_content: dict[str, str] | None = None


class ProposalOutlineRequest(BaseModel):
    proposal_id: int | None = None
    title: str = ""
    category: str = "용역"
    estimated_price: int = 0
    scenario: str = "auto"
    source_file_path: str = ""


class ProposalGenerateRequest(BaseModel):
    proposal_id: int
    section_ids: list[str] = Field(default_factory=list)
    model_tier: str = "strong"  # "fast" | "strong"


class ProposalExportRequest(BaseModel):
    proposal_id: int
    output_path: str = ""
    format: str = "hwpx"  # "hwpx" | "docx" | "md"


SAMPLE_PROFILE_EXPORT_WARNING = "샘플(가상) 회사 프로필로 작성된 문서입니다. 설정에서 실제 회사 정보를 저장한 뒤 다시 생성하세요."


class ExportResult(BaseModel):
    ok: bool
    output_path: str
    format: str
    mode: str = ""
    warnings: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        # 모든 내보내기 경로(제안서·사업계획서, hwpx/docx/md)에 공통으로 샘플 프로필 경고를 붙인다
        from app.config import load_profile

        try:
            is_sample = bool(load_profile().get("is_sample"))
        except Exception:
            is_sample = False
        if is_sample and SAMPLE_PROFILE_EXPORT_WARNING not in self.warnings:
            self.warnings.append(SAMPLE_PROFILE_EXPORT_WARNING)
