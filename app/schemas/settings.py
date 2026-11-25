"""설정, API 키, 단가 및 우리 회사 마스터 프로필 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ModelPricing(BaseModel):
    input: float = 0.15
    output: float = 0.60


class LLMConfig(BaseModel):
    provider: str = "gemini"
    api_key: str = ""
    base_url: str = ""
    fast_model: str = "gemini-3.5-flash-lite"
    strong_model: str = "gemini-3.8-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    simulation_mode: bool = True
    usd_to_krw: float = 1380.0
    monthly_budget_krw: float = Field(default=0.0, ge=0.0)
    pricing_per_1m: dict[str, ModelPricing] = Field(default_factory=dict)


class PublicApiConfig(BaseModel):
    g2b_service_key: str = ""
    g2b_base_url: str = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02"
    seis_service_key: str = ""
    seis_endpoint_url: str = "https://apis.data.go.kr/B552465/integratedSupportNotice/getNoticeList"
    seis_extra_params: dict[str, str] = Field(default_factory=dict)
    bizinfo_api_key: str = ""
    bizinfo_endpoint_url: str = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
    bizinfo_hashtags: str = "사회적기업,ESG,돌봄,친환경,교육"
    daily_limits: dict[str, int] = Field(default_factory=lambda: {"g2b": 1000, "seis": 500, "bizinfo": 1000})
    # 나라장터 7일 윈도우·분류별 최대 페이지 수(페이지당 100건). 초과분은 수집 결과 메시지에 생략 건수로 표시
    g2b_max_pages: int = Field(default=10, ge=1, le=50)


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    public_api: PublicApiConfig = Field(default_factory=PublicApiConfig)
    remote_prompts_manifest_url: str = ""


class PastProject(BaseModel):
    year: str = ""
    client: str = ""
    title: str = ""
    amount: int = 0
    outcome: str = ""


class CompanyProfile(BaseModel):
    name: str = ""
    ceo: str = ""
    biz_reg_no: str = ""
    social_type: str = "예비사회적기업(일자리 제공형)"
    is_certified_social: bool = True
    vulnerable_employment_ratio: float = 30.0
    is_women_owned: bool = False
    is_disabled_owned: bool = False
    industry_codes: list[str] = Field(default_factory=list)
    region: str = "서울특별시"
    include_nationwide: bool = True
    min_bid_amount: int = 0
    max_bid_amount: int = 500_000_000
    certifications: list[str] = Field(default_factory=list)
    direct_production_items: list[str] = Field(default_factory=list)
    svi_current_score: float = 75.0
    svi_target_score: float = 85.0
    summary: str = ""
    past_projects: list[PastProject] = Field(default_factory=list)


class ApiTestRequest(BaseModel):
    target: str  # "gemini" | "g2b" | "seis" | "bizinfo"


class PromptItem(BaseModel):
    name: str
    version: str = "2026.1"
    description: str = ""
    system_prompt: str


class PromptUpdateRequest(BaseModel):
    system_prompt: str
    description: str = ""
    version: str = "2026.1"


class RemotePromptSyncRequest(BaseModel):
    manifest_url: str = ""


class ProfilePresetInfo(BaseModel):
    slug: str
    preset_name: str
    company_name: str = ""
    social_type: str = ""
    region: str = ""
    svi_current_score: float = 0.0
    updated_at: str = ""


class ProfilePresetCreateRequest(BaseModel):
    preset_name: str
    profile: CompanyProfile | None = None


class ModelCatalogEntry(BaseModel):
    model_id: str
    display_name: str
    category: str = "chat"  # "chat" | "embedding"
    recommended_tier: str = "strong"  # "fast" | "strong" | "frontier" | "embedding" | "legacy"
    tier_badge: str = ""
    input_price_per_1m: float | None = None
    output_price_per_1m: float | None = None
    price_note: str = ""
    supports_temperature: bool = False
    supports_thinking_level: bool = True
    allowed_thinking_levels: list[str] = Field(default_factory=lambda: ["low", "medium", "high"])
    deprecated: bool = False
    replacement_model: str = ""
    verified_live: bool | None = None
    source: str = "builtin"


class ActiveModelWarning(BaseModel):
    slot: str  # "fast_model" | "strong_model" | "embedding_model"
    model_id: str
    severity: str = "warning"  # "error" | "warning"
    code: str  # "DEPRECATED" | "REMOVED_FROM_API" | "PRICING_MISSING"
    message: str
    suggested_model: str = ""


class ModelCatalogResponse(BaseModel):
    updated_at: str = ""
    source_urls: list[str] = Field(default_factory=list)
    last_sync_status: str = "builtin"
    last_sync_message: str = ""
    error_code: str = ""
    recovery_hint: str = ""
    recommended_fast: str = "gemini-3.5-flash-lite"
    recommended_strong: str = "gemini-3.8-flash"
    recommended_embedding: str = "gemini-embedding-001"
    models: list[ModelCatalogEntry] = Field(default_factory=list)
    active_warnings: list[ActiveModelWarning] = Field(default_factory=list)


class ModelSyncResponse(BaseModel):
    ok: bool
    status: str
    error_code: str = ""
    message: str
    recovery_hint: str = ""
    warnings: list[str] = Field(default_factory=list)
    checked_urls: list[str] = Field(default_factory=list)
    updated_models_count: int = 0
    updated_pricing_count: int = 0
    catalog: ModelCatalogResponse


class TokenUsageByModel(BaseModel):
    model: str
    provider: str
    calls: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    cost_usd: float
    cost_krw: float
    has_pricing: bool
    input_rate: float | None = None
    output_rate: float | None = None


class TokenUsageByFeature(BaseModel):
    feature: str
    calls: int
    total_tokens: int
    cost_krw: float


class TokenUsageLogItem(BaseModel):
    id: int
    provider: str
    model: str
    feature: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float
    cost_krw: float
    pricing_missing: bool
    created_at: str


class TokenUsageReport(BaseModel):
    scope: str
    usd_to_krw: float
    total_calls: int
    live_calls: int
    simulation_calls: int
    unpriced_calls: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    cost_usd: float
    cost_krw: float
    live_cost_krw: float
    simulation_cost_krw: float
    by_model: list[TokenUsageByModel] = Field(default_factory=list)
    by_feature: list[TokenUsageByFeature] = Field(default_factory=list)
    recent_logs: list[TokenUsageLogItem] = Field(default_factory=list)


class RecalculateUsageResponse(BaseModel):
    ok: bool
    updated_rows: int
    still_unpriced_rows: int
    total_cost_krw: float
    message: str
