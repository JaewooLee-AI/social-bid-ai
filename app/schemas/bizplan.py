"""Pain Point·수익창출 중심 소셜 사업계획서(SBMC, 단위 경제성, SVI 14개 지표, 레드플래그) 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class SocialBMCanvas(BaseModel):
    # Step 1: Pain Point & 이중 고객
    problem_statement: str = ""
    beneficiary: str = ""  # 수혜자 (예: 취약계층 근로자·독거어르신)
    payer: str = ""  # 실제 결제자 (예: 공공기관·지자체·기업 ESG 담당자)
    beneficiary_equals_payer: bool = False
    existing_alternatives_limit: str = ""
    evidence_sources: str = ""
    tam_desc: str = ""
    sam_desc: str = ""
    som_desc: str = ""

    # Step 2: 문제해결 메커니즘 & 내재형 수익 공식
    solution_mechanism: str = ""
    revenue_formula_sentence: str = ""
    revenue_channels: list[str] = Field(default_factory=lambda: ["B2G 공공우선구매(5천만 수의계약)", "B2B ESG 납품"])
    unit_price: int = 120_000
    target_customers_monthly: int = 80
    conversion_rate_pct: float = 25.0
    variable_cost_per_unit: int = 45_000
    fixed_cost_monthly: int = 4_500_000

    # Step 3: 사회적 목적 5유형 & SVI 14개 지표 & 예비사회적기업 3개년 로드맵
    social_purpose_type: str = "job_creation"
    svi_scores: dict[str, float] = Field(default_factory=dict)
    democratic_governance_plan: str = ""
    profit_reinvestment_plan: str = "배분 가능 이윤의 2/3(66.7%) 이상을 취약계층 신규 채용 및 직무 역량강화에 재투자"
    roadmap_3yr: list[dict[str, str]] = Field(default_factory=list)
    execution_evidence_notes: str = ""
    competitor_notes: str = ""


class UnitEconomicsInput(BaseModel):
    unit_price: int
    target_customers_monthly: int
    conversion_rate_pct: float
    variable_cost_per_unit: int
    fixed_cost_monthly: int


class UnitEconomicsResult(BaseModel):
    paying_customers_monthly: int
    monthly_revenue: int
    annual_revenue: int
    contribution_margin_per_unit: int
    contribution_margin_ratio_pct: float
    monthly_gross_profit: int
    monthly_operating_profit: int
    bep_customers: int | None
    bep_revenue: int | None
    formula_text: str
    markdown_table: str


class SviIndicatorScore(BaseModel):
    id: str
    no: int
    domain: str
    domain_label: str
    name: str
    max_score: float
    score: float
    type: str


class SviScoreRequest(BaseModel):
    scores: dict[str, float] = Field(default_factory=dict)


class SviAssessmentResult(BaseModel):
    total_score: float
    social_score: float
    economic_score: float
    innovation_score: float
    grade_key: str
    grade_label: str
    priority_purchase_120: bool
    grade_summary: str
    indicators: list[SviIndicatorScore] = Field(default_factory=list)


class RedFlagItem(BaseModel):
    code: str  # "format_length" | "unsubstantiated_number" | "no_competitor" | "missing_evidence" | "subsidy_dependency"
    title: str
    severity: str  # "high" | "medium" | "low"
    section_key: str
    evidence_excerpt: str
    recommendation: str
    action_label: str


class AuditReport(BaseModel):
    passed: bool
    overall_score: int
    red_flags: list[RedFlagItem] = Field(default_factory=list)
    checked_at: str = ""


class BizPlanCreateRequest(BaseModel):
    title: str
    output_track: str = "psst"  # "psst" | "pre_social" | "hwpx_patch"
    template_hwpx_path: str = ""


class BizPlanDetail(BaseModel):
    id: int
    title: str
    output_track: str = "psst"
    template_hwpx_path: str = ""
    canvas: SocialBMCanvas = Field(default_factory=SocialBMCanvas)
    unit_economics: UnitEconomicsResult | None = None
    svi_assessment: SviAssessmentResult | None = None
    sections_content: dict[str, str] = Field(default_factory=dict)
    audit_report: AuditReport | None = None
    created_at: str = ""
    updated_at: str = ""


class BizPlanCanvasSaveRequest(BaseModel):
    bizplan_id: int
    title: str | None = None
    output_track: str | None = None
    template_hwpx_path: str | None = None
    canvas: SocialBMCanvas


class BizPlanUpdateRequest(BaseModel):
    title: str | None = None
    output_track: str | None = None
    template_hwpx_path: str | None = None
    canvas: SocialBMCanvas | None = None
    sections_content: dict[str, str] | None = None


class BizPlanGenerateRequest(BaseModel):
    bizplan_id: int
    output_track: str | None = None
    section_keys: list[str] = Field(default_factory=list)
    model_tier: str = "strong"


class BizPlanAuditRequest(BaseModel):
    bizplan_id: int


class BizPlanRemediateRequest(BaseModel):
    bizplan_id: int
    flag_code: str
    section_key: str = ""


class BizPlanExportRequest(BaseModel):
    bizplan_id: int
    format: str = "hwpx"  # "hwpx" | "docx" | "md"
    output_path: str = ""
