// Contract-First TypeScript API types mirrored 1:1 with app/schemas/*.py

export interface HealthResponse {
  ok: boolean;
  app_name: string;
  version: string;
  simulation_mode: boolean;
  vec_available: boolean;
  data_dir: string;
}

export interface ApiStatusResponse {
  ok: boolean;
  message: string;
  details: Record<string, unknown>;
}

export interface QuotaStatus {
  source: string;
  label: string;
  used_today: number;
  daily_limit: number;
  remaining: number;
  last_synced_at: string;
  last_status: string;
}

export interface RecentWorkItem {
  kind: 'proposal' | 'bizplan';
  id: number;
  title: string;
  subtitle: string;
  updated_at: string;
}

export interface UrgentBidItem {
  id: number;
  source: string;
  bid_no: string;
  title: string;
  agency: string;
  estimated_price: number;
  deadline: string;
  privilege_tier: string;
  privilege_label: string;
  ai_score: number | null;
  status: string;
}

export interface DashboardSummary {
  urgent_bids_count: number;
  pending_review_count: number;
  approved_bids_count: number;
  active_proposals_count: number;
  active_bizplans_count: number;
  monthly_cost_krw: number;
  monthly_cost_usd: number;
  monthly_tokens: number;
  svi_current_score: number;
  svi_target_score: number;
  svi_grade_label: string;
  priority_purchase_120: boolean;
  simulation_mode: boolean;
  vec_available: boolean;
  quotas: QuotaStatus[];
  urgent_bids: UrgentBidItem[];
  recent_works: RecentWorkItem[];
}

export interface ModelPricing {
  input: number;
  output: number;
}

export interface ModelCatalogEntry {
  model_id: string;
  display_name: string;
  category: 'chat' | 'embedding' | string;
  recommended_tier: 'fast' | 'strong' | 'frontier' | 'embedding' | 'legacy' | string;
  tier_badge: string;
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  price_note: string;
  supports_temperature: boolean;
  supports_thinking_level: boolean;
  allowed_thinking_levels: string[];
  deprecated: boolean;
  replacement_model: string;
  verified_live: boolean | null;
  source: string;
}

export interface ActiveModelWarning {
  slot: string;
  model_id: string;
  severity: 'error' | 'warning' | string;
  code: 'DEPRECATED' | 'REMOVED_FROM_API' | 'PRICING_MISSING' | string;
  message: string;
  suggested_model: string;
}

export interface ModelCatalogResponse {
  updated_at: string;
  source_urls: string[];
  last_sync_status: string;
  last_sync_message: string;
  error_code: string;
  recovery_hint: string;
  recommended_fast: string;
  recommended_strong: string;
  recommended_embedding: string;
  models: ModelCatalogEntry[];
  active_warnings: ActiveModelWarning[];
}

export interface ModelSyncResponse {
  ok: boolean;
  status: string;
  error_code: string;
  message: string;
  recovery_hint: string;
  warnings: string[];
  checked_urls: string[];
  updated_models_count: number;
  updated_pricing_count: number;
  catalog: ModelCatalogResponse;
}

export interface TokenUsageByModel {
  model: string;
  provider: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  has_pricing: boolean;
  input_rate: number | null;
  output_rate: number | null;
}

export interface TokenUsageByFeature {
  feature: string;
  calls: number;
  total_tokens: number;
  cost_krw: number;
}

export interface TokenUsageLogItem {
  id: number;
  provider: string;
  model: string;
  feature: string;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  cost_usd: number;
  cost_krw: number;
  pricing_missing: boolean;
  created_at: string;
}

export interface TokenUsageReport {
  scope: 'month' | 'all' | string;
  usd_to_krw: number;
  total_calls: number;
  live_calls: number;
  simulation_calls: number;
  unpriced_calls: number;
  input_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_krw: number;
  live_cost_krw: number;
  simulation_cost_krw: number;
  by_model: TokenUsageByModel[];
  by_feature: TokenUsageByFeature[];
  recent_logs: TokenUsageLogItem[];
}

export interface RecalculateUsageResponse {
  ok: boolean;
  updated_rows: number;
  still_unpriced_rows: number;
  total_cost_krw: number;
  message: string;
}

export interface LLMConfig {
  provider: string;
  api_key: string;
  base_url: string;
  fast_model: string;
  strong_model: string;
  embedding_model: string;
  embedding_dim: number;
  simulation_mode: boolean;
  usd_to_krw: number;
  /** 월간 Live API 비용 상한(원). 0이면 무제한 */
  monthly_budget_krw: number;
  pricing_per_1m: Record<string, ModelPricing>;
}

