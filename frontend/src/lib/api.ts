import { invalidateSessionToken, resolveSessionToken } from './bridge';
import type {
  ApiStatusResponse,
  AppConfig,
  AuditReport,
  BidDecisionResponse,
  BidItem,
  BidSyncResult,
  BizPlanDetail,
  CompanyProfile,
  DashboardSummary,
  DocumentUploadResponse,
  ExportResult,
  HealthResponse,
  LlmBudgetStatus,
  LlmCacheStats,
  LlmEstimateKind,
  ModelCatalogResponse,
  ModelSyncResponse,
  OutlineNode,
  PromptItem,
  ProfilePresetInfo,
  ProposalDetail,
  QuotaStatus,
  RagDocumentInfo,
  RagSearchHit,
  RagStatus,
  RecalculateUsageResponse,
  SocialBMCanvas,
  SviAssessmentResult,
  TokenUsageReport,
  UnitEconomicsResult,
} from '../types/api';

async function authHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const token = await resolveSessionToken();
  return {
    ...(token ? { 'X-PyWebView-Token': token } : {}),
    ...extra,
  };
}

async function apiFetch<T>(url: string, init: RequestInit = {}): Promise<T> {
  const isFormData = init.body instanceof FormData;
  const headers = await authHeaders(
    isFormData ? {} : { 'Content-Type': 'application/json', ...(init.headers as Record<string, string> | undefined) }
  );
  let resp = await fetch(url, { ...init, headers });
  if (resp.status === 403) {
    // 토큰이 늦게 준비됐거나 바뀐 경우 한 번만 다시 조회해 재시도(문자열·FormData 본문은 재전송 가능)
    invalidateSessionToken();
    const retryHeaders = await authHeaders(
      isFormData ? {} : { 'Content-Type': 'application/json', ...(init.headers as Record<string, string> | undefined) }
    );
    resp = await fetch(url, { ...init, headers: retryHeaders });
  }
  if (!resp.ok) {
    let msg = `요청 실패 (${resp.status})`;
    try {
      const err = await resp.json();
      if (err?.detail) msg = String(err.detail);
    } catch {
      // ignore
    }
    throw new Error(msg);
  }
  return (await resp.json()) as T;
}

export interface SseEventPayload {
  event: string;
  data: Record<string, unknown>;
}

export async function streamPostSse(
  url: string,
  body: unknown,
  onEvent: (ev: SseEventPayload) => void,
  signal?: AbortSignal
): Promise<void> {
  const headers = await authHeaders({ 'Content-Type': 'application/json' });
  const resp = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let msg = `SSE 스트리밍 연결 실패 (${resp.status})`;
    try {
      const err = await resp.json();
      if (err?.detail) msg = String(err.detail);
    } catch {
      // ignore
    }
    throw new Error(msg);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sepIdx = buffer.indexOf('\n\n');
    while (sepIdx !== -1) {
      const rawBlock = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + 2);
      sepIdx = buffer.indexOf('\n\n');

      let eventType = 'message';
      const dataLines: string[] = [];
      for (const line of rawBlock.split('\n')) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim());
        }
      }
      if (dataLines.length > 0) {
        try {
          const parsed = JSON.parse(dataLines.join('\n')) as Record<string, unknown>;
          onEvent({ event: eventType, data: parsed });
        } catch {
          // ignore malformed partial JSON
        }
      }
    }
  }
}

