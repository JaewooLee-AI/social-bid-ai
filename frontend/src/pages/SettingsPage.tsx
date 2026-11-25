import React, { useEffect, useId, useRef, useState } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  Building2,
  Calculator,
  CheckCircle2,
  Coins,
  Database,
  ExternalLink,
  Eye,
  EyeOff,
  FileCode2,
  FolderOpen,
  Globe,
  KeyRound,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Trash2,
  Users,
} from 'lucide-react';
import { api } from '../lib/api';
import { nativeOpenFolder } from '../lib/bridge';
import type {
  AppConfig,
  CompanyProfile,
  LlmBudgetStatus,
  LlmCacheStats,
  ModelCatalogEntry,
  ModelCatalogResponse,
  ModelPricing,
  ModelSyncResponse,
  ProfilePresetInfo,
  PromptItem,
  RagSearchHit,
  RagStatus,
  TokenUsageReport,
} from '../types/api';
import { FileDropzone } from '../components/FileDropzone';
import { useLlmConfirm } from '../components/LlmConfirmDialog';

interface SettingsPageProps {
  onNotify: (msg: string) => void;
  onConfigUpdated: () => void;
}

const SUB_TABS = [
  { key: 'profile', label: '우리 회사 마스터 프로필', icon: Building2 },
  { key: 'llm', label: 'LLM API 키 · 토큰 단가', icon: KeyRound },
  { key: 'public', label: '공공 Open API · 통신 테스트', icon: RefreshCw },
  { key: 'rag', label: '로컬 하이브리드 RAG 관리', icon: Database },
  { key: 'prompts', label: '외부 프롬프트 · 데이터 경로', icon: FileCode2 },
] as const;

type SubTabKey = (typeof SUB_TABS)[number]['key'];

/** RAG 인덱스 임베딩 공간 표시명 (서버 LOCAL_HASH_EMBEDDING_SPACE와 동일 식별자) */
const embeddingSpaceLabel = (model: string) =>
  model === 'local-hash-2gram-v1' ? '로컬 해시(시뮬레이션)' : model;