export interface PublicApiConfig {
  g2b_service_key: string;
  g2b_base_url: string;
  seis_service_key: string;
  seis_endpoint_url: string;
  seis_extra_params: Record<string, string>;
  bizinfo_api_key: string;
  bizinfo_endpoint_url: string;
  bizinfo_hashtags: string;
  daily_limits: Record<string, number>;
}

export interface AppConfig {
  llm: LLMConfig;
  public_api: PublicApiConfig;
  remote_prompts_manifest_url: string;
}

export interface PastProject {
  year: string;
  client: string;
  title: string;
  amount: number;
  outcome: string;
}

export interface CompanyProfile {
  name: string;
  ceo: string;
  biz_reg_no: string;
  social_type: string;
  is_certified_social: boolean;
  vulnerable_employment_ratio: number;
  is_women_owned: boolean;
  is_disabled_owned: boolean;
  industry_codes: string[];
  region: string;
  include_nationwide: boolean;
  min_bid_amount: number;
  max_bid_amount: number;
  certifications: string[];
  direct_production_items: string[];
  svi_current_score: number;
  svi_target_score: number;
  summary: string;
  past_projects: PastProject[];
}

export interface PromptItem {
  name: string;
  version: string;
  description: string;
  system_prompt: string;
}

export interface ProfilePresetInfo {
  slug: string;
  preset_name: string;
  company_name: string;
  social_type: string;
  region: string;
  svi_current_score: number;
  updated_at: string;
}

export interface GoNoGoItem {
  criterion: string;
  required_value: string;
  company_value: string;
  status: 'pass' | 'warn' | 'check';
  note: string;
}

export interface RfpAnalysis {
  summary: string;
  key_tasks: string[];
  go_nogo: GoNoGoItem[];
  scoring_criteria: Record<string, unknown>[];
  primary_file_path: string;
  disclaimer: string;
}

export interface AttachmentInfo {
  filename: string;
  url: string;
  local_path: string;
  size_bytes: number;
  file_type: string;
}

export interface BidItem {
  id: number;
  source: 'g2b' | 'seis' | 'bizinfo' | string;
  bid_no: string;
  bid_ord: string;
  title: string;
  agency: string;
  category: string;
  contract_method: string;
  estimated_price: number;
  deadline: string;
  published_at: string;
  region_limit: string;
  license_limit: string;
  is_sme_competing: boolean;
  /** 앱 내장 샘플(데모) 공고 — 실제 공고 아님 */
  is_demo: boolean;
  url: string;
  privilege_tier: 'private_1_quote' | 'private_2_quotes' | 'general_priority' | 'none' | string;
  privilege_label: string;
  rule_passed: boolean;
  rule_warnings: string[];
  ai_score: number | null;
  ai_summary: string;
  ai_risks: string[];
  status: 'pending' | 'approved' | 'hold' | 'excluded';
  exclusion_reason: string;
  exclusion_note: string;
  attachments: AttachmentInfo[];
  rfp_analysis: RfpAnalysis | null;
  created_at: string;
  updated_at: string;
}

export interface BidSyncSourceReport {
  source: string;
  fetched_count: number;
  saved_count: number;
  chunks_requested: number;
  used_demo: boolean;
  /** 실제 API 수집 실패(샘플로 대체하지 않음) */
  failed: boolean;
  message: string;
}

export interface BidSyncResult {
  total_fetched: number;
  total_saved: number;
  scored_count: number;
  reports: BidSyncSourceReport[];
}

export interface BidDecisionResponse {
  bid: BidItem;
  downloaded_files: string[];
  created_proposal_id: number | null;
}

export interface ScoringCriterion {
  category: string;
  item: string;
  max_score: number;
  criteria: string;
}

export interface HwpxSlot {
  slot_id: string;
  location_label: string;
  where: string;
  table_index: number | null;
  row: number | null;
  col: number | null;
  paragraph_index: number | null;
  prompt_hint: string;
  max_chars_hint: number | null;
  current_text: string;
}

export interface OutlineNode {
  id: string;
  number: string;
  title: string;
  weight_score: number;
  target_chars: number;
  steering_note: string;
  slot_id: string | null;
  children: OutlineNode[];
}

export interface ParsedDocument {
  file_path: string;
  filename: string;
  file_type: string;
  page_count: number;
  full_text: string;
  tables: string[][][];
  outline_items: Record<string, string>[];
  slots: HwpxSlot[];
  conversion_warnings: string[];
  used_vision_ocr: boolean;
  is_template_form: boolean;
}

