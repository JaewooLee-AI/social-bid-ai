import React, { useEffect, useId, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  Filter,
  PauseCircle,
  Radar,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { api } from '../lib/api';
import type { BidItem, BidSyncSourceReport, QuotaStatus } from '../types/api';
import { ScoreRing } from '../components/ScoreRing';
import { NativeDialog } from '../components/NativeDialog';

interface BidsInboxPageProps {
  onOpenProposal: (proposalId: number) => void;
  onNotify: (msg: string) => void;
}

const SOURCE_TABS = [
  { key: 'all', label: '전체 공고' },
  { key: 'g2b', label: '나라장터 (B2G)' },
  { key: 'seis', label: '사회적기업진흥원' },
  { key: 'bizinfo', label: '기업마당 (지원사업)' },
];

const EXCLUSION_REASONS = ['단가 부족', '납기 촉박', '비주력 업종', '직접생산 미보유', '지역/실적 요건 미달'];

export const BidsInboxPage: React.FC<BidsInboxPageProps> = ({ onOpenProposal, onNotify }) => {
  const [bids, setBids] = useState<BidItem[]>([]);
  const [quotas, setQuotas] = useState<QuotaStatus[]>([]);
  const [sourceTab, setSourceTab] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [privilegeOnly, setPrivilegeOnly] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  // await 이전 동기 잠금(같은 틱의 연타로 수집이 중복 실행되는 것을 차단)
  const syncLockRef = useRef(false);
  const [deciding, setDeciding] = useState(false);
  const [lastSyncReports, setLastSyncReports] = useState<BidSyncSourceReport[]>([]);

  // Exclude modal state
  const [excludeTarget, setExcludeTarget] = useState<BidItem | null>(null);
  const [excludeReason, setExcludeReason] = useState<string>(EXCLUSION_REASONS[0]);
  const [excludeNote, setExcludeNote] = useState<string>('');

  const searchInputId = useId();
  const statusSelectId = useId();
  const privCheckId = useId();
  const excludeSelectId = useId();
  const excludeNoteId = useId();

  const loadBids = async () => {
    setLoading(true);
    try {
      const [rows, qRows] = await Promise.all([
        api.listBids({
          source: sourceTab,
          status: statusFilter,
          privilege_only: privilegeOnly,
          q: searchQuery,
        }),
        api.getQuotas(),
      ]);
      setBids(rows);
      setQuotas(qRows);
      if (rows.length > 0 && (!selectedId || !rows.some((r) => r.id === selectedId))) {
        setSelectedId(rows[0].id);
      }
    } catch (e) {
      onNotify(`공고 목록 조회 실패: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadBids();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceTab, statusFilter, privilegeOnly]);

  const selectedBid = bids.find((b) => b.id === selectedId) || null;

  const handleSync = async () => {
    if (syncLockRef.current) return;
    syncLockRef.current = true;
    setSyncing(true);
    try {
      const res = await api.syncBids(7, searchQuery.trim());
      setLastSyncReports(res.reports || []);
      onNotify(`공고 수집 완료: ${res.total_fetched}건 조회 / ${res.scored_count}건 AI 적합도 평가`);
      await loadBids();
    } catch (e) {
      onNotify(`수집 오류: ${(e as Error).message}`);
    } finally {
      syncLockRef.current = false;
      setSyncing(false);
    }
  };

  const handleSeedDemo = async () => {
    if (syncLockRef.current) return;
    syncLockRef.current = true;
    setSyncing(true);
    try {
      const res = await api.seedDemoBids();
      setLastSyncReports(res.reports || []);
      onNotify(`샘플 공고 ${res.total_saved}건(5천만/1억 수의계약 포함)을 불러왔습니다.`);
      await loadBids();
    } catch (e) {
      onNotify(`샘플 로드 오류: ${(e as Error).message}`);
    } finally {
      syncLockRef.current = false;
      setSyncing(false);
    }
  };

  const handleDecision = async (bid: BidItem, decision: 'approved' | 'hold') => {
    setDeciding(true);
    try {
      const res = await api.decideBid(bid.id, { status: decision });
      setBids((prev) => prev.map((b) => (b.id === bid.id ? res.bid : b)));
      if (decision === 'approved') {
        onNotify(
          `승인 완료! 첨부 ${res.downloaded_files.length}건 온디맨드 다운로드 및 Go/No-Go 분석이 완료되었습니다.`
        );
      } else {
        onNotify('공고를 보류 상태로 전환했습니다.');
      }
    } catch (e) {
      onNotify(`검토 처리 실패: ${(e as Error).message}`);
    } finally {
      setDeciding(false);
    }
  };

  const handleConfirmExclude = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!excludeTarget) return;
    setDeciding(true);
    try {
      const res = await api.decideBid(excludeTarget.id, {
        status: 'excluded',
        exclusion_reason: excludeReason,
        exclusion_note: excludeNote,
      });
      setBids((prev) => prev.map((b) => (b.id === excludeTarget.id ? res.bid : b)));
      setExcludeTarget(null);
      setExcludeNote('');
      onNotify(`제외 사유('${excludeReason}')가 학습 피드백 DB에 기록되었습니다.`);
    } catch (err) {
      onNotify(`제외 처리 실패: ${(err as Error).message}`);
    } finally {
      setDeciding(false);
    }
  };

  const handleOpenInProposal = async (bid: BidItem) => {
    try {
      const res = await api.decideBid(bid.id, { status: 'approved' });
      setBids((prev) => prev.map((b) => (b.id === bid.id ? res.bid : b)));
      if (res.created_proposal_id) {
        onOpenProposal(res.created_proposal_id);
      }
    } catch (e) {
      onNotify(`제안서 전환 실패: ${(e as Error).message}`);
    }
  };

  const handleTabKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const idx = SOURCE_TABS.findIndex((t) => t.key === sourceTab);
    if (e.key === 'ArrowRight') {
      setSourceTab(SOURCE_TABS[(idx + 1) % SOURCE_TABS.length].key);
    } else if (e.key === 'ArrowLeft') {
      setSourceTab(SOURCE_TABS[(idx - 1 + SOURCE_TABS.length) % SOURCE_TABS.length].key);
    }
  };

  const statusBadge = (st: BidItem['status']) => {
    if (st === 'approved') return <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-800">승인·다운</span>;
    if (st === 'hold') return <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-amber-100 text-amber-800">보류</span>;
    if (st === 'excluded') return <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-slate-200 text-slate-600">제외</span>;
    return <span className="px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-100 text-blue-800">검토 대기</span>;
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-[#0F2942]">맞춤 공고 검토 인박스 (HITL)</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            1차 사회적기업 수의계약 특례 필터 + 2차 AI 적합도 점수 확인 후, 사람이 승인한 공고만 첨부파일을 온디맨드로 내려받습니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={syncing}
            onClick={handleSync}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold bg-[#0F2942] text-white hover:bg-[#1E3A5F] disabled:opacity-50"
          >
            <Radar className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
            {syncing ? '7일 분할 수집 중...' : '오늘의 맞춤 공고 수집'}
          </button>
          <button
            type="button"
            disabled={syncing}
            onClick={handleSeedDemo}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            <Sparkles className="w-3.5 h-3.5 text-[#0D9488]" />
            샘플 공고 12건 로드
          </button>
        </div>
      </div>

      {lastSyncReports.length > 0 && (
        <div
          aria-live="polite"
          className="bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-2.5 flex flex-wrap items-center gap-2 text-xs"
        >
          <span className="font-semibold text-slate-700">최근 수집 결과:</span>
          {lastSyncReports.map((r) => (
            <span
              key={r.source}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-white border border-slate-200 text-slate-700"
            >
              <strong className="uppercase text-[#0F2942]">{r.source}</strong>
              <span
                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                  r.failed
                    ? 'bg-rose-100 text-rose-800'
                    : r.used_demo
                      ? 'bg-amber-100 text-amber-800'
                      : 'bg-emerald-100 text-emerald-800'
                }`}
              >
                {r.failed ? '실패' : r.used_demo ? '샘플' : '실데이터'}
              </span>
              <span>{r.message || `${r.fetched_count}건`}</span>
            </span>
          ))}
        </div>
      )}

      {/* 상단 필터 바 (<search> semantic element) */}
      <search className="bg-white border border-slate-200 rounded-xl p-3.5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div
            role="tablist"
            aria-label="공고 수집 소스 필터"
            className="mwg-tablist"
            onKeyDown={handleTabKeyDown}
          >
            {SOURCE_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={sourceTab === tab.key}
                onClick={() => setSourceTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            {quotas.map((q) => (
              <span key={q.source} className="px-2 py-1 rounded bg-slate-100 tabular-nums">
                {q.source.toUpperCase()} 잔여 {q.remaining}/{q.daily_limit}
              </span>
            ))}
          </div>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void loadBids();
          }}
          className="flex flex-wrap items-end gap-3"
        >
          <div className="flex-1 min-w-[200px]">
            <label htmlFor={searchInputId} className="block text-xs font-semibold text-slate-600 mb-1">
              공고명·기관·공고번호 검색
            </label>
            <input
              id={searchInputId}
              name="q"
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.nativeEvent.isComposing) return;
              }}
              placeholder="예: 디지털, 성동구, 2026"
              className="w-full px-3 py-1.5 text-sm rounded-lg border border-slate-300 bg-white"
            />
          </div>

          <div>
            <label htmlFor={statusSelectId} className="block text-xs font-semibold text-slate-600 mb-1">
              검토 상태
            </label>
            <select
              id={statusSelectId}
              name="status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-300 bg-white"
            >
              <option value="all">전체 상태</option>
              <option value="pending">검토 대기</option>
              <option value="approved">제안 진행(승인)</option>
              <option value="hold">보류</option>
              <option value="excluded">제외</option>
            </select>
          </div>

          <div className="flex items-center gap-2 pb-1.5">
            <input
              id={privCheckId}
              type="checkbox"
              checked={privilegeOnly}
              onChange={(e) => setPrivilegeOnly(e.target.checked)}
              className="w-4 h-4 accent-[#0D9488]"
            />
            <label htmlFor={privCheckId} className="text-xs font-semibold text-[#0F2942] cursor-pointer">
              수의계약 특례(5천만·1억)만 보기
            </label>
          </div>

          <button
            type="submit"
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 text-white hover:bg-slate-700"
          >
            <Filter className="w-3.5 h-3.5" />
            적용
          </button>
        </form>
      </search>

      {/* 좌측 반응형 공고 테이블 + 우측 상세 HITL 드로어 */}
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
        <div className="xl:col-span-7">
          <div tabIndex={0} role="region" aria-labelledby="bids-table-caption" className="table-wrapper">
            <table className="bids-table">
              <caption id="bids-table-caption" className="visually-hidden">
                사회적기업 맞춤 공공입찰 및 지원사업 공고 목록
              </caption>
              <thead>
                <tr>
                  <th scope="col">구분</th>
                  <th scope="col">공고명</th>
                  <th scope="col">기관</th>
                  <th scope="col">추정가격</th>
                  <th scope="col">마감일</th>
                  <th scope="col">적합도</th>
                  <th scope="col">상태</th>
                </tr>
              </thead>
              <tbody>
                {bids.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="text-center py-10 text-xs text-slate-500">
                      {loading ? '공고를 불러오는 중...' : '표시할 공고가 없습니다. [샘플 공고 12건 로드] 또는 [오늘의 맞춤 공고 수집]을 실행하세요.'}
                    </td>
                  </tr>
                ) : (
                  bids.map((b) => (
                    <tr
                      key={b.id}
                      data-selected={b.id === selectedId ? 'true' : 'false'}
                      onClick={() => setSelectedId(b.id)}
                      className="cursor-pointer"
                    >
                      <td>
                        <span className="px-1.5 py-0.5 rounded bg-slate-100 text-[10px] font-bold uppercase text-slate-700">
                          {b.source}
                        </span>
                      </td>
                      <th scope="row">
                        <div className="max-w-[260px]">
                          <div className="text-xs font-bold text-slate-900 line-clamp-2">{b.title}</div>
                          <div className="flex flex-wrap gap-1 mt-1">
                            {b.is_demo && (
                              <span className="px-1.5 py-0.5 rounded bg-amber-100 text-[10px] font-bold text-amber-900 border border-amber-300">
                                샘플 공고
                              </span>
                            )}
                            {b.privilege_label && (
                              <span className="px-1.5 py-0.5 rounded bg-teal-50 text-[10px] font-bold text-teal-800 border border-teal-200">
                                {b.privilege_label}
                              </span>
                            )}
                            {b.rule_warnings.length > 0 && (
                              <span className="px-1.5 py-0.5 rounded bg-amber-50 text-[10px] font-bold text-amber-800 border border-amber-200">
                                확인 필요 {b.rule_warnings.length}
                              </span>
                            )}
                          </div>
                        </div>
                      </th>
                      <td className="text-xs text-slate-600">{b.agency}</td>
                      <td className="text-xs font-semibold text-slate-800 tabular-nums whitespace-nowrap">
                        {b.estimated_price > 0 ? `${(b.estimated_price / 10000).toLocaleString()}만원` : '-'}
                      </td>
                      <td className="text-xs text-slate-600 tabular-nums whitespace-nowrap">
                        {b.deadline ? b.deadline.slice(0, 10) : '-'}
                      </td>
                      <td>
                        <ScoreRing score={b.ai_score} size={36} />
                      </td>
                      <td>{statusBadge(b.status)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 우측 HITL 상세 검토 패널 */}
        <aside className="xl:col-span-5 bg-white border border-slate-200 rounded-xl p-5 space-y-4">
          {!selectedBid ? (
            <div className="py-12 text-center text-xs text-slate-500">좌측 목록에서 공고를 선택하세요.</div>
          ) : (
            <>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-1.5 mb-1">
                    <span className="px-2 py-0.5 rounded bg-[#0F2942] text-white text-[10px] font-bold uppercase">
                      {selectedBid.source} · {selectedBid.bid_no}
                    </span>
                    {statusBadge(selectedBid.status)}
                  </div>
                  {selectedBid.is_demo && (
                    <p
                      role="note"
                      className="mb-1.5 px-2 py-1 rounded bg-amber-50 border border-amber-300 text-[11px] font-semibold text-amber-900"
                    >
                      앱 내장 샘플 공고입니다. 실제 나라장터·지원사업 공고가 아니므로 입찰·제출 대상이 아닙니다.
                    </p>
                  )}
                  <h2 className="text-base font-bold text-slate-900 leading-snug">{selectedBid.title}</h2>
                  <p className="text-xs text-slate-500 mt-1">
                    {selectedBid.agency} · {selectedBid.contract_method} · 지역: {selectedBid.region_limit}
                  </p>
                </div>
                <ScoreRing score={selectedBid.ai_score} size={52} />
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs bg-slate-50 p-3 rounded-lg border border-slate-200">
                <div>
                  <span className="text-slate-500 block">추정가격 (VAT 제외)</span>
                  <strong className="text-slate-900 tabular-nums">
                    {selectedBid.estimated_price > 0 ? `${selectedBid.estimated_price.toLocaleString('ko-KR')}원` : '별도 공고'}
                  </strong>
                </div>
                <div>
                  <span className="text-slate-500 block">마감 일시</span>
                  <strong className="text-slate-900 tabular-nums">{selectedBid.deadline || '-'}</strong>
                </div>
              </div>

              {/* AI 추천 사유 및 규칙/리스크 */}
              <div className="space-y-2">
                <h3 className="text-xs font-bold text-[#0F2942]">AI 적합도 분석 및 추천 사유</h3>
                <p className="text-xs text-slate-700 leading-relaxed bg-teal-50/50 border border-teal-200 rounded-lg p-3">
                  {selectedBid.ai_summary || 'AI 평가 요약이 생성되었습니다.'}
                </p>
                {(selectedBid.rule_warnings.length > 0 || selectedBid.ai_risks.length > 0) && (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 space-y-1">
                    <div className="text-xs font-bold text-amber-900 flex items-center gap-1">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      사전 점검 및 리스크
                    </div>
                    <ul className="list-disc ml-4 text-xs text-amber-900 space-y-0.5">
                      {[...selectedBid.rule_warnings, ...selectedBid.ai_risks].map((w, idx) => (
                        <li key={idx}>{w}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* HITL 액션 게이트 */}
              <div className="pt-2 border-t border-slate-200 space-y-2">
                <div className="text-xs font-bold text-slate-700">Human-in-the-Loop 검토 결정</div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => handleDecision(selectedBid, 'approved')}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
                  >
                    <Download className="w-3.5 h-3.5" />
                    제안 진행(승인) · 첨부 다운로드
                  </button>
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => handleDecision(selectedBid, 'hold')}
                    className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-amber-50 text-amber-900 border border-amber-300 hover:bg-amber-100"
                  >
                    <PauseCircle className="w-3.5 h-3.5" />
                    보류
                  </button>
                  <button
                    type="button"
                    disabled={deciding}
                    onClick={() => setExcludeTarget(selectedBid)}
                    className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-300 hover:bg-slate-200"
                  >
                    <XCircle className="w-3.5 h-3.5" />
                    제외
                  </button>
                </div>
              </div>

              {/* 승인된 공고의 Go / No-Go 체크리스트 & 제안서 인계 */}
              {selectedBid.rfp_analysis && (
                <div className="pt-3 border-t border-slate-200 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs font-bold text-[#0F2942]">Go / No-Go 참가자격 정밀 검증</h3>
                    <button
                      type="button"
                      onClick={() => handleOpenInProposal(selectedBid)}
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F]"
                    >
                      제안서 워크스페이스로 인계 <ExternalLink className="w-3 h-3" />
                    </button>
                  </div>

                  <div className="space-y-1.5">
                    {selectedBid.rfp_analysis.go_nogo.map((g, i) => (
                      <div key={i} className="text-xs p-2.5 rounded-lg border border-slate-200 bg-slate-50">
                        <div className="flex items-center justify-between font-bold text-slate-900">
                          <span>{g.criterion}</span>
                          <span
                            className={`inline-flex items-center gap-1 ${
                              g.status === 'pass'
                                ? 'text-emerald-700'
                                : g.status === 'warn'
                                  ? 'text-amber-700'
                                  : 'text-blue-700'
                            }`}
                          >
                            <CheckCircle2 className="w-3.5 h-3.5" />
                            {g.status === 'pass' ? '충족' : g.status === 'warn' ? '주의' : '확인 필요'}
                          </span>
                        </div>
                        <div className="text-slate-600 mt-0.5">요구: {g.required_value} / 당사: {g.company_value}</div>
                        {g.note && <div className="text-[11px] text-slate-500 mt-0.5">{g.note}</div>}
                      </div>
                    ))}
                  </div>
                  <p className="text-[11px] text-slate-500 bg-slate-100 px-2.5 py-1.5 rounded">
                    ※ {selectedBid.rfp_analysis.disclaimer}
                  </p>
                </div>
              )}

              {selectedBid.status === 'approved' && !selectedBid.rfp_analysis && (
                <button
                  type="button"
                  onClick={() => handleOpenInProposal(selectedBid)}
                  className="w-full py-2 rounded-lg text-xs font-bold bg-[#0F2942] text-white hover:bg-[#1E3A5F]"
                >
                  제안서 워크스페이스 열기
                </button>
              )}
            </>
          )}
        </aside>
      </div>

      {/* 제외 사유 입력 모달 (Native <dialog>) */}
      <NativeDialog
        open={Boolean(excludeTarget)}
        title="공고 제외 사유 기록 (AI 학습 반영)"
        onClose={() => setExcludeTarget(null)}
      >
        <form onSubmit={handleConfirmExclude} className="space-y-4">
          <p className="text-xs text-slate-600">
            <strong>{excludeTarget?.title}</strong> 공고를 제외합니다. 기록된 사유는 다음 공고 수집 시 AI 적합도 평가의 네거티브 참고 예시로 자동 반영됩니다.
          </p>
          <div>
            <label htmlFor={excludeSelectId} className="block text-xs font-bold text-slate-700 mb-1">
              주요 제외 사유 태그
            </label>
            <select
              id={excludeSelectId}
              name="exclusion_reason"
              autoFocus
              value={excludeReason}
              onChange={(e) => setExcludeReason(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300 bg-white"
            >
              {EXCLUSION_REASONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={excludeNoteId} className="block text-xs font-bold text-slate-700 mb-1">
              세부 메모 (선택)
            </label>
            <textarea
              id={excludeNoteId}
              name="exclusion_note"
              rows={2}
              value={excludeNote}
              onChange={(e) => setExcludeNote(e.target.value)}
              placeholder="예: 사업기간 대비 단가가 낮고 지방 출장 비용 과다"
              className="w-full px-3 py-2 text-sm rounded-lg border border-slate-300"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setExcludeTarget(null)}
              className="px-3.5 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200"
            >
              취소
            </button>
            <button
              type="submit"
              className="px-4 py-2 rounded-lg text-xs font-bold bg-rose-600 text-white hover:bg-rose-700"
            >
              제외 확정 및 피드백 저장
            </button>
          </div>
        </form>
      </NativeDialog>
    </div>
  );
};
