"""공공입찰·지원사업 공고 및 HITL 검토 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class GoNoGoItem(BaseModel):
    criterion: str
    required_value: str
    company_value: str
    status: str  # "pass" | "warn" | "check"
    note: str = ""


class RfpAnalysis(BaseModel):
    summary: str = ""
    key_tasks: list[str] = Field(default_factory=list)
    go_nogo: list[GoNoGoItem] = Field(default_factory=list)
    scoring_criteria: list[dict[str, Any]] = Field(default_factory=list)
    primary_file_path: str = ""
    disclaimer: str = "최종 투찰·신청 전 반드시 발주처 원문 공고문과 과업지시서를 직접 확인하시기 바랍니다."


class AttachmentInfo(BaseModel):
    filename: str
    url: str = ""
    local_path: str = ""
    size_bytes: int = 0
    file_type: str = ""


class BidItem(BaseModel):
    id: int
    source: str  # "g2b" | "seis" | "bizinfo"
    bid_no: str
    bid_ord: str = "00"
    title: str
    agency: str = ""
    category: str = "용역"
    contract_method: str = ""
    estimated_price: int = 0
    deadline: str = ""
    published_at: str = ""
    region_limit: str = "전국"
    license_limit: str = ""
    is_sme_competing: bool = False
    # 앱 내장 샘플(데모) 공고 여부. 실제 공고와 반드시 구분해 표시해야 한다.
    is_demo: bool = False
    url: str = ""
    privilege_tier: str = "none"  # "private_1_quote" | "private_2_quotes" | "general_priority" | "none"
    privilege_label: str = ""
    rule_passed: bool = True
    rule_warnings: list[str] = Field(default_factory=list)
    ai_score: float | None = None
    ai_summary: str = ""
    ai_risks: list[str] = Field(default_factory=list)
    status: str = "pending"  # "pending" | "approved" | "hold" | "excluded"
    exclusion_reason: str = ""
    exclusion_note: str = ""
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    rfp_analysis: RfpAnalysis | None = None
    created_at: str = ""
    updated_at: str = ""


class BidSyncRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["g2b", "seis", "bizinfo"])
    days_back: int = 7
    keywords: list[str] = Field(default_factory=list)
    use_demo_if_no_key: bool = True


class BidSyncSourceReport(BaseModel):
    source: str
    fetched_count: int = 0
    saved_count: int = 0
    chunks_requested: int = 0
    used_demo: bool = False
    # 실제 API 수집 실패 여부(실패 시 샘플로 대체하지 않는다)
    failed: bool = False
    message: str = ""


class BidSyncResult(BaseModel):
    total_fetched: int
    total_saved: int
    scored_count: int
    reports: list[BidSyncSourceReport] = Field(default_factory=list)


class BidDecisionRequest(BaseModel):
    status: str  # "approved" | "hold" | "excluded" | "pending"
    exclusion_reason: str = ""
    exclusion_note: str = ""


class BidDecisionResponse(BaseModel):
    bid: BidItem
    downloaded_files: list[str] = Field(default_factory=list)
    created_proposal_id: int | None = None