export interface DocumentUploadResponse {
  document: ParsedDocument;
  detected_scenario: 'A' | 'B' | 'C';
  scoring_criteria: ScoringCriterion[];
  suggested_outline: OutlineNode[];
  rfp_summary: Record<string, unknown>;
}

export interface ProposalDetail {
  id: number;
  bid_id: number | null;
  title: string;
  scenario: 'A' | 'B' | 'C' | string;
  source_file_path: string;
  source_file_type: string;
  conversion_warnings: string[];
  rfp_summary: Record<string, unknown>;
  scoring_table: ScoringCriterion[];
  outline: OutlineNode[];
  sections_content: Record<string, string>;
  citations: Record<string, Record<string, unknown>[]>;
  created_at: string;
  updated_at: string;
}

export interface ExportResult {
  ok: boolean;
  output_path: string;
  format: string;
  mode: string;
  warnings: string[];
}

export interface SocialBMCanvas {
  problem_statement: string;
  beneficiary: string;
  payer: string;
  beneficiary_equals_payer: boolean;
  existing_alternatives_limit: string;
  evidence_sources: string;
  tam_desc: string;
  sam_desc: string;
  som_desc: string;
  solution_mechanism: string;
  revenue_formula_sentence: string;
  revenue_channels: string[];
  unit_price: number;
  target_customers_monthly: number;
  conversion_rate_pct: number;
  variable_cost_per_unit: number;
  fixed_cost_monthly: number;
  social_purpose_type: string;
  svi_scores: Record<string, number>;
  democratic_governance_plan: string;
  profit_reinvestment_plan: string;
  roadmap_3yr: Record<string, string>[];
  execution_evidence_notes: string;
  competitor_notes: string;
}

export interface UnitEconomicsResult {
  paying_customers_monthly: number;
  monthly_revenue: number;
  annual_revenue: number;
  contribution_margin_per_unit: number;
  contribution_margin_ratio_pct: number;
  monthly_gross_profit: number;
  monthly_operating_profit: number;
  bep_customers: number | null;
  bep_revenue: number | null;
  formula_text: string;
  markdown_table: string;
}

export interface SviIndicatorScore {
  id: string;
  no: number;
  domain: string;
  domain_label: string;
  name: string;
  max_score: number;
  score: number;
  type: string;
}

export interface SviAssessmentResult {
  total_score: number;
  social_score: number;
  economic_score: number;
  innovation_score: number;
  grade_key: string;
  grade_label: string;
  priority_purchase_120: boolean;
  grade_summary: string;
  indicators: SviIndicatorScore[];
}

export interface RedFlagItem {
  code: string;
  title: string;
  severity: 'high' | 'medium' | 'low';
  section_key: string;
  evidence_excerpt: string;
  recommendation: string;
  action_label: string;
}

export interface AuditReport {
  passed: boolean;
  overall_score: number;
  red_flags: RedFlagItem[];
  checked_at: string;
}

export interface BizPlanDetail {
  id: number;
  title: string;
  output_track: 'psst' | 'pre_social' | 'hwpx_patch';
  template_hwpx_path: string;
  canvas: SocialBMCanvas;
  unit_economics: UnitEconomicsResult | null;
  svi_assessment: SviAssessmentResult | null;
  sections_content: Record<string, string>;
  audit_report: AuditReport | null;
  created_at: string;
  updated_at: string;
}

export interface RagDocumentInfo {
  id: number;
  title: string;
  doc_type: string;
  file_path: string;
  chunk_count: number;
  created_at: string;
}

export interface RagStatus {
  vec_available: boolean;
  current_model: string;
  current_dim: number;
  indexed_model: string;
  indexed_dim: number;
  needs_reindex: boolean;
  doc_count: number;
  chunk_count: number;
  documents: RagDocumentInfo[];
}

export interface RagSearchHit {
  chunk_id: number;
  doc_id: number;
  doc_title: string;
  doc_type: string;
  content: string;
  rrf_score: number;
  fts_rank: number | null;
  vec_rank: number | null;
  metadata: Record<string, unknown>;
}

export type LlmEstimateKind = 'section' | 'remediate' | 'embedding';

export interface LlmBudgetStatus {
  simulation_mode: boolean;
  monthly_budget_krw: number;
  used_krw: number;
  remaining_krw: number | null;
  exceeded: boolean;
  estimate_krw: number;
  estimate_model: string;
  would_exceed: boolean;
}

export interface LlmCacheStats {
  embedding_entries: number;
  ocr_entries: number;
}
