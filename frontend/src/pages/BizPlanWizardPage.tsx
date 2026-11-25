import React, { useEffect, useId, useRef, useState } from 'react';
import {
  AlertOctagon,
  Calculator,
  CheckCircle2,
  Download,
  FolderOpen,
  Play,
  Plus,
  Save,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  Wrench,
} from 'lucide-react';
import { api, streamPostSse } from '../lib/api';
import { isPyWebviewEnv, nativeOpenFolder, nativeSaveFile } from '../lib/bridge';
import type {
  BizPlanDetail,
  SocialBMCanvas,
  SviAssessmentResult,
  UnitEconomicsResult,
} from '../types/api';
import { FileDropzone } from '../components/FileDropzone';
import { MarkdownPreview } from '../components/MarkdownPreview';
import { SviGauge } from '../components/SviGauge';
import { NativeDialog } from '../components/NativeDialog';
import { useLlmConfirm } from '../components/LlmConfirmDialog';

interface BizPlanWizardPageProps {
  initialBizPlanId: number | null;
  onNotify: (msg: string) => void;
}

const STEPS = [
  { num: 1, title: 'Pain Point · 이중 고객' },
  { num: 2, title: '문제해결 · 단위 경제성' },
  { num: 3, title: '사회적 목적 · SVI 14지표' },
  { num: 4, title: '출력 양식 · 초안 · 레드플래그' },
];

const PURPOSE_TYPES = [
  { key: 'job_creation', label: '일자리 제공형 (취약계층 고용 30%+)' },
  { key: 'social_service', label: '사회서비스 제공형 (취약계층 수혜 30%+)' },
  { key: 'community_contribution', label: '지역사회 공헌형 (지역 공헌 20%+)' },
  { key: 'mixed', label: '혼합형 (고용 20%+ 및 서비스 20%+)' },
  { key: 'other', label: '기타형 (창의·혁신적 사회문제 해결)' },
];