export const api = {
  health: () => apiFetch<HealthResponse>('/api/health'),
  dashboardSummary: () => apiFetch<DashboardSummary>('/api/dashboard/summary'),

  // Bids & HITL
  listBids: (params?: { source?: string; status?: string; privilege_only?: boolean; q?: string }) => {
    const sp = new URLSearchParams();
    if (params?.source) sp.set('source', params.source);
    if (params?.status) sp.set('status', params.status);
    if (params?.privilege_only) sp.set('privilege_only', 'true');
    if (params?.q) sp.set('q', params.q);
    return apiFetch<BidItem[]>(`/api/bids?${sp.toString()}`);
  },
  syncBids: (daysBack = 7, keyword = '') =>
    apiFetch<BidSyncResult>('/api/bids/sync', {
      method: 'POST',
      body: JSON.stringify({
        sources: ['g2b', 'seis', 'bizinfo'],
        days_back: daysBack,
        keywords: keyword.trim() ? [keyword.trim()] : [],
        use_demo_if_no_key: true,
      }),
    }),
  seedDemoBids: () => apiFetch<BidSyncResult>('/api/bids/seed-demo', { method: 'POST', body: '{}' }),
  decideBid: (bidId: number, payload: { status: string; exclusion_reason?: string; exclusion_note?: string }) =>
    apiFetch<BidDecisionResponse>(`/api/bids/${bidId}/decision`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getQuotas: () => apiFetch<QuotaStatus[]>('/api/bids/quota'),

  // Docs
  uploadDocument: (file: File, estimatedPrice = 0) => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('estimated_price', String(estimatedPrice));
    return apiFetch<DocumentUploadResponse>('/api/docs/upload', { method: 'POST', body: fd });
  },
  parseDocumentPath: (filePath: string) =>
    apiFetch<DocumentUploadResponse>('/api/docs/parse-path', {
      method: 'POST',
      body: JSON.stringify({ file_path: filePath }),
    }),
  sampleTemplate: (kind: 'form' | 'rfp' = 'form') =>
    apiFetch<DocumentUploadResponse>(`/api/docs/sample-template?kind=${kind}`),

  // Proposals
  listProposals: () => apiFetch<ProposalDetail[]>('/api/proposal'),
  createProposal: (payload: { title: string; bid_id?: number | null; scenario?: string; source_file_path?: string }) =>
    apiFetch<ProposalDetail>('/api/proposal', { method: 'POST', body: JSON.stringify(payload) }),
  getProposal: (id: number) => apiFetch<ProposalDetail>(`/api/proposal/${id}`),
  updateProposal: (
    id: number,
    payload: { title?: string; scenario?: string; outline?: OutlineNode[]; sections_content?: Record<string, string> }
  ) =>
    apiFetch<ProposalDetail>(`/api/proposal/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteProposal: (id: number) => apiFetch<{ ok: boolean }>(`/api/proposal/${id}`, { method: 'DELETE' }),
  regenerateOutline: (payload: {
    proposal_id?: number;
    title: string;
    category?: string;
    estimated_price?: number;
    scenario?: string;
    source_file_path?: string;
  }) => apiFetch<OutlineNode[]>('/api/proposal/outline', { method: 'POST', body: JSON.stringify(payload) }),
  exportProposal: (format: 'hwpx' | 'docx' | 'md', proposalId: number, outputPath = '') =>
    apiFetch<ExportResult>(`/api/proposal/export-${format}`, {
      method: 'POST',
      body: JSON.stringify({ proposal_id: proposalId, format, output_path: outputPath }),
    }),

  // BizPlan
  listBizPlans: () => apiFetch<BizPlanDetail[]>('/api/bizplan'),
  createBizPlan: (payload: { title: string; output_track?: string; template_hwpx_path?: string }) =>
    apiFetch<BizPlanDetail>('/api/bizplan', { method: 'POST', body: JSON.stringify(payload) }),
  getBizPlan: (id: number) => apiFetch<BizPlanDetail>(`/api/bizplan/${id}`),
  updateBizPlan: (
    id: number,
    payload: {
      title?: string;
      output_track?: string;
      template_hwpx_path?: string;
      canvas?: SocialBMCanvas;
      sections_content?: Record<string, string>;
    }
  ) => apiFetch<BizPlanDetail>(`/api/bizplan/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  saveBizPlanCanvas: (payload: {
    bizplan_id: number;
    title?: string;
    output_track?: string;
    template_hwpx_path?: string;
    canvas: SocialBMCanvas;
  }) => apiFetch<BizPlanDetail>('/api/bizplan/canvas', { method: 'POST', body: JSON.stringify(payload) }),
  previewUnitEconomics: (payload: {
    unit_price: number;
    target_customers_monthly: number;
    conversion_rate_pct: number;
    variable_cost_per_unit: number;
    fixed_cost_monthly: number;
  }) => apiFetch<UnitEconomicsResult>('/api/bizplan/unit-economics', { method: 'POST', body: JSON.stringify(payload) }),
  previewSviScore: (scores: Record<string, number>) =>
    apiFetch<SviAssessmentResult>('/api/bizplan/svi-score', { method: 'POST', body: JSON.stringify({ scores }) }),
  auditBizPlan: (bizplanId: number) =>
    apiFetch<AuditReport>('/api/bizplan/audit', { method: 'POST', body: JSON.stringify({ bizplan_id: bizplanId }) }),
  remediateBizPlan: (bizplanId: number, flagCode: string, sectionKey: string) =>
    apiFetch<BizPlanDetail>('/api/bizplan/remediate', {
      method: 'POST',
      body: JSON.stringify({ bizplan_id: bizplanId, flag_code: flagCode, section_key: sectionKey }),
    }),
  exportBizPlan: (bizplanId: number, format: 'hwpx' | 'docx' | 'md', outputPath = '') =>
    apiFetch<ExportResult>('/api/bizplan/export', {
      method: 'POST',
      body: JSON.stringify({ bizplan_id: bizplanId, format, output_path: outputPath }),
    }),

  // Settings & RAG
  getSettings: () => apiFetch<AppConfig>('/api/settings'),
  updateSettings: (cfg: AppConfig) => apiFetch<AppConfig>('/api/settings', { method: 'PUT', body: JSON.stringify(cfg) }),
  getModelCatalog: () => apiFetch<ModelCatalogResponse>('/api/settings/models'),
  syncModelCatalog: () => apiFetch<ModelSyncResponse>('/api/settings/models/sync', { method: 'POST', body: '{}' }),
  getTokenUsage: (scope: 'month' | 'all' = 'month') =>
    apiFetch<TokenUsageReport>(`/api/settings/token-usage?scope=${encodeURIComponent(scope)}`),
  recalculateTokenUsage: () =>
    apiFetch<RecalculateUsageResponse>('/api/settings/token-usage/recalculate', { method: 'POST', body: '{}' }),
  getBudget: (kind?: LlmEstimateKind, calls = 0, tier: 'fast' | 'strong' = 'strong') => {
    const sp = new URLSearchParams();
    if (kind) sp.set('kind', kind);
    sp.set('calls', String(calls));
    sp.set('tier', tier);
    return apiFetch<LlmBudgetStatus>(`/api/settings/budget?${sp.toString()}`);
  },
  getLlmCacheStats: () => apiFetch<LlmCacheStats>('/api/settings/llm-cache'),
  clearLlmCache: () => apiFetch<LlmCacheStats>('/api/settings/llm-cache', { method: 'DELETE' }),
  getProfile: () => apiFetch<CompanyProfile>('/api/settings/profile'),
  updateProfile: (profile: CompanyProfile) =>
    apiFetch<CompanyProfile>('/api/settings/profile', { method: 'PUT', body: JSON.stringify(profile) }),
  listProfilePresets: () => apiFetch<ProfilePresetInfo[]>('/api/settings/profile-presets'),
  saveProfilePreset: (presetName: string, profile?: CompanyProfile) =>
    apiFetch<ProfilePresetInfo>('/api/settings/profile-presets', {
      method: 'POST',
      body: JSON.stringify({ preset_name: presetName, profile: profile ?? null }),
    }),
  activateProfilePreset: (slug: string) =>
    apiFetch<CompanyProfile>(`/api/settings/profile-presets/${encodeURIComponent(slug)}/activate`, {
      method: 'POST',
      body: '{}',
    }),
  deleteProfilePreset: (slug: string) =>
    apiFetch<{ ok: boolean }>(`/api/settings/profile-presets/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
  testApi: (target: 'gemini' | 'g2b' | 'seis' | 'bizinfo') =>
    apiFetch<ApiStatusResponse>('/api/settings/test-api', { method: 'POST', body: JSON.stringify({ target }) }),
  getPrompts: () => apiFetch<PromptItem[]>('/api/settings/prompts'),
  updatePrompt: (name: string, payload: { system_prompt: string; description: string; version: string }) =>
    apiFetch<PromptItem>(`/api/settings/prompts/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  syncRemotePrompts: (manifestUrl = '') =>
    apiFetch<ApiStatusResponse>('/api/settings/prompts/sync-remote', {
      method: 'POST',
      body: JSON.stringify({ manifest_url: manifestUrl }),
    }),
  getSviRubric: () => apiFetch<Record<string, unknown>>('/api/settings/svi-rubric'),
  getDataPath: () => apiFetch<{ data_dir: string }>('/api/settings/data-path'),
  getRagStatus: () => apiFetch<RagStatus>('/api/rag/status'),
  ingestRagFile: (file: File, title = '', docType = 'reference') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('title', title || file.name);
    fd.append('doc_type', docType);
    return apiFetch<RagDocumentInfo>('/api/rag/ingest', { method: 'POST', body: fd });
  },
  ingestRagPath: (filePath: string, title = '', docType = 'reference') =>
    apiFetch<RagDocumentInfo>('/api/rag/ingest-path', {
      method: 'POST',
      body: JSON.stringify({ file_path: filePath, title, doc_type: docType }),
    }),
  searchRag: (query: string, topK = 5) =>
    apiFetch<RagSearchHit[]>('/api/rag/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK }),
    }),
  reindexRag: () => apiFetch<RagStatus>('/api/rag/reindex', { method: 'POST', body: '{}' }),
  deleteRagDoc: (docId: number) => apiFetch<{ ok: boolean }>(`/api/rag/docs/${docId}`, { method: 'DELETE' }),
};