export const SettingsPage: React.FC<SettingsPageProps> = ({ onNotify, onConfigUpdated }) => {
  const [tab, setTab] = useState<SubTabKey>('profile');
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  const [ragStatus, setRagStatus] = useState<RagStatus | null>(null);
  const [prompts, setPrompts] = useState<PromptItem[]>([]);
  const [dataDir, setDataDir] = useState<string>('');
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showG2bKey, setShowG2bKey] = useState(false);
  const [ragQuery, setRagQuery] = useState('');
  const [ragHits, setRagHits] = useState<RagSearchHit[]>([]);
  const [ragUploading, setRagUploading] = useState(false);
  const [ragReindexing, setRagReindexing] = useState(false);
  const reindexLockRef = useRef(false);
  const { confirmLlm, confirmDialog } = useLlmConfirm();
  const [budgetStatus, setBudgetStatus] = useState<LlmBudgetStatus | null>(null);
  const [cacheStats, setCacheStats] = useState<LlmCacheStats | null>(null);
  const [cacheClearArmed, setCacheClearArmed] = useState(false);

  const refreshBudgetAndCache = async () => {
    try {
      const [b, c] = await Promise.all([api.getBudget(), api.getLlmCacheStats()]);
      setBudgetStatus(b);
      setCacheStats(c);
    } catch {
      // 부가 정보이므로 실패해도 무시
    }
  };

  useEffect(() => {
    void refreshBudgetAndCache();
  }, []);

  const handleClearLlmCache = async () => {
    try {
      const removed = await api.clearLlmCache();
      setCacheClearArmed(false);
      await refreshBudgetAndCache();
      onNotify(`LLM 캐시를 비웠습니다 (임베딩 ${removed.embedding_entries}건 · OCR ${removed.ocr_entries}건).`);
    } catch (err) {
      onNotify(`캐시 비우기 실패: ${err instanceof Error ? err.message : String(err)}`);
    }
  };
  const [industryRaw, setIndustryRaw] = useState('');
  const [dpRaw, setDpRaw] = useState('');
  const [certRaw, setCertRaw] = useState('');
  const [presets, setPresets] = useState<ProfilePresetInfo[]>([]);
  const [selectedPresetSlug, setSelectedPresetSlug] = useState('');
  const [newPresetName, setNewPresetName] = useState('');
  const [catalog, setCatalog] = useState<ModelCatalogResponse | null>(null);
  const [usageReport, setUsageReport] = useState<TokenUsageReport | null>(null);
  const [usageScope, setUsageScope] = useState<'month' | 'all'>('month');
  const [syncingModels, setSyncingModels] = useState(false);
  const [recalculatingUsage, setRecalculatingUsage] = useState(false);
  const [lastSyncResult, setLastSyncResult] = useState<ModelSyncResponse | null>(null);
  const [fastCustomOpen, setFastCustomOpen] = useState(false);
  const [strongCustomOpen, setStrongCustomOpen] = useState(false);
  const [embCustomOpen, setEmbCustomOpen] = useState(false);
  const [pricingDrafts, setPricingDrafts] = useState<Record<string, { input: string; output: string }>>({});
  const [newModelId, setNewModelId] = useState('');
  const [newModelIn, setNewModelIn] = useState('');
  const [newModelOut, setNewModelOut] = useState('');
  const [testingApi, setTestingApi] = useState<Record<string, boolean>>({});
  const [apiTestResults, setApiTestResults] = useState<
    Record<
      string,
      {
        ok: boolean;
        message: string;
        model?: string;
        elapsed_ms?: number;
        simulation_mode?: boolean;
        tested_at: string;
      }
    >
  >({});

  const ragSearchId = useId();

  const syncPricingDraftsFromConfig = (c: AppConfig, cat: ModelCatalogResponse | null) => {
    const drafts: Record<string, { input: string; output: string }> = {};
    const keys = new Set<string>([
      ...Object.keys(c.llm.pricing_per_1m || {}),
      ...(cat?.models.map((m) => m.model_id) || []),
      c.llm.fast_model,
      c.llm.strong_model,
      c.llm.embedding_model,
    ]);
    for (const k of keys) {
      if (!k) continue;
      const p = c.llm.pricing_per_1m?.[k];
      const catEntry = cat?.models.find((m) => m.model_id === k);
      if (p) {
        drafts[k] = { input: String(p.input), output: String(p.output) };
      } else if (catEntry && catEntry.input_price_per_1m !== null) {
        drafts[k] = {
          input: String(catEntry.input_price_per_1m),
          output: String(catEntry.output_price_per_1m ?? 0),
        };
      }
    }
    setPricingDrafts(drafts);
  };

  const applyLoadedProfile = (p: CompanyProfile) => {
    setProfile(p);
    setIndustryRaw(p.industry_codes.join(', '));
    setDpRaw(p.direct_production_items.join(', '));
    setCertRaw(p.certifications.join(', '));
  };

  const loadAll = async () => {
    try {
      const [c, p, r, pr, dp, presetList, cat, usage] = await Promise.all([
        api.getSettings(),
        api.getProfile(),
        api.getRagStatus(),
        api.getPrompts(),
        api.getDataPath(),
        api.listProfilePresets(),
        api.getModelCatalog(),
        api.getTokenUsage(usageScope),
      ]);
      setCfg(c);
      setCatalog(cat);
      syncPricingDraftsFromConfig(c, cat);
      setUsageReport(usage);
      applyLoadedProfile(p);
      setRagStatus(r);
      setPrompts(pr);
      setDataDir(dp.data_dir);
      setPresets(presetList);
      if (presetList.length > 0 && !selectedPresetSlug) {
        setSelectedPresetSlug(presetList[0].slug);
      }
    } catch (e) {
      onNotify(`설정 불러오기 실패: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const buildConfigWithDrafts = (base: AppConfig): AppConfig => {
    const nextPricing: Record<string, ModelPricing> = { ...(base.llm.pricing_per_1m || {}) };
    for (const [mid, d] of Object.entries(pricingDrafts)) {
      const inRaw = d.input.trim();
      const outRaw = d.output.trim();
      if (inRaw === '' && outRaw === '') continue;
      const inVal = Number(inRaw);
      const outVal = Number(outRaw === '' ? '0' : outRaw);
      if (Number.isFinite(inVal) && Number.isFinite(outVal) && inVal >= 0 && outVal >= 0) {
        nextPricing[mid] = { input: inVal, output: outVal };
      }
    }
    return {
      ...base,
      llm: {
        ...base.llm,
        pricing_per_1m: nextPricing,
      },
    };
  };

  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!cfg) return;
    try {
      const mergedCfg = buildConfigWithDrafts(cfg);
      const saved = await api.updateSettings(mergedCfg);
      const [r, cat, usage] = await Promise.all([
        api.getRagStatus(),
        api.getModelCatalog(),
        api.getTokenUsage(usageScope),
      ]);
      setCfg(saved);
      setCatalog(cat);
      syncPricingDraftsFromConfig(saved, cat);
      setRagStatus(r);
      setUsageReport(usage);
      void refreshBudgetAndCache();
      onConfigUpdated();
      onNotify('API 키 및 단가 설정을 %APPDATA% config.json에 저장했습니다.');
    } catch (err) {
      onNotify(`설정 저장 실패: ${(err as Error).message}`);
    }
  };

  const handleSyncModels = async () => {
    setSyncingModels(true);
    try {
      if (cfg) {
        await api.updateSettings(buildConfigWithDrafts(cfg));
      }
      const res = await api.syncModelCatalog();
      setLastSyncResult(res);
      setCatalog(res.catalog);
      const [freshCfg, usage] = await Promise.all([api.getSettings(), api.getTokenUsage(usageScope)]);
      setCfg(freshCfg);
      syncPricingDraftsFromConfig(freshCfg, res.catalog);
      setUsageReport(usage);
      onConfigUpdated();
      onNotify(res.message);
    } catch (err) {
      onNotify(`공식 사이트 모델 동기화 실패: ${(err as Error).message}`);
    } finally {
      setSyncingModels(false);
    }
  };

  const handleApplyRecommendedModels = async () => {
    if (!cfg || !catalog) return;
    const base = buildConfigWithDrafts(cfg);
    const nextCfg: AppConfig = {
      ...base,
      llm: {
        ...base.llm,
        fast_model: catalog.recommended_fast,
        strong_model: catalog.recommended_strong,
        embedding_model: catalog.recommended_embedding,
      },
    };
    try {
      const saved = await api.updateSettings(nextCfg);
      const [freshCat, rag, usage] = await Promise.all([
        api.getModelCatalog(),
        api.getRagStatus(),
        api.getTokenUsage(usageScope),
      ]);
      setCfg(saved);
      setCatalog(freshCat);
      setFastCustomOpen(false);
      setStrongCustomOpen(false);
      setEmbCustomOpen(false);
      syncPricingDraftsFromConfig(saved, freshCat);
      setRagStatus(rag);
      setUsageReport(usage);
      onConfigUpdated();
      onNotify(
        `권장 모델로 전환했습니다 (경량: ${saved.llm.fast_model} / 고품질: ${saved.llm.strong_model}).`
      );
    } catch (err) {
      onNotify(`권장 모델 전환 실패: ${(err as Error).message}`);
    }
  };

  const handleChangeUsageScope = async (nextScope: 'month' | 'all') => {
    setUsageScope(nextScope);
    try {
      setUsageReport(await api.getTokenUsage(nextScope));
    } catch (err) {
      onNotify(`사용량 조회 실패: ${(err as Error).message}`);
    }
  };

  const handleRecalculateUsage = async () => {
    setRecalculatingUsage(true);
    try {
      if (cfg) {
        const saved = await api.updateSettings(buildConfigWithDrafts(cfg));
        setCfg(saved);
      }
      const res = await api.recalculateTokenUsage();
      const [freshUsage, freshCat] = await Promise.all([
        api.getTokenUsage(usageScope),
        api.getModelCatalog(),
      ]);
      setUsageReport(freshUsage);
      setCatalog(freshCat);
      onConfigUpdated();
      onNotify(res.message);
    } catch (err) {
      onNotify(`사용량 비용 재계산 실패: ${(err as Error).message}`);
    } finally {
      setRecalculatingUsage(false);
    }
  };

  const handleAddCustomPricing = () => {
    if (!cfg) return;
    const mid = newModelId.trim();
    const inVal = Number(newModelIn.trim() || '0');
    const outVal = Number(newModelOut.trim() || '0');
    if (!mid || !Number.isFinite(inVal) || !Number.isFinite(outVal) || inVal < 0 || outVal < 0) {
      onNotify('올바른 모델 ID와 0 이상의 입력·출력 단가(USD)를 입력하세요.');
      return;
    }
    setCfg({
      ...cfg,
      llm: {
        ...cfg.llm,
        pricing_per_1m: {
          ...cfg.llm.pricing_per_1m,
          [mid]: { input: inVal, output: outVal },
        },
      },
    });
    setPricingDrafts((prev) => ({
      ...prev,
      [mid]: { input: String(inVal), output: String(outVal) },
    }));
    setNewModelId('');
    setNewModelIn('');
    setNewModelOut('');
    onNotify(`단가표에 '${mid}'를 추가했습니다. [설정 저장]을 눌러 확정하세요.`);
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile) return;
    const next: CompanyProfile = {
      ...profile,
      industry_codes: industryRaw.split(',').map((s) => s.trim()).filter(Boolean),
      direct_production_items: dpRaw.split(',').map((s) => s.trim()).filter(Boolean),
      certifications: certRaw.split(',').map((s) => s.trim()).filter(Boolean),
    };
    try {
      const saved = await api.updateProfile(next);
      applyLoadedProfile(saved);
      const r = await api.getRagStatus();
      setRagStatus(r);
      onConfigUpdated();
      onNotify('마스터 프로필을 저장하고 로컬 RAG 인덱스를 자동 갱신했습니다.');
    } catch (err) {
      onNotify(`프로필 저장 실패: ${(err as Error).message}`);
    }
  };

  const handleSavePreset = async () => {
    if (!profile) return;
    const name = newPresetName.trim() || profile.name;
    if (!name) return;
    const next: CompanyProfile = {
      ...profile,
      industry_codes: industryRaw.split(',').map((s) => s.trim()).filter(Boolean),
      direct_production_items: dpRaw.split(',').map((s) => s.trim()).filter(Boolean),
      certifications: certRaw.split(',').map((s) => s.trim()).filter(Boolean),
    };
    try {
      const saved = await api.saveProfilePreset(name, next);
      const list = await api.listProfilePresets();
      setPresets(list);
      setSelectedPresetSlug(saved.slug);
      setNewPresetName('');
      onNotify(`고객사 프리셋 '${saved.preset_name}'을 저장했습니다.`);
    } catch (err) {
      onNotify(`프리셋 저장 실패: ${(err as Error).message}`);
    }
  };

  const handleActivatePreset = async () => {
    if (!selectedPresetSlug) return;
    try {
      const activated = await api.activateProfilePreset(selectedPresetSlug);
      applyLoadedProfile(activated);
      setRagStatus(await api.getRagStatus());
      onConfigUpdated();
      onNotify(`활성 고객사를 '${activated.name}'(으)로 전환하고 RAG를 동기화했습니다.`);
    } catch (err) {
      onNotify(`프리셋 전환 실패: ${(err as Error).message}`);
    }
  };

  const handleDeletePreset = async () => {
    if (!selectedPresetSlug) return;
    try {
      await api.deleteProfilePreset(selectedPresetSlug);
      const list = await api.listProfilePresets();
      setPresets(list);
      setSelectedPresetSlug(list[0]?.slug || '');
      onNotify('선택한 고객사 프리셋을 삭제했습니다.');
    } catch (err) {
      onNotify(`프리셋 삭제 실패: ${(err as Error).message}`);
    }
  };

  const handleTestApi = async (target: 'gemini' | 'g2b' | 'seis' | 'bizinfo') => {
    setTestingApi((prev) => ({ ...prev, [target]: true }));
    try {
      if (cfg) {
        const saved = await api.updateSettings(buildConfigWithDrafts(cfg));
        setCfg(saved);
      }
      const res = await api.testApi(target);
      const details = (res.details || {}) as Record<string, unknown>;
      const testedAt =
        typeof details.tested_at === 'string' && details.tested_at
          ? details.tested_at
          : new Date().toLocaleTimeString('ko-KR', { hour12: false });
      setApiTestResults((prev) => ({
        ...prev,
        [target]: {
          ok: Boolean(res.ok),
          message: res.message,
          model: typeof details.model === 'string' ? details.model : undefined,
          elapsed_ms: typeof details.elapsed_ms === 'number' ? details.elapsed_ms : undefined,
          simulation_mode: typeof details.simulation_mode === 'boolean' ? details.simulation_mode : undefined,
          tested_at: testedAt,
        },
      }));
      if (target === 'gemini' && res.ok) {
        onConfigUpdated();
      }
      onNotify(res.message);
    } catch (err) {
      const msg = `비정상 — 통신 테스트 실패: ${(err as Error).message}`;
      setApiTestResults((prev) => ({
        ...prev,
        [target]: {
          ok: false,
          message: msg,
          tested_at: new Date().toLocaleTimeString('ko-KR', { hour12: false }),
        },
      }));
      onNotify(msg);
    } finally {
      setTestingApi((prev) => ({ ...prev, [target]: false }));
    }
  };

  const handleRagReindex = async () => {
    // await 이전 동기 잠금으로 연타 시 전체 재임베딩이 중복 실행되는 것을 차단
    if (reindexLockRef.current) return;
    reindexLockRef.current = true;
    setRagReindexing(true);
    try {
      const ok = await confirmLlm({
        title: '로컬 RAG 전체 재인덱싱',
        details: [
          `대상: 문서 ${ragStatus?.doc_count ?? 0}건 · 청크 ${ragStatus?.chunk_count ?? 0}개 전체 재임베딩`,
          `임베딩 모델: ${ragStatus?.current_model ?? ''} (${ragStatus?.current_dim ?? ''}D)`,
          '같은 모델·차원으로 이미 임베딩된 청크는 캐시를 재사용해 과금되지 않습니다.',
        ],
        confirmLabel: '재인덱싱 실행',
        estimate: { kind: 'embedding', calls: ragStatus?.chunk_count ?? 0 },
      });
      if (!ok) return;
      const updated = await api.reindexRag();
      setRagStatus(updated);
      void refreshBudgetAndCache();
      onNotify('전체 로컬 RAG 임베딩 벡터 재인덱싱을 완료했습니다.');
    } catch (err) {
      onNotify(`재인덱싱 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      reindexLockRef.current = false;
      setRagReindexing(false);
    }
  };

  const handleRagSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ragQuery.trim()) return;
    try {
      const hits = await api.searchRag(ragQuery.trim(), 5);
      setRagHits(hits);
    } catch (err) {
      onNotify(`RAG 검색 실패: ${(err as Error).message}`);
    }
  };

  const runRagIngest = async (ingestFn: () => Promise<unknown>, successMessage: string) => {
    setRagUploading(true);
    try {
      await ingestFn();
      setRagStatus(await api.getRagStatus());
      void refreshBudgetAndCache();
      onNotify(successMessage);
    } catch (err) {
      onNotify(`RAG 인덱싱 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRagUploading(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-[#0F2942]">마스터 프로필 · 외부 설정 · 로컬 하이브리드 RAG</h1>
        <p className="text-xs text-slate-500 mt-0.5">
          모든 설정·API 키·DB·프롬프트 YAML은 앱 외부 데이터 폴더({dataDir || '%APPDATA%/SocialBidAI'})에 분리 보관됩니다.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* 좌측 서브 내비게이션 */}
        <nav aria-label="설정 하위 메뉴" className="lg:col-span-3 bg-white border border-slate-200 rounded-xl p-2 space-y-1">
          {SUB_TABS.map((item) => {
            const Icon = item.icon;
            const active = tab === item.key;
            return (
              <button
                key={item.key}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => setTab(item.key)}
                className={`w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-xs font-semibold text-left transition-colors ${
                  active ? 'bg-[#0F2942] text-white' : 'text-slate-700 hover:bg-slate-100'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        {/* 우측 폼 영역 */}
        <div className="lg:col-span-9 bg-white border border-slate-200 rounded-xl p-5">
          {tab === 'profile' && profile && (
            <form onSubmit={handleSaveProfile} className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-slate-200">
                <h2 className="text-sm font-bold text-[#0F2942]">우리 회사 마스터 프로필 (공고 필터 · RAG 기준 데이터)</h2>
                <button
                  type="submit"
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700"
                >
                  <Save className="w-3.5 h-3.5" />
                  프로필 저장 & RAG 동기화
                </button>
              </div>

              {/* 컨설팅 멀티 고객사 프리셋 스위처 */}
              <fieldset className="p-3 rounded-lg bg-slate-50 border border-slate-200 space-y-2">
                <legend className="text-xs font-bold text-[#0F2942] px-1 flex items-center gap-1">
                  <Users className="w-3.5 h-3.5 text-[#0D9488]" />
                  고객사 프로필 프리셋 전환 (컨설팅 멀티 테넌트)
                </legend>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label="저장된 고객사 프리셋"
                    value={selectedPresetSlug}
                    onChange={(e) => setSelectedPresetSlug(e.target.value)}
                    className="px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 bg-white min-w-[200px]"
                  >
                    {presets.length === 0 ? (
                      <option value="">저장된 프리셋 없음</option>
                    ) : (
                      presets.map((pr) => (
                        <option key={pr.slug} value={pr.slug}>
                          {pr.preset_name} ({pr.region} · SVI {pr.svi_current_score}점)
                        </option>
                      ))
                    )}
                  </select>
                  <button
                    type="button"
                    disabled={!selectedPresetSlug}
                    onClick={() => void handleActivatePreset()}
                    className="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-[#0F2942] text-white disabled:opacity-40"
                  >
                    불러오기(전환)
                  </button>
                  <button
                    type="button"
                    disabled={!selectedPresetSlug}
                    onClick={() => void handleDeletePreset()}
                    className="px-2 py-1.5 rounded-lg text-xs font-semibold border border-slate-300 text-slate-600 hover:text-rose-600 disabled:opacity-40"
                  >
                    삭제
                  </button>
                  <div className="flex-1 flex items-center gap-1.5 min-w-[220px]">
                    <input
                      type="text"
                      aria-label="새 고객사 프리셋 이름"
                      value={newPresetName}
                      onChange={(e) => setNewPresetName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.nativeEvent.isComposing) return;
                      }}
                      placeholder="새 프리셋명 (미입력 시 현재 기업명)"
                      className="flex-1 px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 bg-white"
                    />
                    <button
                      type="button"
                      onClick={() => void handleSavePreset()}
                      className="px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-100"
                    >
                      프리셋 저장
                    </button>
                  </div>
                </div>
              </fieldset>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                <div>
                  <label htmlFor="prof-name" className="block text-xs font-bold text-slate-700 mb-1">기업명</label>
                  <input
                    id="prof-name"
                    type="text"
                    required
                    value={profile.name}
                    onChange={(e) => setProfile({ ...profile, name: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
                  />
                </div>
                <div>
                  <label htmlFor="prof-ceo" className="block text-xs font-bold text-slate-700 mb-1">대표자명</label>
                  <input
                    id="prof-ceo"
                    type="text"
                    value={profile.ceo}
                    onChange={(e) => setProfile({ ...profile, ceo: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
                  />
                </div>
                <div>
                  <label htmlFor="prof-bizno" className="block text-xs font-bold text-slate-700 mb-1">사업자등록번호</label>
                  <input
                    id="prof-bizno"
                    type="text"
                    inputMode="numeric"
                    autoCorrect="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={profile.biz_reg_no}
                    onChange={(e) => setProfile({ ...profile, biz_reg_no: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
                <div>
                  <label htmlFor="prof-type" className="block text-xs font-bold text-slate-700 mb-1">사회적기업 유형</label>
                  <input
                    id="prof-type"
                    type="text"
                    value={profile.social_type}
                    onChange={(e) => setProfile({ ...profile, social_type: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
                  />
                </div>
                <div>
                  <label htmlFor="prof-vuln" className="block text-xs font-bold text-slate-700 mb-1">
                    취약계층 고용비율 (%) — 30% 기준
                  </label>
                  <input
                    id="prof-vuln"
                    type="text"
                    inputMode="decimal"
                    value={profile.vulnerable_employment_ratio}
                    onChange={(e) => setProfile({ ...profile, vulnerable_employment_ratio: Number(e.target.value) || 0 })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
                <div>
                  <label htmlFor="prof-region" className="block text-xs font-bold text-slate-700 mb-1">본사 소재지 (지역제한 필터)</label>
                  <input
                    id="prof-region"
                    type="text"
                    value={profile.region}
                    onChange={(e) => setProfile({ ...profile, region: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div>
                  <label htmlFor="prof-min-amt" className="block text-xs font-bold text-slate-700 mb-1">투찰 하한 금액(원)</label>
                  <input
                    id="prof-min-amt"
                    type="text"
                    inputMode="numeric"
                    value={profile.min_bid_amount}
                    onChange={(e) => setProfile({ ...profile, min_bid_amount: Number(e.target.value.replace(/\D/g, '')) || 0 })}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
                <div>
                  <label htmlFor="prof-max-amt" className="block text-xs font-bold text-slate-700 mb-1">투찰 상한 금액(원)</label>
                  <input
                    id="prof-max-amt"
                    type="text"
                    inputMode="numeric"
                    value={profile.max_bid_amount}
                    onChange={(e) => setProfile({ ...profile, max_bid_amount: Number(e.target.value.replace(/\D/g, '')) || 0 })}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
                <div>
                  <label htmlFor="prof-svi-cur" className="block text-xs font-bold text-slate-700 mb-1">현재 SVI 점수</label>
                  <input
                    id="prof-svi-cur"
                    type="text"
                    inputMode="decimal"
                    value={profile.svi_current_score}
                    onChange={(e) => setProfile({ ...profile, svi_current_score: Number(e.target.value) || 0 })}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
                <div>
                  <label htmlFor="prof-svi-tgt" className="block text-xs font-bold text-slate-700 mb-1">목표 SVI 점수</label>
                  <input
                    id="prof-svi-tgt"
                    type="text"
                    inputMode="decimal"
                    value={profile.svi_target_score}
                    onChange={(e) => setProfile({ ...profile, svi_target_score: Number(e.target.value) || 0 })}
                    className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300 tabular-nums"
                  />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-4 pt-1">
                <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                  <input
                    type="checkbox"
                    checked={profile.is_certified_social}
                    onChange={(e) => setProfile({ ...profile, is_certified_social: e.target.checked })}
                    className="accent-[#0D9488]"
                  />
                  인증·예비 사회적기업
                </label>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                  <input
                    type="checkbox"
                    checked={profile.is_women_owned}
                    onChange={(e) => setProfile({ ...profile, is_women_owned: e.target.checked })}
                    className="accent-[#0D9488]"
                  />
                  여성기업 확인
                </label>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                  <input
                    type="checkbox"
                    checked={profile.is_disabled_owned}
                    onChange={(e) => setProfile({ ...profile, is_disabled_owned: e.target.checked })}
                    className="accent-[#0D9488]"
                  />
                  장애인기업·표준사업장
                </label>
                <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                  <input
                    type="checkbox"
                    checked={profile.include_nationwide}
                    onChange={(e) => setProfile({ ...profile, include_nationwide: e.target.checked })}
                    className="accent-[#0D9488]"
                  />
                  전국 공고 포함
                </label>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="prof-codes" className="block text-xs font-bold text-slate-700 mb-1">
                    복수 투찰가능 업종코드 (쉼표 구분)
                  </label>
                  <input
                    id="prof-codes"
                    type="text"
                    autoCorrect="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={industryRaw}
                    onChange={(e) => setIndustryRaw(e.target.value)}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-mono"
                  />
                </div>
                <div>
                  <label htmlFor="prof-dp" className="block text-xs font-bold text-slate-700 mb-1">
                    직접생산확인증명서 보유 품목 (쉼표 구분)
                  </label>
                  <input
                    id="prof-dp"
                    type="text"
                    value={dpRaw}
                    onChange={(e) => setDpRaw(e.target.value)}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="prof-cert" className="block text-xs font-bold text-slate-700 mb-1">보유 인증 (쉼표 구분)</label>
                <input
                  id="prof-cert"
                  type="text"
                  value={certRaw}
                  onChange={(e) => setCertRaw(e.target.value)}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                />
              </div>

              <div>
                <label htmlFor="prof-sum" className="block text-xs font-bold text-slate-700 mb-1">기업 핵심 역량 요약</label>
                <textarea
                  id="prof-sum"
                  rows={3}
                  value={profile.summary}
                  onChange={(e) => setProfile({ ...profile, summary: e.target.value })}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                />
              </div>

              {/* 과거 주요 수행 실적 에디터 */}
              <fieldset className="p-3.5 rounded-lg border border-slate-200 space-y-2.5">
                <div className="flex items-center justify-between">
                  <legend className="text-xs font-bold text-[#0F2942] px-1">
                    과거 주요 수행 실적 (제안서·사업계획서 RAG 자동 인용)
                  </legend>
                  <button
                    type="button"
                    onClick={() =>
                      setProfile({
                        ...profile,
                        past_projects: [
                          ...profile.past_projects,
                          { year: '2026', client: '', title: '', amount: 0, outcome: '' },
                        ],
                      })
                    }
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-[#0F2942]"
                  >
                    <Plus className="w-3.5 h-3.5" /> 수행 실적 추가
                  </button>
                </div>

                {profile.past_projects.length === 0 ? (
                  <p className="text-xs text-slate-500">등록된 수행 실적이 없습니다. 실적을 추가하면 AI 초안에 수치와 함께 인용됩니다.</p>
                ) : (
                  <div className="space-y-2">
                    {profile.past_projects.map((proj, idx) => (
                      <div key={idx} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center bg-slate-50 p-2 rounded-lg">
                        <input
                          type="text"
                          inputMode="numeric"
                          aria-label={`실적 ${idx + 1} 연도`}
                          placeholder="연도"
                          value={proj.year}
                          onChange={(e) => {
                            const next = profile.past_projects.map((item, i) =>
                              i === idx ? { ...item, year: e.target.value } : item
                            );
                            setProfile({ ...profile, past_projects: next });
                          }}
                          className="md:col-span-1 px-2 py-1 text-xs rounded border border-slate-300 bg-white tabular-nums"
                        />
                        <input
                          type="text"
                          aria-label={`실적 ${idx + 1} 발주처`}
                          placeholder="발주처"
                          value={proj.client}
                          onChange={(e) => {
                            const next = profile.past_projects.map((item, i) =>
                              i === idx ? { ...item, client: e.target.value } : item
                            );
                            setProfile({ ...profile, past_projects: next });
                          }}
                          className="md:col-span-2 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <input
                          type="text"
                          aria-label={`실적 ${idx + 1} 사업명`}
                          placeholder="사업명"
                          value={proj.title}
                          onChange={(e) => {
                            const next = profile.past_projects.map((item, i) =>
                              i === idx ? { ...item, title: e.target.value } : item
                            );
                            setProfile({ ...profile, past_projects: next });
                          }}
                          className="md:col-span-4 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <input
                          type="text"
                          inputMode="numeric"
                          aria-label={`실적 ${idx + 1} 계약금액`}
                          placeholder="금액(원)"
                          value={proj.amount}
                          onChange={(e) => {
                            const val = Number(e.target.value.replace(/\D/g, '')) || 0;
                            const next = profile.past_projects.map((item, i) =>
                              i === idx ? { ...item, amount: val } : item
                            );
                            setProfile({ ...profile, past_projects: next });
                          }}
                          className="md:col-span-2 px-2 py-1 text-xs rounded border border-slate-300 bg-white tabular-nums"
                        />
                        <input
                          type="text"
                          aria-label={`실적 ${idx + 1} 성과`}
                          placeholder="핵심 성과(만족도·고용)"
                          value={proj.outcome}
                          onChange={(e) => {
                            const next = profile.past_projects.map((item, i) =>
                              i === idx ? { ...item, outcome: e.target.value } : item
                            );
                            setProfile({ ...profile, past_projects: next });
                          }}
                          className="md:col-span-2 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <button
                          type="button"
                          aria-label={`실적 ${idx + 1} 삭제`}
                          onClick={() =>
                            setProfile({
                              ...profile,
                              past_projects: profile.past_projects.filter((_, i) => i !== idx),
                            })
                          }
                          className="md:col-span-1 p-1 text-slate-400 hover:text-rose-600 justify-self-end"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </fieldset>
            </form>
          )}

          {tab === 'llm' && cfg && (() => {
            const chatModels: ModelCatalogEntry[] = (catalog?.models || []).filter((m) => m.category === 'chat');
            const embModels: ModelCatalogEntry[] = (catalog?.models || []).filter((m) => m.category === 'embedding');

            const fastInCatalog = chatModels.some((m) => m.model_id === cfg.llm.fast_model);
            const strongInCatalog = chatModels.some((m) => m.model_id === cfg.llm.strong_model);
            const embInCatalog = embModels.some((m) => m.model_id === cfg.llm.embedding_model);

            const showFastCustom = fastCustomOpen || !fastInCatalog;
            const showStrongCustom = strongCustomOpen || !strongInCatalog;
            const showEmbCustom = embCustomOpen || !embInCatalog;

            const fastSelectVal = showFastCustom ? '__custom__' : cfg.llm.fast_model;
            const strongSelectVal = showStrongCustom ? '__custom__' : cfg.llm.strong_model;
            const embSelectVal = showEmbCustom ? '__custom__' : cfg.llm.embedding_model;

            const activeFastEntry = chatModels.find((m) => m.model_id === cfg.llm.fast_model);
            const activeStrongEntry = chatModels.find((m) => m.model_id === cfg.llm.strong_model);
            const activeEmbEntry = embModels.find((m) => m.model_id === cfg.llm.embedding_model);

            const formatOptionLabel = (m: ModelCatalogEntry, recId?: string) => {
              const recPrefix = recId && m.model_id === recId ? '★ 권장 · ' : '';
              const priceStr =
                m.input_price_per_1m !== null && m.output_price_per_1m !== null
                  ? ` ($${m.input_price_per_1m} / $${m.output_price_per_1m})`
                  : ' (단가 미등록)';
              const liveNote = m.verified_live === false ? ' · API 미확인' : '';
              return `${recPrefix}${m.model_id} — ${m.tier_badge}${priceStr}${liveNote}`;
            };

            const renderChatOptGroups = (role: 'fast' | 'strong') => {
              const recId = role === 'fast' ? catalog?.recommended_fast : catalog?.recommended_strong;
              const fastList = chatModels.filter((m) => !m.deprecated && m.recommended_tier === 'fast');
              const strongList = chatModels.filter((m) => !m.deprecated && m.recommended_tier === 'strong');
              const frontierList = chatModels.filter((m) => !m.deprecated && m.recommended_tier === 'frontier');
              const legacyList = chatModels.filter((m) => m.deprecated);

              const groups =
                role === 'fast'
                  ? [
                      { label: '고속·경량 추천 (공고 채점 · OCR)', items: fastList },
                      { label: '기본 고품질 (Flash)', items: strongList },
                      { label: 'Pro · Gemini 4 최상위', items: frontierList },
                      { label: '지원 종료 예정 (Legacy)', items: legacyList },
                    ]
                  : [
                      { label: '고품질 기본 추천 (제안서 · 사업계획서)', items: strongList },
                      { label: 'Pro 이상 · Gemini 4 최상위 추론', items: frontierList },
                      { label: '고속·경량 (비용 절감)', items: fastList },
                      { label: '지원 종료 예정 (Legacy)', items: legacyList },
                    ];

              return (
                <>
                  {groups
                    .filter((g) => g.items.length > 0)
                    .map((g) => (
                      <optgroup key={g.label} label={g.label}>
                        {g.items.map((m) => (
                          <option key={m.model_id} value={m.model_id}>
                            {formatOptionLabel(m, recId)}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  <option value="__custom__">직접 입력 (커스텀 / 신규 모델 ID)...</option>
                </>
              );
            };

            const syncErrorActive =
              (lastSyncResult && !lastSyncResult.ok) ||
              (catalog !== null &&
                ['schema_changed', 'partial_schema_changed', 'network_error', 'http_error'].includes(
                  catalog.last_sync_status
                ));
            const syncBannerCode =
              (lastSyncResult && !lastSyncResult.ok ? lastSyncResult.error_code : catalog?.error_code) || '';
            const syncBannerMsg =
              (lastSyncResult && !lastSyncResult.ok ? lastSyncResult.message : catalog?.last_sync_message) || '';
            const syncBannerHint =
              (lastSyncResult && !lastSyncResult.ok ? lastSyncResult.recovery_hint : catalog?.recovery_hint) || '';

            const pricingKeys = Array.from(
              new Set<string>([
                cfg.llm.fast_model,
                cfg.llm.strong_model,
                cfg.llm.embedding_model,
                ...(catalog?.models.map((m) => m.model_id) || []),
                ...Object.keys(cfg.llm.pricing_per_1m || {}),
              ])
            ).filter(Boolean);

            return (
              <div className="space-y-5">
                <form onSubmit={handleSaveConfig} className="space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-slate-200">
                    <h2 className="text-sm font-bold text-[#0F2942]">LLM · 임베딩 모델 및 토큰 단가 설정</h2>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={syncingModels}
                        onClick={() => void handleSyncModels()}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-[#0F2942]/30 text-[#0F2942] bg-slate-50 hover:bg-slate-100 disabled:opacity-50"
                      >
                        <Globe className={`w-3.5 h-3.5 ${syncingModels ? 'animate-spin' : ''}`} />
                        {syncingModels ? '공식 사이트 확인 중...' : '공식 사이트 모델·단가 최신화'}
                      </button>
                      <button
                        type="button"
                        disabled={Boolean(testingApi.gemini)}
                        onClick={() => void handleTestApi('gemini')}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-300 hover:bg-slate-50 disabled:opacity-50"
                      >
                        <RefreshCw className={`w-3.5 h-3.5 ${testingApi.gemini ? 'animate-spin' : ''}`} />
                        {testingApi.gemini ? '통신 테스트 중...' : 'Gemini API 통신 테스트'}
                      </button>
                      <button
                        type="submit"
                        className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700"
                      >
                        <Save className="w-3.5 h-3.5" /> 설정 저장
                      </button>
                    </div>
                  </div>

                  {/* 공식 사이트 동기화 상태 / 사이트 구조 변경 오류 알림 */}
                  {syncErrorActive && (
                    <div
                      role="alert"
                      className="p-3.5 rounded-lg bg-[#FEF2F2] border border-[#DC2626]/40 text-xs text-rose-950 space-y-1.5"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 font-bold text-[#DC2626]">
                          <AlertOctagon className="w-4 h-4 shrink-0" />
                          <span>
                            {syncBannerCode === 'SITE_STRUCTURE_CHANGED'
                              ? '공식 단가 사이트 구조 변경 감지 (기존 설정·단가 보존됨)'
                              : `공식 사이트 모델 동기화 실패 (${syncBannerCode || 'ERROR'})`}
                          </span>
                        </div>
                        <a
                          href="https://aistudio.google.com/docs/pricing"
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-[11px] font-semibold text-[#0F2942] underline hover:text-[#0D9488]"
                        >
                          공식 단가 페이지 직접 확인 <ExternalLink className="w-3 h-3" />
                        </a>
                      </div>
                      <p className="text-rose-900 leading-relaxed">{syncBannerMsg}</p>
                      {syncBannerHint && (
                        <p className="text-[11px] text-rose-800 bg-white/70 px-2.5 py-1.5 rounded border border-rose-200">
                          <strong>안내:</strong> {syncBannerHint}
                        </p>
                      )}
                    </div>
                  )}

                  {!syncErrorActive && catalog && (
                    <div
                      role="status"
                      aria-live="polite"
                      className={`px-3 py-2 rounded-lg border text-[11px] flex flex-wrap items-center justify-between gap-2 ${
                        catalog.last_sync_status === 'ok'
                          ? 'bg-[#ECFDF5] border-[#059669]/30 text-emerald-900'
                          : 'bg-slate-50 border-slate-200 text-slate-600'
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-[#059669] shrink-0" />
                        <span>{catalog.last_sync_message}</span>
                      </div>
                      {catalog.updated_at && (
                        <span className="font-mono text-[10px] text-slate-500 tabular-nums">
                          최근 동기화: {catalog.updated_at}
                        </span>
                      )}
                    </div>
                  )}

                  {/* 활성 모델 지원 종료 / API 미확인 / 단가 미등록 경고 */}
                  {catalog && catalog.active_warnings.length > 0 && (
                    <div
                      role={catalog.active_warnings.some((w) => w.severity === 'error') ? 'alert' : 'status'}
                      className="p-3.5 rounded-lg bg-[#FFFBEB] border border-[#D97706]/40 text-xs text-amber-950 space-y-2"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 font-bold text-[#D97706]">
                          <AlertTriangle className="w-4 h-4 shrink-0" />
                          <span>현재 선택된 모델 점검 필요 ({catalog.active_warnings.length}건)</span>
                        </div>
                        {catalog.active_warnings.some((w) => Boolean(w.suggested_model)) && (
                          <button
                            type="button"
                            onClick={() => void handleApplyRecommendedModels()}
                            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F]"
                          >
                            <Sparkles className="w-3.5 h-3.5" />
                            권장 모델로 자동 전환
                          </button>
                        )}
                      </div>
                      <ul className="list-disc pl-5 space-y-1 text-[11px] text-amber-900">
                        {catalog.active_warnings.map((w) => (
                          <li key={`${w.slot}-${w.code}`}>{w.message}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-between">
                    <div>
                      <label htmlFor="llm-sim" className="text-xs font-bold text-slate-800 cursor-pointer">
                        시뮬레이션(데모) 모드
                      </label>
                      <p className="text-[11px] text-slate-500">
                        API 키 없이도 제안서/사업계획서 스트리밍과 RAG 검색을 즉시 테스트할 수 있습니다.
                      </p>
                    </div>
                    <input
                      id="llm-sim"
                      name="simulation_mode"
                      type="checkbox"
                      checked={cfg.llm.simulation_mode}
                      onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, simulation_mode: e.target.checked } })}
                      className="w-4 h-4 accent-[#0D9488]"
                    />
                  </div>

                  <div>
                    <label htmlFor="llm-apikey" className="block text-xs font-bold text-slate-700 mb-1">
                      Gemini API Key
                    </label>
                    <div className="flex gap-2">
                      <input
                        id="llm-apikey"
                        name="api_key"
                        type={showGeminiKey ? 'text' : 'password'}
                        autoCorrect="off"
                        autoCapitalize="none"
                        spellCheck={false}
                        value={cfg.llm.api_key}
                        onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, api_key: e.target.value } })}
                        placeholder="AIza..."
                        className="flex-1 px-3 py-2 text-xs rounded-lg border border-slate-300 font-mono"
                      />
                      <button
                        type="button"
                        aria-pressed={showGeminiKey}
                        aria-label={showGeminiKey ? 'API 키 숨기기' : 'API 키 보기'}
                        onClick={() => setShowGeminiKey((v) => !v)}
                        className="px-3 py-2 rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50"
                      >
                        {showGeminiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                    {apiTestResults.gemini && (
                      <div
                        role={apiTestResults.gemini.ok ? 'status' : 'alert'}
                        aria-live="polite"
                        className={`mt-2 p-3 rounded-lg border text-xs space-y-1.5 ${
                          apiTestResults.gemini.ok
                            ? 'bg-[#ECFDF5] border-[#059669]/40 text-emerald-950'
                            : 'bg-[#FEF2F2] border-[#DC2626]/40 text-rose-950'
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2 font-bold">
                            {apiTestResults.gemini.ok ? (
                              <CheckCircle2 className="w-4 h-4 text-[#059669] shrink-0" />
                            ) : (
                              <AlertOctagon className="w-4 h-4 text-[#DC2626] shrink-0" />
                            )}
                            <span
                              className={`px-2 py-0.5 rounded-full text-[11px] font-extrabold ${
                                apiTestResults.gemini.ok
                                  ? 'bg-[#059669] text-white'
                                  : 'bg-[#DC2626] text-white'
                              }`}
                            >
                              {apiTestResults.gemini.ok ? '정상' : '비정상'}
                            </span>
                            <span>
                              {apiTestResults.gemini.ok ? 'Gemini API 연결 확인 완료' : 'Gemini API 연결 오류'}
                            </span>
                          </div>
                          <span className="font-mono text-[11px] text-slate-600 tabular-nums">
                            {apiTestResults.gemini.model ? `${apiTestResults.gemini.model}` : ''}
                            {typeof apiTestResults.gemini.elapsed_ms === 'number'
                              ? ` · ${apiTestResults.gemini.elapsed_ms}ms`
                              : ''}
                            {` · ${apiTestResults.gemini.tested_at.replace('T', ' ').slice(0, 19)}`}
                          </span>
                        </div>
                        <p className="text-[11px] leading-relaxed">{apiTestResults.gemini.message}</p>
                        {apiTestResults.gemini.ok && cfg.llm.simulation_mode && (
                          <p className="text-[11px] text-amber-900 bg-amber-50 border border-amber-300 rounded px-2.5 py-1.5">
                            <strong>참고:</strong> 현재 상단 <strong>시뮬레이션(데모) 모드</strong>가 켜져 있어 실제 문서 생성에는 API가 호출되지 않습니다. 실사용 시 체크를 해제하고 <strong>[설정 저장]</strong>을 누르세요.
                          </p>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="llm-fast" className="block text-xs font-bold text-slate-700 mb-1">
                        고속 경량 모델 (공고 점수 · 스캔 OCR)
                      </label>
                      <select
                        id="llm-fast"
                        name="fast_model"
                        aria-describedby="llm-fast-note"
                        value={fastSelectVal}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '__custom__') {
                            setFastCustomOpen(true);
                          } else {
                            setFastCustomOpen(false);
                            setCfg({ ...cfg, llm: { ...cfg.llm, fast_model: v } });
                          }
                        }}
                        className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-mono bg-white"
                      >
                        {renderChatOptGroups('fast')}
                      </select>
                      {showFastCustom && (
                        <div className="mt-1.5">
                          <label htmlFor="llm-fast-custom" className="sr-only">
                            고속 경량 모델 ID 직접 입력
                          </label>
                          <input
                            id="llm-fast-custom"
                            type="text"
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            placeholder="예: gemini-4.5-flash-lite"
                            value={cfg.llm.fast_model}
                            onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, fast_model: e.target.value } })}
                            className="w-full px-3 py-1.5 text-xs rounded-lg border border-[#0D9488] font-mono bg-white"
                          />
                        </div>
                      )}
                      <p id="llm-fast-note" className="text-[11px] text-slate-500 mt-1">
                        {activeFastEntry
                          ? `${activeFastEntry.price_note || activeFastEntry.tier_badge}${
                              activeFastEntry.supports_thinking_level && !activeFastEntry.supports_temperature
                                ? ' · Gemini 3+/4 사고 수준(thinking_level) 자동 적용'
                                : ''
                            }`
                          : '커스텀 모델 ID 사용 중 — 아래 단가표에 1M 단가를 등록하면 비용이 자동 집계됩니다.'}
                      </p>
                    </div>

                    <div>
                      <label htmlFor="llm-strong" className="block text-xs font-bold text-slate-700 mb-1">
                        고품질 작성 모델 (제안서 · 사업계획서)
                      </label>
                      <select
                        id="llm-strong"
                        name="strong_model"
                        aria-describedby="llm-strong-note"
                        value={strongSelectVal}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '__custom__') {
                            setStrongCustomOpen(true);
                          } else {
                            setStrongCustomOpen(false);
                            setCfg({ ...cfg, llm: { ...cfg.llm, strong_model: v } });
                          }
                        }}
                        className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-mono bg-white"
                      >
                        {renderChatOptGroups('strong')}
                      </select>
                      {showStrongCustom && (
                        <div className="mt-1.5">
                          <label htmlFor="llm-strong-custom" className="sr-only">
                            고품질 작성 모델 ID 직접 입력
                          </label>
                          <input
                            id="llm-strong-custom"
                            type="text"
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            placeholder="예: gemini-4-neon"
                            value={cfg.llm.strong_model}
                            onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, strong_model: e.target.value } })}
                            className="w-full px-3 py-1.5 text-xs rounded-lg border border-[#0D9488] font-mono bg-white"
                          />
                        </div>
                      )}
                      <p id="llm-strong-note" className="text-[11px] text-slate-500 mt-1">
                        {activeStrongEntry
                          ? `${activeStrongEntry.price_note || activeStrongEntry.tier_badge}${
                              activeStrongEntry.supports_thinking_level && !activeStrongEntry.supports_temperature
                                ? ' · Gemini 3+/4 사고 수준(thinking_level) 자동 적용'
                                : ''
                            }`
                          : '커스텀 모델 ID 사용 중 — 아래 단가표에 1M 단가를 등록하면 비용이 자동 집계됩니다.'}
                      </p>
                    </div>

                    <div>
                      <label htmlFor="llm-emb" className="block text-xs font-bold text-slate-700 mb-1">
                        임베딩 모델 (Matryoshka 768차원)
                      </label>
                      <select
                        id="llm-emb"
                        name="embedding_model"
                        aria-describedby="llm-emb-note"
                        value={embSelectVal}
                        onChange={(e) => {
                          const v = e.target.value;
                          if (v === '__custom__') {
                            setEmbCustomOpen(true);
                          } else {
                            setEmbCustomOpen(false);
                            setCfg({ ...cfg, llm: { ...cfg.llm, embedding_model: v } });
                          }
                        }}
                        className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 font-mono bg-white"
                      >
                        {embModels.length > 0 ? (
                          embModels.map((m) => (
                            <option key={m.model_id} value={m.model_id}>
                              {m.model_id === catalog?.recommended_embedding ? '★ 권장 · ' : ''}
                              {m.model_id} (${m.input_price_per_1m ?? '?'}/1M)
                            </option>
                          ))
                        ) : (
                          <>
                            <option value="gemini-embedding-001">gemini-embedding-001 ($0.15/1M)</option>
                            <option value="gemini-embedding-2">gemini-embedding-2 ($0.20/1M)</option>
                          </>
                        )}
                        <option value="__custom__">직접 입력 (커스텀 임베딩 ID)...</option>
                      </select>
                      {showEmbCustom && (
                        <div className="mt-1.5">
                          <label htmlFor="llm-emb-custom" className="sr-only">
                            임베딩 모델 ID 직접 입력
                          </label>
                          <input
                            id="llm-emb-custom"
                            type="text"
                            autoCorrect="off"
                            autoCapitalize="none"
                            spellCheck={false}
                            value={cfg.llm.embedding_model}
                            onChange={(e) => setCfg({ ...cfg, llm: { ...cfg.llm, embedding_model: e.target.value } })}
                            className="w-full px-3 py-1.5 text-xs rounded-lg border border-[#0D9488] font-mono bg-white"
                          />
                        </div>
                      )}
                      <p id="llm-emb-note" className="text-[11px] text-slate-500 mt-1">
                        {activeEmbEntry?.price_note || '변경 후에는 [로컬 하이브리드 RAG 관리] 탭에서 전체 재인덱싱이 필요합니다.'}
                      </p>
                    </div>

                    <div>
                      <label htmlFor="llm-krw" className="block text-xs font-bold text-slate-700 mb-1">
                        환율 (USD → KRW)
                      </label>
                      <input
                        id="llm-krw"
                        name="usd_to_krw"
                        type="text"
                        inputMode="decimal"
                        value={cfg.llm.usd_to_krw}
                        onChange={(e) =>
                          setCfg({ ...cfg, llm: { ...cfg.llm, usd_to_krw: Number(e.target.value) || 1380 } })
                        }
                        className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 tabular-nums"
                      />
                    </div>

                    <div>
                      <label htmlFor="llm-budget" className="block text-xs font-bold text-slate-700 mb-1">
                        월 LLM 예산 상한 (원, 0 = 무제한)
                      </label>
                      <input
                        id="llm-budget"
                        name="monthly_budget_krw"
                        type="text"
                        inputMode="numeric"
                        aria-describedby="llm-budget-note"
                        value={cfg.llm.monthly_budget_krw ?? 0}
                        onChange={(e) =>
                          setCfg({
                            ...cfg,
                            llm: { ...cfg.llm, monthly_budget_krw: Number(e.target.value.replace(/[^\d]/g, '')) || 0 },
                          })
                        }
                        className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 tabular-nums"
                      />
                      <p
                        id="llm-budget-note"
                        className={`text-[11px] mt-1 tabular-nums ${budgetStatus?.exceeded ? 'text-rose-700 font-semibold' : 'text-slate-500'}`}
                      >
                        {budgetStatus
                          ? `이번 달 Live 사용액 ${Math.round(budgetStatus.used_krw).toLocaleString()}원` +
                            (budgetStatus.monthly_budget_krw > 0
                              ? budgetStatus.exceeded
                                ? ' — 예산 소진: 신규 AI 생성이 차단됩니다.'
                                : ` · 잔여 ${Math.round(budgetStatus.remaining_krw ?? 0).toLocaleString()}원`
                              : '')
                          : '예산을 넘으면 초안 생성·보완·재인덱싱 등 신규 Live 호출이 차단됩니다.'}
                      </p>
                    </div>
                  </div>

                  {/* 모델별 1M 토큰 단가표 (직접 편집 + 재계산) */}
                  <fieldset className="p-3.5 rounded-xl border border-slate-200 bg-slate-50/60 space-y-3">
                    <legend className="text-xs font-bold text-[#0F2942] px-1">
                      모델별 토큰 단가표 (USD / 1M 토큰 · 출력 단가에 사고 Thinking 토큰 포함 과금)
                    </legend>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-[11px] text-slate-600">
                        단가 변경 후 <strong>[설정 저장]</strong>을 누르면 이후 호출부터 즉시 적용되며, 기존 기록도 재계산할 수 있습니다.
                      </p>
                      <button
                        type="button"
                        disabled={recalculatingUsage}
                        onClick={() => void handleRecalculateUsage()}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-[#0F2942] hover:bg-slate-100 disabled:opacity-50"
                      >
                        <Calculator className="w-3.5 h-3.5 text-[#0D9488]" />
                        {recalculatingUsage ? '재계산 중...' : '단가 기준 기존 사용량 재계산'}
                      </button>
                    </div>

                    <div
                      tabIndex={0}
                      role="region"
                      aria-labelledby="pricing-table-caption"
                      className="overflow-x-auto bg-white border border-slate-200 rounded-lg"
                    >
                      <table className="w-full text-xs text-left border-collapse">
                        <caption id="pricing-table-caption" className="sr-only">
                          모델별 100만 토큰당 입력 및 출력 USD 단가 설정 표
                        </caption>
                        <thead className="bg-slate-100 text-slate-700 border-b border-slate-200">
                          <tr>
                            <th scope="col" className="px-3 py-2 font-bold">모델 ID</th>
                            <th scope="col" className="px-3 py-2 font-bold">입력 ($ / 1M)</th>
                            <th scope="col" className="px-3 py-2 font-bold">출력 ($ / 1M)</th>
                            <th scope="col" className="px-3 py-2 font-bold text-right">출력 1M 원화 환산</th>
                            <th scope="col" className="px-3 py-2 font-bold text-center">상태</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {pricingKeys.map((mid) => {
                            const draft = pricingDrafts[mid] ?? {
                              input: cfg.llm.pricing_per_1m?.[mid] ? String(cfg.llm.pricing_per_1m[mid].input) : '',
                              output: cfg.llm.pricing_per_1m?.[mid] ? String(cfg.llm.pricing_per_1m[mid].output) : '',
                            };
                            const isFast = mid === cfg.llm.fast_model;
                            const isStrong = mid === cfg.llm.strong_model;
                            const isEmb = mid === cfg.llm.embedding_model;
                            const catMeta = catalog?.models.find((m) => m.model_id === mid);
                            const outNum = Number(draft.output || 0);
                            const krwPer1M = Number.isFinite(outNum) ? outNum * cfg.llm.usd_to_krw : 0;
                            const isMissing = draft.input.trim() === '' && draft.output.trim() === '';
                            const isCustom = !catMeta && !isFast && !isStrong && !isEmb;

                            return (
                              <tr key={mid} className={isFast || isStrong || isEmb ? 'bg-teal-50/30' : ''}>
                                <th scope="row" className="px-3 py-2 font-mono font-semibold text-[#0F2942] whitespace-nowrap">
                                  <div className="flex flex-wrap items-center gap-1.5">
                                    <span>{mid}</span>
                                    {isFast && (
                                      <span className="px-1.5 py-0.5 rounded bg-sky-100 text-sky-900 text-[10px] font-sans font-bold">
                                        사용 중(경량)
                                      </span>
                                    )}
                                    {isStrong && (
                                      <span className="px-1.5 py-0.5 rounded bg-teal-100 text-teal-900 text-[10px] font-sans font-bold">
                                        사용 중(고품질)
                                      </span>
                                    )}
                                    {isEmb && (
                                      <span className="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-900 text-[10px] font-sans font-bold">
                                        사용 중(임베딩)
                                      </span>
                                    )}
                                    {catMeta?.deprecated && (
                                      <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 text-[10px] font-sans font-bold">
                                        종료 예정
                                      </span>
                                    )}
                                  </div>
                                </th>
                                <td className="px-3 py-1.5">
                                  <input
                                    type="text"
                                    inputMode="decimal"
                                    aria-label={`${mid} 입력 단가 USD`}
                                    placeholder="0.00"
                                    value={draft.input}
                                    onChange={(e) =>
                                      setPricingDrafts((prev) => ({
                                        ...prev,
                                        [mid]: { input: e.target.value, output: draft.output },
                                      }))
                                    }
                                    className="w-24 px-2 py-1 text-xs rounded border border-slate-300 font-mono tabular-nums bg-white"
                                  />
                                </td>
                                <td className="px-3 py-1.5">
                                  <input
                                    type="text"
                                    inputMode="decimal"
                                    aria-label={`${mid} 출력 단가 USD`}
                                    placeholder="0.00"
                                    value={draft.output}
                                    onChange={(e) =>
                                      setPricingDrafts((prev) => ({
                                        ...prev,
                                        [mid]: { input: draft.input, output: e.target.value },
                                      }))
                                    }
                                    className="w-24 px-2 py-1 text-xs rounded border border-slate-300 font-mono tabular-nums bg-white"
                                  />
                                </td>
                                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-600">
                                  {isMissing ? '-' : `${Math.round(krwPer1M).toLocaleString('ko-KR')}원`}
                                </td>
                                <td className="px-3 py-2 text-center">
                                  {isMissing ? (
                                    <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 text-[10px] font-bold">
                                      단가 미등록
                                    </span>
                                  ) : isCustom ? (
                                    <button
                                      type="button"
                                      aria-label={`${mid} 커스텀 단가 삭제`}
                                      onClick={() => {
                                        const nextMap = { ...cfg.llm.pricing_per_1m };
                                        delete nextMap[mid];
                                        const nextDrafts = { ...pricingDrafts };
                                        delete nextDrafts[mid];
                                        setPricingDrafts(nextDrafts);
                                        setCfg({ ...cfg, llm: { ...cfg.llm, pricing_per_1m: nextMap } });
                                      }}
                                      className="p-1 text-slate-400 hover:text-rose-600"
                                    >
                                      <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                  ) : (
                                    <span className="text-[11px] text-emerald-700 font-semibold">등록됨</span>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>

                    {/* 커스텀 모델 단가 추가 */}
                    <div className="flex flex-wrap items-end gap-2 pt-1">
                      <div className="flex-1 min-w-[180px]">
                        <label htmlFor="new-price-id" className="block text-[11px] font-semibold text-slate-600 mb-0.5">
                          신규/커스텀 모델 ID
                        </label>
                        <input
                          id="new-price-id"
                          type="text"
                          autoCorrect="off"
                          autoCapitalize="none"
                          spellCheck={false}
                          placeholder="예: gemini-4.5-flash"
                          value={newModelId}
                          onChange={(e) => setNewModelId(e.target.value)}
                          className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 font-mono bg-white"
                        />
                      </div>
                      <div className="w-24">
                        <label htmlFor="new-price-in" className="block text-[11px] font-semibold text-slate-600 mb-0.5">
                          입력 ($/1M)
                        </label>
                        <input
                          id="new-price-in"
                          type="text"
                          inputMode="decimal"
                          placeholder="0.75"
                          value={newModelIn}
                          onChange={(e) => setNewModelIn(e.target.value)}
                          className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 font-mono tabular-nums bg-white"
                        />
                      </div>
                      <div className="w-24">
                        <label htmlFor="new-price-out" className="block text-[11px] font-semibold text-slate-600 mb-0.5">
                          출력 ($/1M)
                        </label>
                        <input
                          id="new-price-out"
                          type="text"
                          inputMode="decimal"
                          placeholder="3.75"
                          value={newModelOut}
                          onChange={(e) => setNewModelOut(e.target.value)}
                          className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 font-mono tabular-nums bg-white"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={handleAddCustomPricing}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-[#0F2942] text-white hover:bg-[#1E3A5F]"
                      >
                        <Plus className="w-3.5 h-3.5" /> 단가 추가
                      </button>
                    </div>
                  </fieldset>
                </form>

                {/* 토큰 사용량 및 비용 측정 리포트 (<form> 외부 배치) */}
                <section className="pt-4 border-t border-slate-200 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <h3 id="usage-report-heading" className="text-sm font-bold text-[#0F2942] flex items-center gap-1.5">
                        <Coins className="w-4 h-4 text-amber-600" />
                        토큰 사용량 · 비용 측정 현황
                      </h3>
                      <p className="text-[11px] text-slate-500">
                        실제 API 호출과 시뮬레이션 호출을 구분하고, 사고(Thinking) 토큰까지 포함한 실비를 추적합니다.
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        aria-pressed={usageScope === 'month'}
                        onClick={() => void handleChangeUsageScope('month')}
                        className={`px-2.5 py-1 rounded-md text-xs font-semibold border ${
                          usageScope === 'month'
                            ? 'bg-[#0F2942] text-white border-[#0F2942]'
                            : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        이번 달
                      </button>
                      <button
                        type="button"
                        aria-pressed={usageScope === 'all'}
                        onClick={() => void handleChangeUsageScope('all')}
                        className={`px-2.5 py-1 rounded-md text-xs font-semibold border ${
                          usageScope === 'all'
                            ? 'bg-[#0F2942] text-white border-[#0F2942]'
                            : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                        }`}
                      >
                        전체 누적
                      </button>
                      <button
                        type="button"
                        aria-label="사용량 새로고침"
                        onClick={() => void handleChangeUsageScope(usageScope)}
                        className="p-1.5 rounded-md border border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                      >
                        <RefreshCw className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>

                  {usageReport && (
                    <>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <div className="p-3 rounded-lg border border-slate-200 bg-slate-50">
                          <div className="text-[11px] text-slate-500 font-semibold">총 호출 수</div>
                          <div className="text-lg font-bold text-[#0F2942] tabular-nums mt-0.5">
                            {usageReport.total_calls.toLocaleString('ko-KR')}회
                          </div>
                          <div className="text-[10px] text-slate-500 tabular-nums">
                            실제 {usageReport.live_calls} · 시뮬 {usageReport.simulation_calls}
                          </div>
                        </div>
                        <div className="p-3 rounded-lg border border-slate-200 bg-slate-50">
                          <div className="text-[11px] text-slate-500 font-semibold">총 사용 토큰</div>
                          <div className="text-lg font-bold text-[#0F2942] tabular-nums mt-0.5">
                            {usageReport.total_tokens.toLocaleString('ko-KR')}
                          </div>
                          <div className="text-[10px] text-slate-500 tabular-nums">
                            사고(Thinking) {usageReport.thinking_tokens.toLocaleString('ko-KR')} tok
                          </div>
                        </div>
                        <div className="p-3 rounded-lg border border-slate-200 bg-slate-50">
                          <div className="text-[11px] text-slate-500 font-semibold">누적 환산 비용</div>
                          <div className="text-lg font-bold text-[#0D9488] tabular-nums mt-0.5">
                            {Math.round(usageReport.cost_krw).toLocaleString('ko-KR')}원
                          </div>
                          <div className="text-[10px] text-slate-500 tabular-nums">
                            ${usageReport.cost_usd.toFixed(4)} (실API {Math.round(usageReport.live_cost_krw).toLocaleString('ko-KR')}원)
                          </div>
                        </div>
                        <div
                          className={`p-3 rounded-lg border ${
                            usageReport.unpriced_calls > 0
                              ? 'border-amber-300 bg-amber-50'
                              : 'border-emerald-200 bg-emerald-50/60'
                          }`}
                        >
                          <div className="text-[11px] text-slate-600 font-semibold">단가 미등록 호출</div>
                          <div
                            className={`text-lg font-bold tabular-nums mt-0.5 ${
                              usageReport.unpriced_calls > 0 ? 'text-amber-800' : 'text-emerald-800'
                            }`}
                          >
                            {usageReport.unpriced_calls.toLocaleString('ko-KR')}건
                          </div>
                          <div className="text-[10px] text-slate-600">
                            {usageReport.unpriced_calls > 0
                              ? '최고 등록 단가로 보수 추정 중 · 단가표 등록 후 재계산 권장'
                              : '모든 호출 단가 매핑 완료'}
                          </div>
                        </div>
                      </div>

                      {usageReport.by_model.length > 0 ? (
                        <div
                          tabIndex={0}
                          role="region"
                          aria-labelledby="usage-by-model-caption"
                          className="overflow-x-auto border border-slate-200 rounded-lg"
                        >
                          <table className="w-full text-xs text-left border-collapse">
                            <caption id="usage-by-model-caption" className="sr-only">
                              모델별 토큰 사용량 및 추정 비용 집계 표
                            </caption>
                            <thead className="bg-slate-100 text-slate-700 border-b border-slate-200">
                              <tr>
                                <th scope="col" className="px-3 py-2 font-bold">모델</th>
                                <th scope="col" className="px-3 py-2 font-bold">구분</th>
                                <th scope="col" className="px-3 py-2 font-bold text-right">호출</th>
                                <th scope="col" className="px-3 py-2 font-bold text-right">입력</th>
                                <th scope="col" className="px-3 py-2 font-bold text-right">출력</th>
                                <th scope="col" className="px-3 py-2 font-bold text-right">사고</th>
                                <th scope="col" className="px-3 py-2 font-bold text-right">비용 (원 / USD)</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 bg-white">
                              {usageReport.by_model.map((row) => (
                                <tr key={`${row.model}-${row.provider}`}>
                                  <th scope="row" className="px-3 py-2 font-mono font-semibold text-[#0F2942] whitespace-nowrap">
                                    <span>{row.model}</span>
                                    {!row.has_pricing && (
                                      <span className="ml-1.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 text-[10px] font-sans font-bold">
                                        단가 미등록
                                      </span>
                                    )}
                                  </th>
                                  <td className="px-3 py-2 text-slate-600">
                                    {row.provider === 'simulation' ? '시뮬레이션' : row.provider}
                                  </td>
                                  <td className="px-3 py-2 text-right tabular-nums">{row.calls.toLocaleString('ko-KR')}</td>
                                  <td className="px-3 py-2 text-right tabular-nums">{row.input_tokens.toLocaleString('ko-KR')}</td>
                                  <td className="px-3 py-2 text-right tabular-nums">{row.output_tokens.toLocaleString('ko-KR')}</td>
                                  <td className="px-3 py-2 text-right tabular-nums">{row.thinking_tokens.toLocaleString('ko-KR')}</td>
                                  <td className="px-3 py-2 text-right font-mono tabular-nums">
                                    {Math.round(row.cost_krw).toLocaleString('ko-KR')}원 (${row.cost_usd.toFixed(4)})
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <div className="p-4 rounded-lg border border-dashed border-slate-300 text-center text-xs text-slate-500">
                          아직 기록된 토큰 사용량이 없습니다. 공고 AI 평가나 제안서·사업계획서 초안 생성을 실행하면 모델별 사용량과 비용이 자동 기록됩니다.
                        </div>
                      )}

                      {usageReport.recent_logs.length > 0 && (
                        <details className="text-xs border border-slate-200 rounded-lg bg-slate-50/70">
                          <summary className="px-3 py-2 font-semibold text-slate-700 cursor-pointer">
                            최근 LLM 호출 로그 ({usageReport.recent_logs.length}건)
                          </summary>
                          <div
                            tabIndex={0}
                            role="region"
                            aria-label="최근 LLM 호출 로그 표"
                            className="overflow-x-auto border-t border-slate-200 bg-white"
                          >
                            <table className="w-full text-[11px] text-left border-collapse">
                              <caption className="sr-only">최근 LLM 호출 로그</caption>
                              <thead className="bg-slate-50 text-slate-600 border-b border-slate-200">
                                <tr>
                                  <th scope="col" className="px-2.5 py-1.5 font-semibold">시각</th>
                                  <th scope="col" className="px-2.5 py-1.5 font-semibold">기능</th>
                                  <th scope="col" className="px-2.5 py-1.5 font-semibold">모델</th>
                                  <th scope="col" className="px-2.5 py-1.5 font-semibold text-right">토큰(입/출/사고)</th>
                                  <th scope="col" className="px-2.5 py-1.5 font-semibold text-right">비용</th>
                                </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100">
                                {usageReport.recent_logs.map((lg) => (
                                  <tr key={lg.id}>
                                    <td className="px-2.5 py-1.5 font-mono text-slate-500 whitespace-nowrap">
                                      {lg.created_at.replace('T', ' ').slice(0, 19)}
                                    </td>
                                    <td className="px-2.5 py-1.5 font-mono text-slate-700">{lg.feature}</td>
                                    <td className="px-2.5 py-1.5 font-mono text-[#0F2942]">
                                      {lg.model}
                                      {lg.provider === 'simulation' ? ' (시뮬)' : ''}
                                    </td>
                                    <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-slate-600">
                                      {lg.input_tokens.toLocaleString()} / {lg.output_tokens.toLocaleString()} /{' '}
                                      {lg.thinking_tokens.toLocaleString()}
                                    </td>
                                    <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">
                                      {lg.pricing_missing ? (
                                        <span
                                          className="text-amber-700 font-semibold"
                                          title="단가 미등록 모델: 최고 등록 단가로 보수 추정한 금액"
                                        >
                                          ≈{lg.cost_krw.toFixed(1)}원 (추정)
                                        </span>
                                      ) : (
                                        `${lg.cost_krw.toFixed(1)}원`
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </details>
                      )}
                    </>
                  )}
                </section>
              </div>
            );
          })()}

          {tab === 'public' && cfg && (
            <form onSubmit={handleSaveConfig} className="space-y-4">
              <div className="flex items-center justify-between pb-2 border-b border-slate-200">
                <h2 className="text-sm font-bold text-[#0F2942]">3대 공공 Open API 키 · 엔드포인트 설정</h2>
                <button
                  type="submit"
                  className="inline-flex items-center gap-1 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-[#0D9488] text-white"
                >
                  <Save className="w-3.5 h-3.5" /> 저장
                </button>
              </div>

              <div className="space-y-3">
                <div className="p-3 rounded-lg border border-slate-200 space-y-2">
                  <div className="flex items-center justify-between">
                    <label htmlFor="api-g2b" className="text-xs font-bold text-slate-800">
                      1) 조달청 나라장터 ServiceKey (Encoding/Decoding 자동 정규화 · 7일 분할 조회)
                    </label>
                    <button
                      type="button"
                      disabled={Boolean(testingApi.g2b)}
                      onClick={() => void handleTestApi('g2b')}
                      className="px-2.5 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-50"
                    >
                      {testingApi.g2b ? '테스트 중...' : 'API 통신 테스트'}
                    </button>
                  </div>
                  <div className="flex gap-2">
                    <input
                      id="api-g2b"
                      type={showG2bKey ? 'text' : 'password'}
                      autoCorrect="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      value={cfg.public_api.g2b_service_key}
                      onChange={(e) =>
                        setCfg({ ...cfg, public_api: { ...cfg.public_api, g2b_service_key: e.target.value } })
                      }
                      className="flex-1 px-3 py-1.5 text-xs rounded border border-slate-300 font-mono"
                    />
                    <button
                      type="button"
                      aria-pressed={showG2bKey}
                      aria-label="나라장터 키 표시 전환"
                      onClick={() => setShowG2bKey((v) => !v)}
                      className="px-2.5 py-1.5 rounded border border-slate-300"
                    >
                      {showG2bKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  {apiTestResults.g2b && (
                    <div
                      role={apiTestResults.g2b.ok ? 'status' : 'alert'}
                      className={`px-2.5 py-1.5 rounded border text-[11px] flex items-center gap-2 ${
                        apiTestResults.g2b.ok
                          ? 'bg-[#ECFDF5] border-[#059669]/40 text-emerald-900'
                          : 'bg-[#FEF2F2] border-[#DC2626]/40 text-rose-900'
                      }`}
                    >
                      <span
                        className={`px-1.5 py-0.5 rounded-full text-[10px] font-extrabold text-white ${
                          apiTestResults.g2b.ok ? 'bg-[#059669]' : 'bg-[#DC2626]'
                        }`}
                      >
                        {apiTestResults.g2b.ok ? '정상' : '비정상'}
                      </span>
                      <span className="truncate">{apiTestResults.g2b.message}</span>
                    </div>
                  )}
                </div>

                <div className="p-3 rounded-lg border border-slate-200 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800">
                      2) 한국사회적기업진흥원 통합사업신청 (ID 15165134 · 엔드포인트 설정 바인딩)
                    </span>
                    <button
                      type="button"
                      disabled={Boolean(testingApi.seis)}
                      onClick={() => void handleTestApi('seis')}
                      className="px-2.5 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-50"
                    >
                      {testingApi.seis ? '테스트 중...' : 'API 통신 테스트'}
                    </button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <input
                      type="password"
                      aria-label="진흥원 ServiceKey"
                      autoCorrect="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      placeholder="ServiceKey"
                      value={cfg.public_api.seis_service_key}
                      onChange={(e) =>
                        setCfg({ ...cfg, public_api: { ...cfg.public_api, seis_service_key: e.target.value } })
                      }
                      className="px-3 py-1.5 text-xs rounded border border-slate-300 font-mono"
                    />
                    <input
                      type="url"
                      aria-label="진흥원 엔드포인트 URL"
                      autoCorrect="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      value={cfg.public_api.seis_endpoint_url}
                      onChange={(e) =>
                        setCfg({ ...cfg, public_api: { ...cfg.public_api, seis_endpoint_url: e.target.value } })
                      }
                      className="px-3 py-1.5 text-xs rounded border border-slate-300 font-mono"
                    />
                  </div>
                  {apiTestResults.seis && (
                    <div
                      role={apiTestResults.seis.ok ? 'status' : 'alert'}
                      className={`px-2.5 py-1.5 rounded border text-[11px] flex items-center gap-2 ${
                        apiTestResults.seis.ok
                          ? 'bg-[#ECFDF5] border-[#059669]/40 text-emerald-900'
                          : 'bg-[#FEF2F2] border-[#DC2626]/40 text-rose-900'
                      }`}
                    >
                      <span
                        className={`px-1.5 py-0.5 rounded-full text-[10px] font-extrabold text-white ${
                          apiTestResults.seis.ok ? 'bg-[#059669]' : 'bg-[#DC2626]'
                        }`}
                      >
                        {apiTestResults.seis.ok ? '정상' : '비정상'}
                      </span>
                      <span className="truncate">{apiTestResults.seis.message}</span>
                    </div>
                  )}
                </div>

                <div className="p-3 rounded-lg border border-slate-200 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800">3) 기업마당(Bizinfo) Open API</span>
                    <button
                      type="button"
                      disabled={Boolean(testingApi.bizinfo)}
                      onClick={() => void handleTestApi('bizinfo')}
                      className="px-2.5 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 disabled:opacity-50"
                    >
                      {testingApi.bizinfo ? '테스트 중...' : 'API 통신 테스트'}
                    </button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <input
                      type="password"
                      aria-label="기업마당 crtfcKey"
                      autoCorrect="off"
                      autoCapitalize="none"
                      spellCheck={false}
                      placeholder="crtfcKey"
                      value={cfg.public_api.bizinfo_api_key}
                      onChange={(e) =>
                        setCfg({ ...cfg, public_api: { ...cfg.public_api, bizinfo_api_key: e.target.value } })
                      }
                      className="px-3 py-1.5 text-xs rounded border border-slate-300 font-mono"
                    />
                    <input
                      type="text"
                      aria-label="기업마당 해시태그"
                      placeholder="해시태그 (예: 사회적기업,ESG)"
                      value={cfg.public_api.bizinfo_hashtags}
                      onChange={(e) =>
                        setCfg({ ...cfg, public_api: { ...cfg.public_api, bizinfo_hashtags: e.target.value } })
                      }
                      className="px-3 py-1.5 text-xs rounded border border-slate-300"
                    />
                  </div>
                  {apiTestResults.bizinfo && (
                    <div
                      role={apiTestResults.bizinfo.ok ? 'status' : 'alert'}
                      className={`px-2.5 py-1.5 rounded border text-[11px] flex items-center gap-2 ${
                        apiTestResults.bizinfo.ok
                          ? 'bg-[#ECFDF5] border-[#059669]/40 text-emerald-900'
                          : 'bg-[#FEF2F2] border-[#DC2626]/40 text-rose-900'
                      }`}
                    >
                      <span
                        className={`px-1.5 py-0.5 rounded-full text-[10px] font-extrabold text-white ${
                          apiTestResults.bizinfo.ok ? 'bg-[#059669]' : 'bg-[#DC2626]'
                        }`}
                      >
                        {apiTestResults.bizinfo.ok ? '정상' : '비정상'}
                      </span>
                      <span className="truncate">{apiTestResults.bizinfo.message}</span>
                    </div>
                  )}
                </div>
              </div>
            </form>
          )}

          {tab === 'rag' && ragStatus && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-slate-200">
                <div>
                  <h2 className="text-sm font-bold text-[#0F2942]">로컬 SQLite 하이브리드 RAG (FTS5 + sqlite-vec RRF)</h2>
                  <p className="text-[11px] text-slate-500 tabular-nums">
                    vec0 확장: {ragStatus.vec_available ? '활성' : '비활성'} · 문서 {ragStatus.doc_count}건 · 청크 {ragStatus.chunk_count}개 · 임베딩 {embeddingSpaceLabel(ragStatus.indexed_model)} ({ragStatus.indexed_dim}D)
                  </p>
                </div>
                <button
                  type="button"
                  disabled={ragReindexing || ragUploading}
                  onClick={() => void handleRagReindex()}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${ragReindexing ? 'animate-spin' : ''}`} />
                  {ragReindexing ? '재인덱싱 중...' : '전체 재인덱싱'}
                </button>
              </div>

              {cacheStats && (
                <div className="flex flex-wrap items-center justify-between gap-2 p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-slate-600">
                  <span className="tabular-nums">
                    LLM 결과 캐시: 임베딩 {cacheStats.embedding_entries.toLocaleString()}건 · Vision OCR{' '}
                    {cacheStats.ocr_entries.toLocaleString()}건 (동일 입력 재호출 시 과금 없음)
                  </span>
                  {cacheClearArmed ? (
                    <span className="flex items-center gap-1.5">
                      <span className="text-rose-700 font-semibold">비우면 다음 호출부터 다시 과금됩니다.</span>
                      <button
                        type="button"
                        onClick={() => void handleClearLlmCache()}
                        className="px-2 py-1 rounded text-[11px] font-bold bg-rose-600 text-white"
                      >
                        비우기
                      </button>
                      <button
                        type="button"
                        onClick={() => setCacheClearArmed(false)}
                        className="px-2 py-1 rounded text-[11px] font-semibold bg-slate-200 text-slate-700"
                      >
                        취소
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={ragReindexing || ragUploading}
                      onClick={() => setCacheClearArmed(true)}
                      className="px-2 py-1 rounded text-[11px] font-semibold bg-white border border-slate-300 text-slate-700 disabled:opacity-50"
                    >
                      캐시 비우기
                    </button>
                  )}
                </div>
              )}

              {ragStatus.needs_reindex && (
                <div className="p-3 rounded-lg bg-amber-50 border border-amber-300 text-xs text-amber-900 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0" />
                    <span>
                      임베딩 공간이 변경되었습니다 ({embeddingSpaceLabel(ragStatus.indexed_model)} {ragStatus.indexed_dim}D → {embeddingSpaceLabel(ragStatus.current_model)} {ragStatus.current_dim}D). 서로 다른 벡터는 섞을 수 없어 재인덱싱 전까지는 키워드 검색만 사용합니다. <strong>[전체 재인덱싱]</strong>을 실행하세요.
                    </span>
                  </div>
                </div>
              )}

              <FileDropzone
                accept=".hwpx,.hwp,.pdf,.txt,.md"
                dialogMode="rag"
                disabled={ragUploading || ragReindexing}
                label={ragUploading ? '문서 청킹 및 임베딩 인덱싱 중...' : '과거 선정 제안서 · 사업계획서 · SVI 매뉴얼 인덱싱'}
                hint="업로드한 문서는 2,048토큰 이내로 청킹되어 FTS5 + sqlite-vec에 저장됩니다."
                onFileSelected={(f) =>
                  runRagIngest(() => api.ingestRagFile(f), `'${f.name}' 문서를 로컬 RAG에 인덱싱했습니다.`)
                }
                onNativePathSelected={(p) =>
                  runRagIngest(() => api.ingestRagPath(p), '선택한 문서를 로컬 RAG에 인덱싱했습니다.')
                }
              />

              {/* 하이브리드 검색 테스트 */}
              <form onSubmit={handleRagSearch} className="flex gap-2">
                <label htmlFor={ragSearchId} className="visually-hidden">RAG 하이브리드 검색 테스트</label>
                <input
                  id={ragSearchId}
                  type="search"
                  value={ragQuery}
                  onChange={(e) => setRagQuery(e.target.value)}
                  placeholder="검색 테스트: 예) 직접생산확인 성동구청 SVI 우수"
                  className="flex-1 px-3 py-1.5 text-xs rounded-lg border border-slate-300"
                />
                <button
                  type="submit"
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0D9488] text-white"
                >
                  <Search className="w-3.5 h-3.5" /> RRF 검색
                </button>
              </form>

              {ragHits.length > 0 && (
                <div className="space-y-2 bg-slate-50 p-3 rounded-lg border border-slate-200">
                  {ragHits.map((h) => (
                    <div key={h.chunk_id} className="bg-white p-2.5 rounded border border-slate-200 text-xs">
                      <div className="flex items-center justify-between font-bold text-[#0F2942]">
                        <span>{h.doc_title}</span>
                        <span className="tabular-nums text-[11px] text-[#0D9488]">
                          RRF {h.rrf_score} (FTS #{h.fts_rank ?? '-'} / Vec #{h.vec_rank ?? '-'})
                        </span>
                      </div>
                      <p className="text-slate-600 mt-1 line-clamp-2">{h.content}</p>
                    </div>
                  ))}
                </div>
              )}

              <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg">
                {ragStatus.documents.map((doc) => (
                  <div key={doc.id} className="p-2.5 flex items-center justify-between text-xs">
                    <div>
                      <span className="font-bold text-slate-800">{doc.title}</span>
                      <span className="ml-2 px-1.5 py-0.5 rounded bg-slate-100 text-[10px] text-slate-600">
                        {doc.doc_type} · {doc.chunk_count}청크
                      </span>
                    </div>
                    <button
                      type="button"
                      aria-label={`${doc.title} 삭제`}
                      onClick={async () => {
                        await api.deleteRagDoc(doc.id);
                        setRagStatus(await api.getRagStatus());
                        onNotify(`'${doc.title}' 문서를 삭제했습니다.`);
                      }}
                      className="p-1 text-slate-400 hover:text-rose-600"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === 'prompts' && cfg && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-slate-200">
                <h2 className="text-sm font-bold text-[#0F2942]">외부 프롬프트 YAML & %APPDATA% 분리 경로</h2>
                <button
                  type="button"
                  onClick={() => void nativeOpenFolder(dataDir)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 hover:bg-slate-200"
                >
                  <FolderOpen className="w-3.5 h-3.5" /> 데이터 폴더 열기
                </button>
              </div>

              <div className="flex gap-2 items-center">
                <input
                  type="url"
                  aria-label="원격 프롬프트 매니페스트 URL"
                  autoCorrect="off"
                  autoCapitalize="none"
                  spellCheck={false}
                  placeholder="https://.../prompts-manifest.json (고객사별 원격 프롬프트 업데이트)"
                  value={cfg.remote_prompts_manifest_url}
                  onChange={(e) => setCfg({ ...cfg, remote_prompts_manifest_url: e.target.value })}
                  className="flex-1 px-3 py-1.5 text-xs rounded-lg border border-slate-300 font-mono"
                />
                <button
                  type="button"
                  onClick={async () => {
                    await api.updateSettings(cfg);
                    const res = await api.syncRemotePrompts(cfg.remote_prompts_manifest_url);
                    setPrompts(await api.getPrompts());
                    onNotify(res.message);
                  }}
                  className="px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white"
                >
                  원격 동기화
                </button>
              </div>

              <div className="space-y-3">
                {prompts.map((pr, idx) => (
                  <div key={pr.name} className="p-3 rounded-lg border border-slate-200 space-y-2">
                    <div className="flex items-center justify-between">
                      <div>
                        <strong className="text-xs text-[#0F2942] font-mono">{pr.name}.yaml</strong>
                        <span className="ml-2 text-[11px] text-slate-500">{pr.description}</span>
                      </div>
                      <button
                        type="button"
                        onClick={async () => {
                          await api.updatePrompt(pr.name, {
                            system_prompt: pr.system_prompt,
                            description: pr.description,
                            version: pr.version,
                          });
                          onNotify(`프롬프트 '${pr.name}.yaml'을 저장했습니다.`);
                        }}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded text-xs font-semibold bg-[#0D9488] text-white"
                      >
                        <CheckCircle2 className="w-3 h-3" /> 저장
                      </button>
                    </div>
                    <textarea
                      aria-label={`${pr.name} 시스템 프롬프트`}
                      rows={3}
                      value={pr.system_prompt}
                      onChange={(e) =>
                        setPrompts((prev) =>
                          prev.map((item, i) => (i === idx ? { ...item, system_prompt: e.target.value } : item))
                        )
                      }
                      className="w-full p-2 text-xs rounded border border-slate-300 font-mono bg-slate-50"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
      {confirmDialog}
    </div>
  );
};
