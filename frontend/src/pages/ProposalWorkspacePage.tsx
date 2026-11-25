import React, { useEffect, useId, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BookOpen,
  Download,
  FileCheck,
  FolderOpen,
  Play,
  Plus,
  Save,
  Sparkles,
  Square,
} from 'lucide-react';
import { api, streamPostSse } from '../lib/api';
import { isPyWebviewEnv, nativeOpenFolder, nativeSaveFile } from '../lib/bridge';
import type { OutlineNode, ProposalDetail } from '../types/api';
import { FileDropzone } from '../components/FileDropzone';
import { MarkdownPreview } from '../components/MarkdownPreview';
import { NativeDialog } from '../components/NativeDialog';
import { useLlmConfirm } from '../components/LlmConfirmDialog';

interface ProposalWorkspacePageProps {
  initialProposalId: number | null;
  onNotify: (msg: string) => void;
}

export const ProposalWorkspacePage: React.FC<ProposalWorkspacePageProps> = ({
  initialProposalId,
  onNotify,
}) => {
  const [proposals, setProposals] = useState<ProposalDetail[]>([]);
  const [active, setActive] = useState<ProposalDetail | null>(null);
  const [selectedSecId, setSelectedSecId] = useState<string>('');
  const [modelTier, setModelTier] = useState<'fast' | 'strong'>('strong');
  const [fastModelName, setFastModelName] = useState<string>('gemini-3.5-flash-lite');
  const [strongModelName, setStrongModelName] = useState<string>('gemini-3.8-flash');
  const [viewMode, setViewMode] = useState<'edit' | 'preview'>('preview');
  const [streaming, setStreaming] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [lastExportPath, setLastExportPath] = useState('');
  const [newOpen, setNewOpen] = useState(false);
  const [newTitle, setNewTitle] = useState('');
  const [newScenario, setNewScenario] = useState<'A' | 'B' | 'C'>('B');
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  // 생성 잠금: ref는 await 이전 동기 중복 클릭 차단, state는 확인·저장·스트리밍 전 구간 UI 비활성화
  const genLockRef = useRef(false);
  const [generating, setGenerating] = useState(false);
  const { confirmLlm, confirmDialog } = useLlmConfirm();
  const selectProposalId = useId();
  const newTitleId = useId();

  const loadList = async (pickId?: number | null) => {
    try {
      const list = await api.listProposals();
      setProposals(list);
      const targetId = pickId ?? initialProposalId ?? list[0]?.id ?? null;
      if (targetId) {
        const found = list.find((p) => p.id === targetId) || list[0];
        if (found) {
          setActive(found);
          if (found.outline.length > 0) {
            setSelectedSecId(found.outline[0].id);
          }
        }
      }
    } catch (e) {
      onNotify(`제안서 목록 로드 실패: ${(e as Error).message}`);
    }
  };

  useEffect(() => {
    void loadList(initialProposalId);
    void api
      .getSettings()
      .then((c) => {
        if (c.llm.fast_model) setFastModelName(c.llm.fast_model);
        if (c.llm.strong_model) setStrongModelName(c.llm.strong_model);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialProposalId]);

  // 화면 이탈(언마운트) 시 진행 중인 스트림을 중단해 백그라운드 LLM 호출이 이어지지 않게 한다.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleCreateProposal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTitle.trim()) return;
    try {
      const created = await api.createProposal({ title: newTitle.trim(), scenario: newScenario });
      setProposals((prev) => [created, ...prev]);
      setActive(created);
      setSelectedSecId(created.outline[0]?.id || '');
      setNewOpen(false);
      setNewTitle('');
      onNotify(`새 제안서 '${created.title}'를 생성했습니다.`);
    } catch (err) {
      onNotify(`제안서 생성 실패: ${(err as Error).message}`);
    }
  };

  const applyParsedDocToWorkspace = async (resp: Awaited<ReturnType<typeof api.sampleTemplate>>) => {
    const titleBase = resp.document.filename.replace(/\.(hwpx|hwp|pdf)$/i, '');
    const created = await api.createProposal({
      title: active ? active.title : titleBase,
      scenario: resp.detected_scenario,
      source_file_path: resp.document.file_path,
    });
    const updated = await api.updateProposal(created.id, {
      scenario: resp.detected_scenario,
      outline: resp.suggested_outline,
    });
    setProposals((prev) => [updated, ...prev.filter((p) => p.id !== updated.id)]);
    setActive(updated);
    setSelectedSecId(updated.outline[0]?.id || '');
    onNotify(
      `문서 분석 완료 (시나리오 ${resp.detected_scenario} · 배점항목 ${resp.scoring_criteria.length}건 · 슬롯 ${resp.document.slots.length}건)`
    );
  };

  const handleUploadFile = async (file: File) => {
    setParsing(true);
    try {
      const resp = await api.uploadDocument(file);
      await applyParsedDocToWorkspace(resp);
    } catch (e) {
      onNotify(`문서 파싱 실패: ${(e as Error).message}`);
    } finally {
      setParsing(false);
    }
  };

  const handleNativePath = async (path: string) => {
    setParsing(true);
    try {
      const resp = await api.parseDocumentPath(path);
      await applyParsedDocToWorkspace(resp);
    } catch (e) {
      onNotify(`네이티브 경로 파싱 실패: ${(e as Error).message}`);
    } finally {
      setParsing(false);
    }
  };

  const handleLoadSample = async (kind: 'rfp' | 'form') => {
    setParsing(true);
    try {
      const resp = await api.sampleTemplate(kind);
      await applyParsedDocToWorkspace(resp);
    } catch (e) {
      onNotify(`샘플 생성 실패: ${(e as Error).message}`);
    } finally {
      setParsing(false);
    }
  };

  const handleSaveProposal = async (): Promise<boolean> => {
    if (!active) return false;
    try {
      const saved = await api.updateProposal(active.id, {
        title: active.title,
        scenario: active.scenario,
        outline: active.outline,
        sections_content: active.sections_content,
      });
      setActive(saved);
      onNotify('목차 및 제안서 본문을 저장했습니다.');
      return true;
    } catch (e) {
      onNotify(`저장 실패: ${(e as Error).message}`);
      return false;
    }
  };

  const updateOutlineNode = (idx: number, patch: Partial<OutlineNode>) => {
    if (!active) return;
    const next = active.outline.map((n, i) => (i === idx ? { ...n, ...patch } : n));
    setActive({ ...active, outline: next });
  };

  const moveOutlineNode = (from: number, to: number) => {
    if (!active || to < 0 || to >= active.outline.length || from === to) return;
    const arr = [...active.outline];
    const [moved] = arr.splice(from, 1);
    arr.splice(to, 0, moved);
    const renumbered = arr.map((n, i) => ({ ...n, number: String(i + 1) }));
    setActive({ ...active, outline: renumbered });
  };

  const handleGenerate = async (onlySelected: boolean) => {
    if (!active) return;
    // await 이전에 동기적으로 잠가 더블클릭·연타로 인한 중복 생성 요청을 차단
    if (genLockRef.current) return;
    const targetIds = onlySelected && selectedSecId ? [selectedSecId] : active.outline.map((o) => o.id);
    if (targetIds.length === 0) return;
    genLockRef.current = true;
    setGenerating(true);
    const proposalId = active.id;

    try {
      const overwriteCount = targetIds.filter((id) => (active.sections_content[id] || '').trim()).length;
      const modelName = modelTier === 'strong' ? `고품질 ${strongModelName}` : `고속 경량 ${fastModelName}`;
      const ok = await confirmLlm({
        title: onlySelected ? '현재 섹션 AI 초안 생성' : '전체 섹션 AI 순차 생성',
        details: [
          `생성 범위: ${targetIds.length}개 섹션 (섹션별 LLM 1회 + RAG 임베딩 1회)`,
          `사용 모델: ${modelName}`,
        ],
        overwriteCount,
        confirmLabel: '초안 생성 시작',
        estimate: { kind: 'section', calls: targetIds.length, tier: modelTier },
      });
      if (!ok) return;
      if (!(await handleSaveProposal())) return;

      const ac = new AbortController();
      abortRef.current = ac;
      setStreaming(true);
      setViewMode('preview');
      // 다른 제안서로 전환된 화면 상태에 스트림이 섞이지 않도록 대상 ID를 고정
      const applyToProposal = (fn: (prev: ProposalDetail) => ProposalDetail) =>
        setActive((prev) => (prev && prev.id === proposalId ? fn(prev) : prev));

      await streamPostSse(
        '/api/proposal/generate',
        { proposal_id: proposalId, section_ids: targetIds, model_tier: modelTier },
        (ev) => {
          if (ev.event === 'section_start') {
            const sid = String(ev.data.section_id || '');
            setSelectedSecId(sid);
            applyToProposal((prev) => ({ ...prev, sections_content: { ...prev.sections_content, [sid]: '' } }));
          } else if (ev.event === 'citations') {
            const sid = String(ev.data.section_id || '');
            const cites = (ev.data.citations as Record<string, unknown>[]) || [];
            applyToProposal((prev) => ({ ...prev, citations: { ...prev.citations, [sid]: cites } }));
          } else if (ev.event === 'delta') {
            const sid = String(ev.data.section_id || '');
            const txt = String(ev.data.text || '');
            applyToProposal((prev) => ({
              ...prev,
              sections_content: {
                ...prev.sections_content,
                [sid]: (prev.sections_content[sid] || '') + txt,
              },
            }));
          } else if (ev.event === 'error') {
            onNotify(`생성 중단: ${String(ev.data.message || '알 수 없는 오류')} (완료된 섹션까지만 저장됨)`);
          } else if (ev.event === 'done') {
            onNotify('제안서 AI 스트리밍 초안 생성이 완료되었습니다.');
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
      setStreaming(false);
      abortRef.current = null;
      genLockRef.current = false;
      setGenerating(false);
    }
  };

  const handleExport = async (fmt: 'hwpx' | 'docx' | 'md') => {
    if (!active || genLockRef.current) return;
    await handleSaveProposal();
    let customPath = '';
    if (isPyWebviewEnv()) {
      const dlg = await nativeSaveFile(`${active.title}.${fmt}`);
      if (dlg.cancelled) return;
      if (dlg.ok && dlg.path) customPath = dlg.path;
    }
    try {
      const res = await api.exportProposal(fmt, active.id, customPath);
      setLastExportPath(res.output_path);
      const warnMsg = res.warnings.length > 0 ? ` (${res.warnings.join(' / ')})` : '';
      onNotify(`${fmt.toUpperCase()} 저장 완료 [${res.mode}]: ${res.output_path}${warnMsg}`);
    } catch (e) {
      onNotify(`내보내기 실패: ${(e as Error).message}`);
    }
  };

  const scenarioLabel = (sc: string) => {
    if (sc === 'A') return '시나리오 A · RFP 목차/배점표 비례';
    if (sc === 'C') return '시나리오 C · 지정 HWPX 양식 패치';
    return '시나리오 B · 표준/수의계약 경량';
  };

  const currentSection = active?.outline.find((o) => o.id === selectedSecId) || active?.outline[0] || null;
  const currentContent = (active && currentSection ? active.sections_content[currentSection.id] : '') || '';
  const currentCites = (active && currentSection ? active.citations[currentSection.id] : []) || [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-[#0F2942]">제안서 워크스페이스 (듀얼 트랙 문서 엔진)</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            HWPX·HWP·PDF 파싱 → 배점 비례 목차·지시문 편집 → 로컬 RAG 기반 실시간 스트리밍 → 서식 보존 Patch Mode 출력
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {proposals.length > 0 && (
            <div className="flex items-center gap-1.5">
              <label htmlFor={selectProposalId} className="visually-hidden">
                작업할 제안서 선택
              </label>
              <select
                id={selectProposalId}
                value={active?.id ?? ''}
                disabled={generating}
                title={generating ? 'AI 생성 중에는 제안서를 전환할 수 없습니다.' : undefined}
                onChange={(e) => {
                  const found = proposals.find((p) => p.id === Number(e.target.value));
                  if (found) {
                    setActive(found);
                    setSelectedSecId(found.outline[0]?.id || '');
                  }
                }}
                className="px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 bg-white max-w-[260px]"
              >
                {proposals.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title}
                  </option>
                ))}
              </select>
            </div>
          )}

          <button
            type="button"
            disabled={generating}
            onClick={() => setNewOpen(true)}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F]"
          >
            <Plus className="w-3.5 h-3.5" />
            새 제안서
          </button>
          <button
            type="button"
            disabled={parsing || generating}
            onClick={() => handleLoadSample('rfp')}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            <Sparkles className="w-3.5 h-3.5 text-[#0D9488]" />
            샘플 배점표 RFP
          </button>
          <button
            type="button"
            disabled={parsing || generating}
            onClick={() => handleLoadSample('form')}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            <FileCheck className="w-3.5 h-3.5 text-[#0D9488]" />
            샘플 지정 양식(.hwpx)
          </button>
        </div>
      </div>

      {/* 좌우 Split-View (35% / 65%) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* 좌측 패널 (35% -> 4/12 또는 5/12) */}
        <section className="lg:col-span-5 space-y-4">
          <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-teal-50 text-teal-900 border border-teal-200">
                {scenarioLabel(active?.scenario || 'B')}
              </span>
              {active?.source_file_type && (
                <span className="text-[11px] font-mono text-slate-500 uppercase">
                  원본: .{active.source_file_type}
                </span>
              )}
            </div>

            <FileDropzone
              accept=".hwpx,.hwp,.pdf"
              dialogMode="document"
              label={parsing ? '문서 구조·배점표 분석 중...' : 'RFP 또는 지정 .hwpx 양식 업로드'}
              hint=".hwpx, .hwp(5.0), .pdf 파일을 끌어다 놓거나 선택하세요 (한컴오피스 미설치 지원)"
              onFileSelected={handleUploadFile}
              onNativePathSelected={handleNativePath}
              disabled={parsing || generating}
            />

            {active && active.conversion_warnings.length > 0 && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-2.5 text-xs text-amber-900 flex gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                <div>{active.conversion_warnings.join(' / ')}</div>
              </div>
            )}

            {active && active.scoring_table.length > 0 && (
              <div className="border border-slate-200 rounded-lg overflow-hidden">
                <div className="bg-slate-100 px-3 py-1.5 text-xs font-bold text-slate-700">
                  추출된 RFP 평가 배점표 (가중치 비례 분량 자동 계산)
                </div>
                <div className="divide-y divide-slate-100 max-h-40 overflow-y-auto">
                  {active.scoring_table.map((sc, i) => (
                    <div key={i} className="px-3 py-1.5 text-xs flex items-center justify-between gap-2">
                      <span className="text-slate-800 truncate">{sc.item}</span>
                      <span className="font-bold text-[#0F2942] tabular-nums shrink-0">{sc.max_score}점</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 드래그/순서 편집 가능 목차 트리 + 섹션별 Steering Note */}
          <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-bold text-[#0F2942]">목차 트리 & 섹션별 지시문 (Steering Note)</h2>
              <button
                type="button"
                onClick={() => void handleSaveProposal()}
                disabled={!active || generating}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-800"
              >
                <Save className="w-3.5 h-3.5" />
                목차 저장
              </button>
            </div>

            {!active || active.outline.length === 0 ? (
              <p className="text-xs text-slate-500 py-4 text-center">
                상단에서 <strong>[새 제안서]</strong> 또는 <strong>[샘플 배점표 RFP]</strong>를 선택해 목차를 구성하세요.
              </p>
            ) : (
              <div className="space-y-2 max-h-[440px] overflow-y-auto pr-1">
                {active.outline.map((node, idx) => {
                  const isSel = node.id === (currentSection?.id || '');
                  const hasDraft = Boolean((active.sections_content[node.id] || '').trim());
                  return (
                    <div
                      key={node.id}
                      draggable
                      onDragStart={() => setDragIdx(idx)}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={() => {
                        if (dragIdx !== null) moveOutlineNode(dragIdx, idx);
                        setDragIdx(null);
                      }}
                      onClick={() => setSelectedSecId(node.id)}
                      className={`p-3 rounded-lg border transition-colors cursor-pointer ${
                        isSel ? 'border-[#0D9488] bg-teal-50/40' : 'border-slate-200 bg-white hover:border-slate-300'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-1.5 min-w-0 flex-1">
                          <span className="text-xs font-bold text-[#0F2942] tabular-nums">{node.number}.</span>
                          <input
                            type="text"
                            aria-label={`목차 ${node.number} 제목`}
                            value={node.title}
                            onChange={(e) => updateOutlineNode(idx, { title: e.target.value })}
                            className="text-xs font-bold text-slate-900 bg-transparent border-b border-transparent focus:border-[#0D9488] w-full outline-none"
                          />
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 tabular-nums">
                            {node.weight_score}점 · {node.target_chars}자
                          </span>
                          {hasDraft && <span className="w-2 h-2 rounded-full bg-[#0D9488]" title="초안 작성됨" />}
                          <button
                            type="button"
                            aria-label="위로 이동"
                            onClick={(e) => {
                              e.stopPropagation();
                              moveOutlineNode(idx, idx - 1);
                            }}
                            className="p-0.5 text-slate-400 hover:text-slate-700"
                          >
                            <ArrowUp className="w-3.5 h-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label="아래로 이동"
                            onClick={(e) => {
                              e.stopPropagation();
                              moveOutlineNode(idx, idx + 1);
                            }}
                            className="p-0.5 text-slate-400 hover:text-slate-700"
                          >
                            <ArrowDown className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>

                      <div className="mt-2">
                        <label htmlFor={`steer-${node.id}`} className="visually-hidden">
                          {node.title} 섹션 AI 지시문
                        </label>
                        <input
                          id={`steer-${node.id}`}
                          type="text"
                          value={node.steering_note}
                          onChange={(e) => updateOutlineNode(idx, { steering_note: e.target.value })}
                          placeholder="지시문 예: 성동구청 수행 실적과 SVI 78점 우선구매 120% 강점 강조"
                          className="w-full px-2 py-1 text-xs rounded border border-slate-200 bg-slate-50 text-slate-700"
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        {/* 우측 패널 (65% -> 7/12): 실시간 SSE 스트리밍 에디터 & 내보내기 */}
        <section className="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-200">
            <div className="min-w-0">
              <div className="text-xs text-slate-500">선택된 섹션</div>
              <h2 className="text-sm font-bold text-[#0F2942] truncate">
                {currentSection ? `${currentSection.number}. ${currentSection.title}` : '목차를 선택하세요'}
              </h2>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label="생성 모델 등급"
                value={modelTier}
                onChange={(e) => setModelTier(e.target.value as 'fast' | 'strong')}
                className="px-2.5 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 bg-white"
              >
                <option value="strong">고품질 ({strongModelName})</option>
                <option value="fast">고속 경량 ({fastModelName})</option>
              </select>

              {streaming ? (
                <button
                  type="button"
                  onClick={() => abortRef.current?.abort()}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-rose-600 text-white hover:bg-rose-700"
                >
                  <Square className="w-3.5 h-3.5" />
                  생성 중지
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={!active || !currentSection || generating}
                    onClick={() => void handleGenerate(true)}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
                  >
                    <Play className="w-3.5 h-3.5" />
                    현재 섹션 생성
                  </button>
                  <button
                    type="button"
                    disabled={!active || active.outline.length === 0 || generating}
                    onClick={() => void handleGenerate(false)}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F] disabled:opacity-50"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    전체 순차 생성
                  </button>
                </>
              )}
            </div>
          </div>

          {/* RAG 참조 출처 배지 */}
          {currentCites.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 bg-slate-50 px-3 py-2 rounded-lg border border-slate-200">
              <span className="text-[11px] font-bold text-[#0F2942] inline-flex items-center gap-1 mr-1">
                <BookOpen className="w-3.5 h-3.5 text-[#0D9488]" />
                RAG 인용 출처:
              </span>
              {currentCites.map((c, idx) => (
                <span
                  key={idx}
                  className="px-2 py-0.5 rounded-full text-[11px] bg-white border border-slate-300 text-slate-700"
                  title={String(c.snippet || '')}
                >
                  {String(c.doc_title || '문서')} (RRF {String(c.rrf_score || '')})
                </span>
              ))}
            </div>
          )}

          {/* 편집 / 미리보기 전환 */}
          <div className="flex items-center justify-between">
            <div className="mwg-tablist" role="tablist" aria-label="에디터 보기 모드">
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === 'preview'}
                onClick={() => setViewMode('preview')}
              >
                서식·표 미리보기
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={viewMode === 'edit'}
                onClick={() => setViewMode('edit')}
              >
                마크다운 직접 편집
              </button>
            </div>
            <span className="text-xs text-slate-500 tabular-nums">
              현재 {currentContent.length.toLocaleString()}자 / 목표 {currentSection?.target_chars.toLocaleString() || 0}자
            </span>
          </div>

          {viewMode === 'edit' ? (
            <textarea
              aria-label="제안서 섹션 본문 편집"
              readOnly={streaming}
              rows={18}
              value={currentContent}
              onChange={(e) => {
                if (!active || !currentSection) return;
                setActive({
                  ...active,
                  sections_content: {
                    ...active.sections_content,
                    [currentSection.id]: e.target.value,
                  },
                });
              }}
              placeholder="상단 [현재 섹션 생성] 또는 [전체 순차 생성]을 실행하거나 직접 초안을 입력하세요."
              className="w-full p-3.5 rounded-lg border border-slate-300 font-mono text-xs leading-relaxed bg-slate-50"
            />
          ) : (
            <div className="min-h-[380px] max-h-[520px] overflow-y-auto p-4 rounded-lg border border-slate-200 bg-slate-50/40">
              <MarkdownPreview content={currentContent} />
            </div>
          )}

          {/* 내보내기 바 */}
          <div className="pt-3 border-t border-slate-200 flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!active || generating}
                onClick={() => void handleExport('hwpx')}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
              >
                <Download className="w-3.5 h-3.5" />
                {active?.scenario === 'C' ? 'HWPX 양식 패치 저장' : 'HWPX 파일 저장'}
              </button>
              <button
                type="button"
                disabled={!active || generating}
                onClick={() => void handleExport('docx')}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50"
              >
                DOCX 내보내기
              </button>
              <button
                type="button"
                disabled={!active || generating}
                onClick={() => void handleExport('md')}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50"
              >
                마크다운(.md)
              </button>
            </div>

            <button
              type="button"
              onClick={async () => {
                const res = await nativeOpenFolder(lastExportPath);
                if (!res.ok && res.message) onNotify(res.message);
              }}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200"
            >
              <FolderOpen className="w-3.5 h-3.5" />
              저장 폴더 열기
            </button>
          </div>
        </section>
      </div>

      {confirmDialog}

      {/* 새 제안서 생성 모달 */}
      <NativeDialog open={newOpen} title="새 제안서 만들기" onClose={() => setNewOpen(false)}>
        <form onSubmit={handleCreateProposal} className="space-y-4">
          <div>
            <label htmlFor={newTitleId} className="block text-xs font-bold text-slate-700 mb-1">
              제안서 / 사업명
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
              placeholder="예: 2026년 취약계층 디지털 돌봄 운영 제안서"
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
            />
          </div>
          <fieldset className="space-y-1.5">
            <legend className="text-xs font-bold text-slate-700">작성 시나리오</legend>
            {[
              { id: 'B', label: '시나리오 B: 표준/5천만 이하 수의계약 경량 목차' },
              { id: 'A', label: '시나리오 A: RFP 목차 및 배점표 비례' },
              { id: 'C', label: '시나리오 C: 지정 .hwpx 양식 패치' },
            ].map((opt) => (
              <label key={opt.id} className="flex items-center gap-2 text-xs text-slate-800 cursor-pointer">
                <input
                  type="radio"
                  name="scenario"
                  value={opt.id}
                  checked={newScenario === opt.id}
                  onChange={() => setNewScenario(opt.id as 'A' | 'B' | 'C')}
                  className="accent-[#0D9488]"
                />
                {opt.label}
              </label>
            ))}
          </fieldset>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setNewOpen(false)}
              className="px-3 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700"
            >
              취소
            </button>
            <button type="submit" className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white">
              생성하기
            </button>
          </div>
        </form>
      </NativeDialog>
    </div>
  );
};
