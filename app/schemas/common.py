"""공통 응답 및 대시보드 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    ok: bool = True
    app_name: str = "SocialBidAI"
    version: str = "0.1.0"
    simulation_mode: bool = True
    vec_available: bool = False
    data_dir: str = ""


class ApiStatusResponse(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class QuotaStatus(BaseModel):
    source: str
    label: str
    used_today: int
    daily_limit: int
    remaining: int
    last_synced_at: str = ""
    last_status: str = "idle"


class RecentWorkItem(BaseModel):
    kind: str  # "proposal" | "bizplan"
    id: int
    title: str
    subtitle: str
    updated_at: str


class UrgentBidItem(BaseModel):
    id: int
    source: str
    bid_no: str
    title: str
    agency: str
    estimated_price: int
    deadline: str
    privilege_tier: str
    privilege_label: str
    ai_score: float | None = None
    status: str


class DashboardSummary(BaseModel):
    urgent_bids_count: int
    pending_review_count: int
    approved_bids_count: int
    active_proposals_count: int
    active_bizplans_count: int
    monthly_cost_krw: float
    monthly_cost_usd: float
    monthly_tokens: int
    svi_current_score: float
    svi_target_score: float
    svi_grade_label: str
    priority_purchase_120: bool
    simulation_mode: bool
    vec_available: bool
    quotas: list[QuotaStatus] = Field(default_factory=list)
    urgent_bids: list[UrgentBidItem] = Field(default_factory=list)
    recent_works: list[RecentWorkItem] = Field(default_factory=list)