export const BizPlanWizardPage: React.FC<BizPlanWizardPageProps> = ({
  initialBizPlanId,
  onNotify,
}) => {
  const [plans, setPlans] = useState<BizPlanDetail[]>([]);
  const [active, setActive] = useState<BizPlanDetail | null>(null);
  const [step, setStep] = useState<number>(1);
  const [uePreview, setUePreview] = useState<UnitEconomicsResult | null>(null);
  const [sviPreview, setSviPreview] = useState<SviAssessmentResult | null>(null);
  // LLM 작업 실행 잠금: state는 UI 비활성화용, ref는 await 이전 동기 중복 클릭 차단용
  const [busy, setBusy] = useState<null | 'generate' | 'remediate'>(null);
  const busyRef = useRef<null | 'generate' | 'remediate'>(null);
  const abortRef = useRef<AbortController | null>(null);
  // SSE 연결이 실제로 열린 구간(중지 버튼 표시용). 확인·저장 단계에서는 false
  const [streaming, setStreaming] = useState(false);
  const isBusy = busy !== null;
  const [strongModelName, setStrongModelName] = useState('');
  const { confirmLlm, confirmDialog } = useLlmConfirm();
  const [activeSecKey, setActiveSecKey] = useState<string>('problem');
  const [secViewMode, setSecViewMode] = useState<'preview' | 'edit'>('preview');
  const [newOpen, setNewOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [lastExportPath, setLastExportPath] = useState('');

  const stepHeadingRef = useRef<HTMLHeadingElement>(null);
  const selectId = useId();
  const newTitleId = useId();

  const loadPlans = async (pickId?: number | null) => {
    try {
      let list = await api.listBizPlans();
      if (list.length === 0) {
        const created = await api.createBizPlan({
          title: '2026 사회적기업 디지털 돌봄 스케일업 사업계획서',
          output_track: 'psst',
        });
        list = [created];
      }
      setPlans(list);
      const target = list.find((p) => p.id === (pickId ?? initialBizPlanId)) || list[0];
      setActive(target);
      setUePreview(target.unit_economics);
      setSviPreview(target.svi_assessment);
      const firstKey = Object.keys(target.sections_content)[0] || (target.output_track === 'pre_social' ? 'area_1' : 'problem');
      setActiveSecKey(firstKey);
    } catch (e) {
      onNotify(`사업계획서 로드 실패: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void loadPlans(initialBizPlanId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialBizPlanId]);

  useEffect(() => {
    void api
      .getSettings()
      .then((c) => setStrongModelName(c.llm.strong_model || ''))
      .catch(() => {});
    // 화면 이탈(언마운트) 시 진행 중인 스트림을 중단해 백그라운드 LLM 호출이 이어지지 않게 한다.
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const tryLock = (kind: 'generate' | 'remediate'): boolean => {
    if (busyRef.current) {
      onNotify('다른 AI 작업이 진행 중입니다. 완료되거나 중지된 뒤 다시 시도하세요.');
      return false;
    }
    busyRef.current = kind;
    setBusy(kind);
    return true;
  };

  const unlock = () => {
    busyRef.current = null;
    setBusy(null);
  };

  const goToStep = (nextStep: number) => {
    setStep(nextStep);
    window.requestAnimationFrame(() => {
      stepHeadingRef.current?.focus();
    });
  };

  const updateCanvas = (patch: Partial<SocialBMCanvas>) => {
    if (!active) return;
    setActive({ ...active, canvas: { ...active.canvas, ...patch } });
  };

  // 단위 경제성 실시간 미리보기 계산
  useEffect(() => {
    if (!active) return;
    const c = active.canvas;
    const timer = window.setTimeout(() => {
      void api
        .previewUnitEconomics({
          unit_price: Number(c.unit_price) || 0,
          target_customers_monthly: Number(c.target_customers_monthly) || 0,
          conversion_rate_pct: Number(c.conversion_rate_pct) || 0,
          variable_cost_per_unit: Number(c.variable_cost_per_unit) || 0,
          fixed_cost_monthly: Number(c.fixed_cost_monthly) || 0,
        })
        .then(setUePreview)
        .catch(() => {});
    }, 180);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    active?.canvas.unit_price,
    active?.canvas.target_customers_monthly,
    active?.canvas.conversion_rate_pct,
    active?.canvas.variable_cost_per_unit,
    active?.canvas.fixed_cost_monthly,
  ]);

  const handleSviChange = async (indId: string, val: number) => {
    if (!active) return;
    const nextScores = { ...active.canvas.svi_scores, [indId]: val };
    updateCanvas({ svi_scores: nextScores });
    try {
      const res = await api.previewSviScore(nextScores);
      setSviPreview(res);
    } catch {
      // ignore
    }
  };

  const handleSaveCanvas = async (): Promise<boolean> => {
    if (!active) return false;
    try {
      const saved = await api.saveBizPlanCanvas({
        bizplan_id: active.id,
        title: active.title,
        output_track: active.output_track,
        template_hwpx_path: active.template_hwpx_path,
        canvas: active.canvas,
      });
      setActive(saved);
      setUePreview(saved.unit_economics);
      setSviPreview(saved.svi_assessment);
      onNotify('소셜 비즈니스 모델 캔버스와 단위 경제성·SVI 점수를 저장했습니다.');
      return true;
    } catch (e) {
      onNotify(`저장 실패: ${(e as Error).message}`);
      return false;
    }
  };

  const handleGenerateBizPlan = async () => {
    if (!active) return;
    // await 이전에 동기적으로 잠가 더블클릭·연타로 인한 중복 생성 요청을 차단
    if (!tryLock('generate')) return;
    const planId = active.id;
    try {
      const sectionCount =
        active.output_track === 'pre_social' ? 8 : active.output_track === 'hwpx_patch' ? null : 4;
      const overwriteCount = Object.values(active.sections_content).filter((v) => (v || '').trim()).length;
      const ok = await confirmLlm({
        title: '사업계획서 AI 초안 생성',
        details: [
          `생성 범위: ${sectionCount ? `${sectionCount}개 섹션` : '지정 HWPX 양식의 전체 슬롯'} (섹션별 LLM 1회 + RAG 임베딩 1회)`,
          `사용 모델: 고품질 ${strongModelName || 'strong 모델'}`,
          '완료 후 레드플래그 자동 점검(규칙 기반, 추가 비용 없음)',
        ],
        overwriteCount,
        confirmLabel: '초안 생성 시작',
        estimate: { kind: 'section', calls: sectionCount ?? 4, tier: 'strong' },
      });
      if (!ok) return;
      if (!(await handleSaveCanvas())) return;

      const ac = new AbortController();
      abortRef.current = ac;
      setStreaming(true);
      // 다른 사업계획서의 화면 상태에 스트림이 섞이지 않도록 대상 ID를 고정
      const applyToPlan = (fn: (prev: BizPlanDetail) => BizPlanDetail) =>
        setActive((prev) => (prev && prev.id === planId ? fn(prev) : prev));

      await streamPostSse(
        '/api/bizplan/generate',
        { bizplan_id: planId, output_track: active.output_track, model_tier: 'strong' },
        (ev) => {
          if (ev.event === 'section_start') {
            const key = String(ev.data.section_key || '');
            setActiveSecKey(key);
            applyToPlan((prev) => ({ ...prev, sections_content: { ...prev.sections_content, [key]: '' } }));
          } else if (ev.event === 'delta') {
            const key = String(ev.data.section_key || '');
            const txt = String(ev.data.text || '');
            applyToPlan((prev) => ({
              ...prev,
              sections_content: {
                ...prev.sections_content,
                [key]: (prev.sections_content[key] || '') + txt,
              },
            }));
          } else if (ev.event === 'audit') {
            applyToPlan((prev) => ({ ...prev, audit_report: ev.data as unknown as BizPlanDetail['audit_report'] }));
          } else if (ev.event === 'error') {
            onNotify(`생성 중단: ${String(ev.data.message || '알 수 없는 오류')} (완료된 섹션까지만 저장됨)`);
          } else if (ev.event === 'done') {
            onNotify('사업계획서 초안 생성 및 심사관 레드플래그 자동 점검이 완료되었습니다.');
          }
        },
        ac.signal
      );
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        onNotify('초안 생성을 중지했습니다. (완료된 섹션까지만 저장됨)');
      } else {
        onNotify(`생성 오류: ${(e as Error).message}`);
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
      unlock();
    }
  };

  const handleRunAudit = async () => {
    if (!active) return;
    try {
      const report = await api.auditBizPlan(active.id);
      setActive({ ...active, audit_report: report });
      onNotify(`심사관 점검 완료 (종합 ${report.overall_score}점 · 레드플래그 ${report.red_flags.length}건)`);
    } catch (e) {
      onNotify(`점검 실패: ${(e as Error).message}`);
    }
  };

  const handleRemediate = async (flagCode: string, sectionKey: string, actionLabel: string) => {
    if (!active) return;
    if (!tryLock('remediate')) return;
    try {
      const ok = await confirmLlm({
        title: '레드플래그 AI 보완',
        details: [
          `보완 항목: ${actionLabel}`,
          `대상 섹션: ${sectionKey || '자동 선택'} — 섹션 본문 전체를 LLM이 교정·재작성합니다.`,
          `사용 모델: 고품질 ${strongModelName || 'strong 모델'} (호출 1회)`,
        ],
        confirmLabel: '보완 실행',
        estimate: { kind: 'remediate', calls: 1, tier: 'strong' },
      });
      if (!ok) return;
      const updated = await api.remediateBizPlan(active.id, flagCode, sectionKey);
      setActive((prev) => (prev && prev.id === updated.id ? updated : prev));
      if (sectionKey) setActiveSecKey(sectionKey);
      onNotify('레드플래그 보완 내용을 본문에 자동 반영했습니다.');
    } catch (e) {
      onNotify(`보완 실패: ${(e as Error).message}`);
    } finally {
      unlock();
    }
  };

  const handleSaveSections = async () => {
    if (!active) return;
    try {
      const updated = await api.updateBizPlan(active.id, {
        title: active.title,
        output_track: active.output_track,
        template_hwpx_path: active.template_hwpx_path,
        canvas: active.canvas,
        sections_content: active.sections_content,
      });
      setActive(updated);
      onNotify('수정된 사업계획서 본문을 저장하고 레드플래그 재점검을 반영했습니다.');
    } catch (e) {
      onNotify(`본문 저장 실패: ${(e as Error).message}`);
    }
  };

  const handleExport = async (fmt: 'hwpx' | 'docx' | 'md') => {
    if (!active) return;
    let customPath = '';
    if (isPyWebviewEnv()) {
      const dlg = await nativeSaveFile(`${active.title}.${fmt}`);
      if (dlg.cancelled) return;
      if (dlg.ok && dlg.path) customPath = dlg.path;
    }
    try {
      const res = await api.exportBizPlan(active.id, fmt, customPath);
      setLastExportPath(res.output_path);
      onNotify(`${fmt.toUpperCase()} 저장 완료 [${res.mode}]: ${res.output_path}`);
    } catch (e) {
      onNotify(`내보내기 실패: ${(e as Error).message}`);
    }
  };

  const handleCreateNew = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      const created = await api.createBizPlan({ title: newTitle.trim(), output_track: 'psst' });
      setPlans((prev) => [created, ...prev]);
      setActive(created);
      setUePreview(created.unit_economics);
      setSviPreview(created.svi_assessment);
      setNewOpen(false);
      setNewTitle('');
      goToStep(1);
    } catch (err) {
      onNotify(`생성 실패: ${(err as Error).message}`);
    }
  };

  const sectionEntries = Object.entries(active?.sections_content || {});
  const audit = active?.audit_report;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-[#0F2942]">Pain Point·수익창출 중심 소셜 사업계획서 위저드</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Pain Point → 이중 고객 → 단위 경제성(BEP) → SVI 14개 지표 → PSST/예비사회적기업 출력 & 심사관 레드플래그 5종 자동 점검
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={selectId} className="visually-hidden">
            사업계획서 선택
          </label>
          <select
            id={selectId}
            value={active?.id ?? ''}
            disabled={isBusy}
            title={isBusy ? 'AI 작업 중에는 사업계획서를 전환할 수 없습니다.' : undefined}
            onChange={(e) => {
              const found = plans.find((p) => p.id === Number(e.target.value));
              if (found) {
                setActive(found);
                setUePreview(found.unit_economics);
                setSviPreview(found.svi_assessment);
              }
            }}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 bg-white max-w-[260px] disabled:opacity-60"
          >
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title}
              </option>
            ))}
          </select>

          <button
            type="button"
            disabled={isBusy}
            onClick={() => setNewOpen(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F] disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" />
            새 사업계획서
          </button>
          <button
            type="button"
            disabled={isBusy}
            onClick={() => void handleSaveCanvas()}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            캔버스 저장
          </button>
        </div>
      </div>

      {/* 상단 4단계 스텝퍼 */}
      <nav aria-label="사업계획서 작성 단계" className="bg-white border border-slate-200 rounded-xl p-3">
        <ol className="grid grid-cols-2 lg:grid-cols-4 gap-2">
          {STEPS.map((st) => {
            const isCurrent = step === st.num;
            return (
              <li key={st.num}>
                <button
                  type="button"
                  aria-current={isCurrent ? 'step' : undefined}
                  onClick={() => goToStep(st.num)}
                  className={`w-full text-left px-3 py-2 rounded-lg border text-xs transition-colors ${
                    isCurrent
                      ? 'border-[#0D9488] bg-teal-50 text-[#0F2942] font-bold'
                      : 'border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100'
                  }`}
                >
                  <span className="block text-[10px] uppercase tracking-wide text-[#0D9488] font-bold">
                    STEP {st.num}
                  </span>
                  {st.title}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      {/* 중앙 캔버스(8/12) + 우측 실시간 심사관 레드플래그 패널(4/12) */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div className="xl:col-span-8 bg-white border border-slate-200 rounded-xl p-5">
          {active && step === 1 && (
            <div className="space-y-4">
              <h2 ref={stepHeadingRef} tabIndex={-1} className="text-base font-bold text-[#0F2942] outline-none">
                1단계: Pain Point 및 이중 고객(수혜자 · 실제 결제자) 정의
              </h2>

              <div className="space-y-3">
                <div>
                  <label htmlFor="bp-problem" className="block text-xs font-bold text-slate-700 mb-1">
                    해결하려는 핵심 사회문제 · 고객 고통 (Pain Point)
                  </label>
                  <textarea
                    id="bp-problem"
                    rows={3}
                    value={active.canvas.problem_statement}
                    onChange={(e) => updateCanvas({ problem_statement: e.target.value })}
                    className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
                  />
                </div>

                <fieldset className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3.5 rounded-lg bg-slate-50 border border-slate-200">
                  <legend className="text-xs font-bold text-[#0F2942] px-1">이중 고객 분리 구조</legend>
                  <div>
                    <label htmlFor="bp-beneficiary" className="block text-xs font-semibold text-slate-700 mb-1">
                      수혜자 (Beneficiary — 문제를 겪는 대상)
                    </label>
                    <input
                      id="bp-beneficiary"
                      type="text"
                      value={active.canvas.beneficiary}
                      onChange={(e) => updateCanvas({ beneficiary: e.target.value })}
                      className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 bg-white"
                    />
                  </div>
                  <div>
                    <label htmlFor="bp-payer" className="block text-xs font-semibold text-slate-700 mb-1">
                      실제 결제자 (Payer — 예산을 집행하는 고객)
                    </label>
                    <input
                      id="bp-payer"
                      type="text"
                      value={active.canvas.payer}
                      onChange={(e) => updateCanvas({ payer: e.target.value })}
                      className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 bg-white"
                    />
                  </div>
                  <div className="md:col-span-2 flex items-center gap-2">
                    <input
                      id="bp-eq-payer"
                      type="checkbox"
                      checked={active.canvas.beneficiary_equals_payer}
                      onChange={(e) => updateCanvas({ beneficiary_equals_payer: e.target.checked })}
                      className="w-4 h-4 accent-[#0D9488]"
                    />
                    <label htmlFor="bp-eq-payer" className="text-xs text-slate-700">
                      수혜자와 실제 결제자가 동일합니다 (직접 B2C 모델)
                    </label>
                  </div>
                </fieldset>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="bp-alt" className="block text-xs font-bold text-slate-700 mb-1">
                      기존 대안(경쟁사·자체 수행)의 한계
                    </label>
                    <textarea
                      id="bp-alt"
                      rows={2}
                      value={active.canvas.existing_alternatives_limit}
                      onChange={(e) => updateCanvas({ existing_alternatives_limit: e.target.value })}
                      className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                    />
                  </div>
                  <div>
                    <label htmlFor="bp-ev" className="block text-xs font-bold text-slate-700 mb-1">
                      객관적 문제 근거 (통계청·설문·시범실적)
                    </label>
                    <textarea
                      id="bp-ev"
                      rows={2}
                      value={active.canvas.evidence_sources}
                      onChange={(e) => updateCanvas({ evidence_sources: e.target.value })}
                      className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div>
                    <label htmlFor="bp-tam" className="block text-xs font-semibold text-slate-600 mb-1">TAM (전체 시장)</label>
                    <input
                      id="bp-tam"
                      type="text"
                      value={active.canvas.tam_desc}
                      onChange={(e) => updateCanvas({ tam_desc: e.target.value })}
                      className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300"
                    />
                  </div>
                  <div>
                    <label htmlFor="bp-sam" className="block text-xs font-semibold text-slate-600 mb-1">SAM (유효 시장)</label>
                    <input
                      id="bp-sam"
                      type="text"
                      value={active.canvas.sam_desc}
                      onChange={(e) => updateCanvas({ sam_desc: e.target.value })}
                      className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300"
                    />
                  </div>
                  <div>
                    <label htmlFor="bp-som" className="block text-xs font-semibold text-slate-600 mb-1">SOM (초기 목표 시장)</label>
                    <input
                      id="bp-som"
                      type="text"
                      value={active.canvas.som_desc}
                      onChange={(e) => updateCanvas({ som_desc: e.target.value })}
                      className="w-full px-2.5 py-1.5 text-xs rounded-lg border border-slate-300"
                    />
                  </div>
                </div>
              </div>

              <div className="flex justify-end pt-2">
                <button
                  type="button"
                  onClick={() => goToStep(2)}
                  className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white"
                >
                  다음: 문제해결 & 단위 경제성 →
                </button>
              </div>
            </div>
          )}

          {active && step === 2 && (
            <div className="space-y-4">
              <h2 ref={stepHeadingRef} tabIndex={-1} className="text-base font-bold text-[#0F2942] outline-none">
                2단계: 문제해결 메커니즘 & 내재형 수익 엔진 (단위 경제성 계산기)
              </h2>

              <div className="space-y-3">
                <div>
                  <label htmlFor="bp-sol" className="block text-xs font-bold text-slate-700 mb-1">
                    문제해결 메커니즘 (제품·서비스 작동 방식)
                  </label>
                  <textarea
                    id="bp-sol"
                    rows={2}
                    value={active.canvas.solution_mechanism}
                    onChange={(e) => updateCanvas({ solution_mechanism: e.target.value })}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                  />
                </div>

                <div>
                  <label htmlFor="bp-formula" className="block text-xs font-bold text-slate-700 mb-1">
                    내재형 수익 창출 한 문장 공식
                  </label>
                  <input
                    id="bp-formula"
                    type="text"
                    value={active.canvas.revenue_formula_sentence}
                    onChange={(e) => updateCanvas({ revenue_formula_sentence: e.target.value })}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                  />
                </div>

                {/* 단위 경제성 계산기 */}
                <fieldset className="p-4 rounded-xl bg-teal-50/50 border border-teal-200 space-y-3">
                  <legend className="text-xs font-bold text-[#0F2942] px-1 flex items-center gap-1">
                    <Calculator className="w-3.5 h-3.5 text-[#0D9488]" />
                    단위 경제성 & 손익분기점(BEP) 실시간 계산기
                  </legend>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2.5">
                    <div>
                      <label htmlFor="ue-price" className="block text-[11px] font-semibold text-slate-700 mb-1">
                        객단가 (원)
                      </label>
                      <input
                        id="ue-price"
                        type="text"
                        inputMode="numeric"
                        value={active.canvas.unit_price}
                        onChange={(e) => updateCanvas({ unit_price: Number(e.target.value.replace(/\D/g, '')) || 0 })}
                        className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 bg-white tabular-nums"
                      />
                    </div>
                    <div>
                      <label htmlFor="ue-cust" className="block text-[11px] font-semibold text-slate-700 mb-1">
                        월 잠재고객(명)
                      </label>
                      <input
                        id="ue-cust"
                        type="text"
                        inputMode="numeric"
                        value={active.canvas.target_customers_monthly}
                        onChange={(e) =>
                          updateCanvas({ target_customers_monthly: Number(e.target.value.replace(/\D/g, '')) || 0 })
                        }
                        className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 bg-white tabular-nums"
                      />
                    </div>
                    <div>
                      <label htmlFor="ue-conv" className="block text-[11px] font-semibold text-slate-700 mb-1">
                        전환율 (%)
                      </label>
                      <input
                        id="ue-conv"
                        type="text"
                        inputMode="decimal"
                        value={active.canvas.conversion_rate_pct}
                        onChange={(e) => updateCanvas({ conversion_rate_pct: Number(e.target.value) || 0 })}
                        className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 bg-white tabular-nums"
                      />
                    </div>
                    <div>
                      <label htmlFor="ue-var" className="block text-[11px] font-semibold text-slate-700 mb-1">
                        단위 변동비 (원)
                      </label>
                      <input
                        id="ue-var"
                        type="text"
                        inputMode="numeric"
                        value={active.canvas.variable_cost_per_unit}
                        onChange={(e) =>
                          updateCanvas({ variable_cost_per_unit: Number(e.target.value.replace(/\D/g, '')) || 0 })
                        }
                        className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 bg-white tabular-nums"
                      />
                    </div>
                    <div>
                      <label htmlFor="ue-fix" className="block text-[11px] font-semibold text-slate-700 mb-1">
                        월 고정비 (원)
                      </label>
                      <input
                        id="ue-fix"
                        type="text"
                        inputMode="numeric"
                        value={active.canvas.fixed_cost_monthly}
                        onChange={(e) =>
                          updateCanvas({ fixed_cost_monthly: Number(e.target.value.replace(/\D/g, '')) || 0 })
                        }
                        className="w-full px-2.5 py-1.5 text-xs rounded border border-slate-300 bg-white tabular-nums"
                      />
                    </div>
                  </div>

                  {uePreview && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
                      <div className="bg-white p-2.5 rounded-lg border border-teal-200">
                        <span className="text-[11px] text-slate-500 block">예상 월 매출</span>
                        <strong className="text-sm text-[#0F2942] tabular-nums">
                          {uePreview.monthly_revenue.toLocaleString()}원
                        </strong>
                      </div>
                      <div className="bg-white p-2.5 rounded-lg border border-teal-200">
                        <span className="text-[11px] text-slate-500 block">예상 연 매출</span>
                        <strong className="text-sm text-[#0D9488] tabular-nums">
                          {uePreview.annual_revenue.toLocaleString()}원
                        </strong>
                      </div>
                      <div className="bg-white p-2.5 rounded-lg border border-teal-200">
                        <span className="text-[11px] text-slate-500 block">단위 공헌이익률</span>
                        <strong className="text-sm text-[#0F2942] tabular-nums">
                          {uePreview.contribution_margin_ratio_pct}%
                        </strong>
                      </div>
                      <div className="bg-white p-2.5 rounded-lg border border-teal-200">
                        <span className="text-[11px] text-slate-500 block">손익분기점 (BEP)</span>
                        <strong className="text-sm text-[#0F2942] tabular-nums">
                          {uePreview.bep_customers !== null ? `${uePreview.bep_customers}건/월` : '적자 구조'}
                        </strong>
                      </div>
                    </div>
                  )}
                </fieldset>
              </div>

              <div className="flex justify-between pt-2">
                <button
                  type="button"
                  onClick={() => goToStep(1)}
                  className="px-3.5 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700"
                >
                  ← 이전
                </button>
                <button
                  type="button"
                  onClick={() => goToStep(3)}
                  className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white"
                >
                  다음: 사회적 목적 & SVI 14지표 →
                </button>
              </div>
            </div>
          )}

          {active && step === 3 && (
            <div className="space-y-4">
              <h2 ref={stepHeadingRef} tabIndex={-1} className="text-base font-bold text-[#0F2942] outline-none">
                3단계: 사회적 목적 5유형 · SVI 14개 지표 매핑 · 예비사회적기업 거버넌스
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label htmlFor="bp-purpose" className="block text-xs font-bold text-slate-700 mb-1">
                    사회적기업육성법상 5대 사회적 목적 실현 유형
                  </label>
                  <select
                    id="bp-purpose"
                    value={active.canvas.social_purpose_type}
                    onChange={(e) => updateCanvas({ social_purpose_type: e.target.value })}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300 bg-white"
                  >
                    {PURPOSE_TYPES.map((pt) => (
                      <option key={pt.key} value={pt.key}>
                        {pt.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="bp-reinvest" className="block text-xs font-bold text-slate-700 mb-1">
                    이윤 2/3 이상 사회적 환원 계획
                  </label>
                  <input
                    id="bp-reinvest"
                    type="text"
                    value={active.canvas.profit_reinvestment_plan}
                    onChange={(e) => updateCanvas({ profit_reinvestment_plan: e.target.value })}
                    className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="bp-gov" className="block text-xs font-bold text-slate-700 mb-1">
                  민주적 의사결정구조 (이사회·운영위원회 회의록 증빙 계획)
                </label>
                <input
                  id="bp-gov"
                  type="text"
                  value={active.canvas.democratic_governance_plan}
                  onChange={(e) => updateCanvas({ democratic_governance_plan: e.target.value })}
                  className="w-full px-3 py-2 text-xs rounded-lg border border-slate-300"
                />
              </div>

              {sviPreview && (
                <>
                  <SviGauge
                    currentScore={sviPreview.total_score}
                    targetScore={85}
                    gradeLabel={sviPreview.grade_label}
                    priority120={sviPreview.priority_purchase_120}
                  />

                  <div className="border border-slate-200 rounded-xl overflow-hidden">
                    <div className="bg-slate-100 px-3 py-2 text-xs font-bold text-slate-700 flex justify-between">
                      <span>SVI 14개 세부 지표 자가 배점 ({sviPreview.total_score} / 100점)</span>
                      <span className="tabular-nums">
                        사회 {sviPreview.social_score} · 경제 {sviPreview.economic_score} · 혁신 {sviPreview.innovation_score}
                      </span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-x divide-slate-100 max-h-64 overflow-y-auto">
                      {sviPreview.indicators.map((ind) => (
                        <div key={ind.id} className="p-2.5 flex items-center justify-between gap-2 text-xs">
                          <div className="min-w-0">
                            <div className="font-semibold text-slate-800 truncate">
                              {ind.no}. {ind.name}
                            </div>
                            <span className="text-[10px] text-slate-500">
                              {ind.domain_label} · 배점 {ind.max_score}점
                            </span>
                          </div>
                          <input
                            type="range"
                            aria-label={`${ind.name} 점수`}
                            min={0}
                            max={ind.max_score}
                            step={0.5}
                            value={ind.score}
                            onChange={(e) => void handleSviChange(ind.id, Number(e.target.value))}
                            className="w-24 accent-[#0D9488]"
                          />
                          <span className="w-10 text-right font-bold text-[#0F2942] tabular-nums">{ind.score}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}

              {/* 3개년 정식 인증 로드맵 에디터 */}
              <fieldset className="p-3.5 rounded-xl border border-slate-200 space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <legend className="text-xs font-bold text-[#0F2942] px-1">
                    3개년 사회적기업 정식 인증 및 성장 로드맵
                  </legend>
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      onClick={() =>
                        updateCanvas({
                          roadmap_3yr: [
                            ...active.canvas.roadmap_3yr,
                            {
                              year: `${active.canvas.roadmap_3yr.length + 1}차년도`,
                              hiring: '',
                              revenue: '',
                              svi: '',
                            },
                          ],
                        })
                      }
                      className="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-[#0F2942]"
                    >
                      <Plus className="w-3 h-3" /> 연차 추가
                    </button>
                  </div>
                </div>
                {active.canvas.roadmap_3yr.length === 0 ? (
                  <p className="text-xs text-slate-500">
                    비어 있으면 초안 생성 시 기본 3개년 로드맵이 자동 삽입됩니다. 직접 편집하려면 [연차 추가]를 누르세요.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {active.canvas.roadmap_3yr.map((row, idx) => (
                      <div key={idx} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center bg-slate-50 p-2 rounded-lg">
                        <input
                          type="text"
                          aria-label={`로드맵 ${idx + 1} 연차`}
                          placeholder="1차년도"
                          value={row.year || ''}
                          onChange={(e) => {
                            const next = active.canvas.roadmap_3yr.map((r, i) =>
                              i === idx ? { ...r, year: e.target.value } : r
                            );
                            updateCanvas({ roadmap_3yr: next });
                          }}
                          className="md:col-span-2 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <input
                          type="text"
                          aria-label={`로드맵 ${idx + 1} 고용 계획`}
                          placeholder="취약계층 고용 계획"
                          value={row.hiring || ''}
                          onChange={(e) => {
                            const next = active.canvas.roadmap_3yr.map((r, i) =>
                              i === idx ? { ...r, hiring: e.target.value } : r
                            );
                            updateCanvas({ roadmap_3yr: next });
                          }}
                          className="md:col-span-3 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <input
                          type="text"
                          aria-label={`로드맵 ${idx + 1} 매출 목표`}
                          placeholder="매출 목표"
                          value={row.revenue || ''}
                          onChange={(e) => {
                            const next = active.canvas.roadmap_3yr.map((r, i) =>
                              i === idx ? { ...r, revenue: e.target.value } : r
                            );
                            updateCanvas({ roadmap_3yr: next });
                          }}
                          className="md:col-span-3 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <input
                          type="text"
                          aria-label={`로드맵 ${idx + 1} SVI·인증 마일스톤`}
                          placeholder="SVI·인증 마일스톤"
                          value={row.svi || ''}
                          onChange={(e) => {
                            const next = active.canvas.roadmap_3yr.map((r, i) =>
                              i === idx ? { ...r, svi: e.target.value } : r
                            );
                            updateCanvas({ roadmap_3yr: next });
                          }}
                          className="md:col-span-3 px-2 py-1 text-xs rounded border border-slate-300 bg-white"
                        />
                        <button
                          type="button"
                          aria-label={`로드맵 ${idx + 1} 삭제`}
                          onClick={() =>
                            updateCanvas({
                              roadmap_3yr: active.canvas.roadmap_3yr.filter((_, i) => i !== idx),
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

              <div className="flex justify-between pt-2">
                <button
                  type="button"
                  onClick={() => goToStep(2)}
                  className="px-3.5 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700"
                >
                  ← 이전
                </button>
                <button
                  type="button"
                  onClick={() => goToStep(4)}
                  className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white"
                >
                  다음: 출력 트랙 · 초안 생성 →
                </button>
              </div>
            </div>
          )}

          {active && step === 4 && (
            <div className="space-y-4">
              <h2 ref={stepHeadingRef} tabIndex={-1} className="text-base font-bold text-[#0F2942] outline-none">
                4단계: 출력 양식 트랙 선택 & SSE 사업계획서 초안 생성
              </h2>

              <fieldset className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
                <legend className="visually-hidden">사업계획서 출력 양식 트랙</legend>
                {[
                  { key: 'psst', title: 'PSST 표준 사업계획서', desc: '중기부·K-Startup 4대 블록 (Problem-Solution-Scaleup-Team)' },
                  { key: 'pre_social', title: '예비사회적기업 8대 영역', desc: '고용노동부 지정 요건·3개년 인증 로드맵 포함' },
                  { key: 'hwpx_patch', title: '지정 HWPX 양식 패치', desc: '업로드한 공고 HWPX 서식을 보존해 빈 칸만 채움' },
                ].map((tr) => (
                  <label
                    key={tr.key}
                    className={`p-3 rounded-lg border cursor-pointer text-xs ${
                      active.output_track === tr.key
                        ? 'border-[#0D9488] bg-teal-50/70'
                        : 'border-slate-200 bg-slate-50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="output_track"
                      value={tr.key}
                      checked={active.output_track === tr.key}
                      disabled={isBusy}
                      onChange={() => setActive({ ...active, output_track: tr.key as BizPlanDetail['output_track'] })}
                      className="mr-1.5 accent-[#0D9488]"
                    />
                    <strong className="text-[#0F2942]">{tr.title}</strong>
                    <p className="text-[11px] text-slate-600 mt-1">{tr.desc}</p>
                  </label>
                ))}
              </fieldset>

              {active.output_track === 'hwpx_patch' && (
                <FileDropzone
                  accept=".hwpx,.hwp"
                  dialogMode="hwpx"
                  label="지정 사업계획서 .hwpx 양식 파일 선택"
                  hint="양식 내 안내문·빈 셀을 자동 탐지해 Patch Mode로 채웁니다."
                  onFileSelected={async (f) => {
                    const resp = await api.uploadDocument(f);
                    setActive({ ...active, template_hwpx_path: resp.document.file_path });
                    onNotify(`양식 슬롯 ${resp.document.slots.length}개 탐지됨`);
                  }}
                  onNativePathSelected={(p) => setActive({ ...active, template_hwpx_path: p })}
                />
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                {streaming ? (
                  <button
                    type="button"
                    onClick={() => abortRef.current?.abort()}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold bg-rose-600 text-white hover:bg-rose-700"
                  >
                    <Square className="w-3.5 h-3.5" />
                    AI 생성 중지
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={isBusy}
                    onClick={() => void handleGenerateBizPlan()}
                    className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
                  >
                    <Play className="w-3.5 h-3.5" />
                    {busy === 'generate' ? '준비 중...' : '소셜 사업계획서 초안 생성 + 레드플래그 자동 점검'}
                  </button>
                )}

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => handleExport('hwpx')}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white"
                  >
                    <Download className="w-3.5 h-3.5" /> HWPX 저장
                  </button>
                  <button
                    type="button"
                    onClick={() => handleExport('docx')}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-300 bg-white"
                  >
                    DOCX
                  </button>
                  <button
                    type="button"
                    onClick={() => handleExport('md')}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-300 bg-white"
                  >
                    MD
                  </button>
                  <button
                    type="button"
                    onClick={() => nativeOpenFolder(lastExportPath)}
                    className="p-1.5 rounded-lg border border-slate-300 bg-slate-50"
                    aria-label="저장 폴더 열기"
                  >
                    <FolderOpen className="w-4 h-4 text-slate-700" />
                  </button>
                </div>
              </div>

              {sectionEntries.length > 0 ? (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="mwg-tablist" role="tablist" aria-label="생성된 사업계획서 섹션">
                      {sectionEntries.map(([key]) => (
                        <button
                          key={key}
                          type="button"
                          role="tab"
                          aria-selected={activeSecKey === key}
                          onClick={() => setActiveSecKey(key)}
                        >
                          {key}
                        </button>
                      ))}
                    </div>

                    <div className="flex items-center gap-2">
                      <div className="mwg-tablist" role="tablist" aria-label="사업계획서 보기 모드">
                        <button
                          type="button"
                          role="tab"
                          aria-selected={secViewMode === 'preview'}
                          onClick={() => setSecViewMode('preview')}
                        >
                          서식·표 미리보기
                        </button>
                        <button
                          type="button"
                          role="tab"
                          aria-selected={secViewMode === 'edit'}
                          onClick={() => setSecViewMode('edit')}
                        >
                          직접 편집
                        </button>
                      </div>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => void handleSaveSections()}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-bold bg-slate-100 hover:bg-slate-200 text-[#0F2942] disabled:opacity-50"
                      >
                        <Save className="w-3.5 h-3.5" /> 본문 저장 & 재점검
                      </button>
                    </div>
                  </div>

                  {secViewMode === 'edit' ? (
                    <textarea
                      aria-label={`${activeSecKey} 섹션 본문 편집`}
                      readOnly={streaming}
                      rows={15}
                      value={active.sections_content[activeSecKey] || ''}
                      onChange={(e) =>
                        setActive({
                          ...active,
                          sections_content: {
                            ...active.sections_content,
                            [activeSecKey]: e.target.value,
                          },
                        })
                      }
                      className="w-full p-3.5 rounded-lg border border-slate-300 font-mono text-xs leading-relaxed bg-slate-50"
                    />
                  ) : (
                    <div className="p-4 rounded-lg border border-slate-200 bg-slate-50/40 max-h-[380px] overflow-y-auto">
                      <MarkdownPreview content={active.sections_content[activeSecKey] || ''} />
                    </div>
                  )}
                </div>
              ) : (
                <div className="p-8 text-center text-xs text-slate-500 border border-dashed border-slate-300 rounded-lg">
                  상단 <strong>[소셜 사업계획서 초안 생성]</strong>을 클릭하면 단위 경제성 표와 SVI 논리가 통합된 초안이 스트리밍됩니다.
                </div>
              )}

              <div className="flex justify-start pt-2">
                <button
                  type="button"
                  onClick={() => goToStep(3)}
                  className="px-3.5 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700"
                >
                  ← 이전
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 우측: 실시간 심사관 레드플래그 5종 자동 점검 Critic 패널 */}
        <aside className="xl:col-span-4 bg-white border border-slate-200 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between gap-2 pb-3 border-b border-slate-200">
            <div>
              <h2 className="text-sm font-bold text-[#0F2942] flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-rose-600" />
                심사관 레드플래그 자동 점검
              </h2>
              <p className="text-[11px] text-slate-500">분량·뇌피셜 숫자·경쟁사 부재·실행증거·보조금 의존 5종 스캔</p>
            </div>
            <button
              type="button"
              disabled={isBusy}
              onClick={handleRunAudit}
              className="px-2.5 py-1.5 rounded-lg text-xs font-bold bg-slate-100 hover:bg-slate-200 text-[#0F2942] disabled:opacity-50"
            >
              재점검
            </button>
          </div>

          {!audit ? (
            <div className="py-10 text-center text-xs text-slate-500">
              4단계에서 초안을 생성하거나 상단 <strong>[재점검]</strong>을 누르면 Critic Agent가 5대 탈락 요인을 검사합니다.
            </div>
          ) : (
            <div className="space-y-3">
              <div
                className={`p-3 rounded-lg border flex items-center justify-between ${
                  audit.passed ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'
                }`}
              >
                <div className="flex items-center gap-2 text-xs font-bold">
                  {audit.passed ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-700" />
                  ) : (
                    <AlertOctagon className="w-4 h-4 text-amber-700" />
                  )}
                  <span>{audit.passed ? '레드플래그 0건 — 심사 통과 권장' : `보완 필요 항목 ${audit.red_flags.length}건`}</span>
                </div>
                <span className="text-sm font-extrabold text-[#0F2942] tabular-nums">{audit.overall_score}점</span>
              </div>

              {audit.red_flags.map((rf, idx) => (
                <div key={`${rf.code}-${idx}`} className="p-3 rounded-lg border border-rose-200 bg-rose-50/40 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-bold text-rose-900">{rf.title}</span>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase bg-rose-100 text-rose-800">
                      {rf.severity}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-700">{rf.evidence_excerpt}</p>
                  <p className="text-[11px] text-slate-600">{rf.recommendation}</p>
                  <button
                    type="button"
                    disabled={isBusy}
                    onClick={() => void handleRemediate(rf.code, rf.section_key, rf.action_label)}
                    className="w-full inline-flex items-center justify-center gap-1 py-1.5 rounded-md text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F] disabled:opacity-50"
                  >
                    <Wrench className="w-3 h-3" />
                    {busy === 'remediate' ? 'AI 보완 처리 중...' : rf.action_label}
                  </button>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      {confirmDialog}

      <NativeDialog open={newOpen} title="새 소셜 사업계획서 만들기" onClose={() => setNewOpen(false)}>
        <form onSubmit={handleCreateNew} className="space-y-4">
          <div>
            <label htmlFor={newTitleId} className="block text-xs font-bold text-slate-700 mb-1">
              사업계획서 제목
            </label>
            <input
              id={newTitleId}
              type="text"
              autoFocus
              required
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
              }}
              placeholder="예: 2026 예비사회적기업 지정 신청 사업계획서"
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
            />
          </div>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setNewOpen(false)} className="px-3 py-2 rounded-lg text-xs bg-slate-100">
              취소
            </button>
            <button type="submit" className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white">
              만들기
            </button>
          </div>
        </form>
      </NativeDialog>
    </div>
  );
};
