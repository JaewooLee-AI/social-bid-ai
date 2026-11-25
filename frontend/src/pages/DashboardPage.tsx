import React from 'react';
import { ArrowRight, Award, Coins, FileCheck2, FileText, Radar, RefreshCw } from 'lucide-react';
import type { DashboardSummary } from '../types/api';
import { SviGauge } from '../components/SviGauge';
import { ScoreRing } from '../components/ScoreRing';

interface DashboardPageProps {
  summary: DashboardSummary | null;
  loading: boolean;
  onRefresh: () => void;
  onSyncBids: () => Promise<void>;
  syncing: boolean;
  onNavigate: (page: 'dashboard' | 'bids' | 'proposals' | 'bizplan' | 'settings', id?: number) => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({
  summary,
  loading,
  onRefresh,
  onSyncBids,
  syncing,
  onNavigate,
}) => {
  const formatKRW = (n: number) => `${Math.round(n).toLocaleString('ko-KR')}원`;

  return (
    <div className="space-y-5">
      {summary?.profile_is_sample && (
        <div
          role="note"
          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-xs text-amber-900"
        >
          <span>
            <strong className="font-semibold">샘플 회사 정보 사용 중</strong> — 기본 제공된 가상 회사((주)함께자람) 정보로
            제안서·사업계획서가 작성됩니다. 실제 회사 정보로 바꿔 저장하세요.
          </span>
          <button
            type="button"
            onClick={() => onNavigate('settings')}
            className="px-2.5 py-1 rounded-md bg-amber-600 text-white font-semibold hover:bg-amber-700"
          >
            회사 프로필 수정
          </button>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-[#0F2942]">대시보드 & 인사이트</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            사회적기업 수의계약 특례(5천만 1인 견적·1억 2인 견적), 진행 중인 제안서·사업계획서 및 로컬 AI 비용 현황
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            disabled={syncing}
            onClick={() => void onSyncBids()}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
          >
            <Radar className="w-3.5 h-3.5" />
            {syncing ? '공고 수집 중...' : '맞춤 공고 수집 실행'}
          </button>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            새로고침
          </button>
        </div>
      </div>

      {/* 상단 KPI 4열 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
            <span>검토 대기 공고 (HITL)</span>
            <Radar className="w-4 h-4 text-[#0D9488]" />
          </div>
          <div className="mt-2 text-2xl font-bold text-[#0F2942] tabular-nums">
            {summary?.pending_review_count ?? 0}건
          </div>
          <p className="text-xs text-slate-500 mt-1 tabular-nums">
            승인 완료 {summary?.approved_bids_count ?? 0}건 · 마감 임박 {summary?.urgent_bids_count ?? 0}건
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
            <span>진행 중 제안서·사업계획서</span>
            <FileText className="w-4 h-4 text-[#0F2942]" />
          </div>
          <div className="mt-2 text-2xl font-bold text-[#0F2942] tabular-nums">
            {(summary?.active_proposals_count ?? 0) + (summary?.active_bizplans_count ?? 0)}건
          </div>
          <p className="text-xs text-slate-500 mt-1 tabular-nums">
            제안서 {summary?.active_proposals_count ?? 0}건 · 소셜 사업계획서 {summary?.active_bizplans_count ?? 0}건
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
            <span>월간 LLM 토큰 비용 (원화 환산)</span>
            <Coins className="w-4 h-4 text-amber-600" />
          </div>
          <div className="mt-2 text-2xl font-bold text-[#0F2942] tabular-nums">
            {formatKRW(summary?.monthly_cost_krw ?? 0)}
          </div>
          <p className="text-xs text-slate-500 mt-1 tabular-nums">
            총 {(summary?.monthly_tokens ?? 0).toLocaleString('ko-KR')} 토큰 (${(summary?.monthly_cost_usd ?? 0).toFixed(4)})
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl p-4">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500">
            <span>SVI 현재 / 목표 등급</span>
            <Award className="w-4 h-4 text-[#0D9488]" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold text-[#0D9488] tabular-nums">
              {(summary?.svi_current_score ?? 0).toFixed(0)}점
            </span>
            <span className="text-sm font-bold text-[#0F2942]">
              ({summary?.svi_grade_label ?? '양호'})
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            {summary?.priority_purchase_120 ? '공공기관 우선구매 120% 가중 인정 대상' : '75점 달성 시 우선구매 120% 인정'}
          </p>
        </div>
      </div>

      {/* SVI 5단계 게이지 */}
      <SviGauge
        currentScore={summary?.svi_current_score ?? 78}
        targetScore={summary?.svi_target_score ?? 88}
        gradeLabel={summary?.svi_grade_label ?? '우수'}
        priority120={summary?.priority_purchase_120 ?? true}
      />

      {/* 하단 2열 분할 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <section className="bg-white border border-slate-200 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold text-[#0F2942]">적합도 상위·마감 임박 맞춤 공고</h2>
            <button
              type="button"
              onClick={() => onNavigate('bids')}
              className="inline-flex items-center gap-1 text-xs font-semibold text-[#0D9488] hover:underline"
            >
              공고 검토 인박스 열기 <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>
          {summary?.urgent_bids && summary.urgent_bids.length > 0 ? (
            <ul className="divide-y divide-slate-100">
              {summary.urgent_bids.map((b) => (
                <li key={b.id} className="py-3 flex items-center gap-3">
                  <ScoreRing score={b.ai_score} size={42} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="px-1.5 py-0.5 rounded bg-slate-100 text-[10px] font-bold text-slate-700 uppercase">
                        {b.source}
                      </span>
                      {b.privilege_label && (
                        <span className="px-1.5 py-0.5 rounded bg-teal-50 text-[10px] font-bold text-teal-800 border border-teal-200">
                          {b.privilege_label}
                        </span>
                      )}
                    </div>
                    <div className="text-sm font-semibold text-slate-900 truncate mt-0.5">{b.title}</div>
                    <div className="text-xs text-slate-500 tabular-nums">
                      {b.agency} · 추정 {b.estimated_price > 0 ? `${b.estimated_price.toLocaleString('ko-KR')}원` : '별도 공고'} · 마감 {b.deadline || '-'}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="py-8 text-center text-xs text-slate-500">
              수집된 공고가 아직 없습니다. 상단 <strong>[맞춤 공고 수집 실행]</strong> 버튼을 눌러 시작하세요.
            </div>
          )}
        </section>

        <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-5">
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-[#0F2942]">최근 작업한 제안서·사업계획서</h2>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => onNavigate('proposals')}
                  className="text-xs font-semibold text-[#0F2942] hover:underline"
                >
                  제안서
                </button>
                <button
                  type="button"
                  onClick={() => onNavigate('bizplan')}
                  className="text-xs font-semibold text-[#0D9488] hover:underline"
                >
                  사업계획서
                </button>
              </div>
            </div>
            {summary?.recent_works && summary.recent_works.length > 0 ? (
              <ul className="divide-y divide-slate-100">
                {summary.recent_works.map((w) => (
                  <li key={`${w.kind}-${w.id}`} className="py-2.5 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-slate-900 truncate">{w.title}</div>
                      <div className="text-xs text-slate-500">{w.subtitle}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => onNavigate(w.kind === 'proposal' ? 'proposals' : 'bizplan', w.id)}
                      className="px-2.5 py-1 rounded-md text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-[#0F2942] shrink-0"
                    >
                      열기
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-slate-500 py-3">최근 작성 문서가 없습니다. 제안서 또는 사업계획서를 새로 시작해 보세요.</p>
            )}
          </div>

          <div className="pt-3 border-t border-slate-200">
            <h3 className="text-xs font-bold text-slate-700 mb-2 flex items-center gap-1.5">
              <FileCheck2 className="w-3.5 h-3.5 text-[#0D9488]" />
              공공 Open API 일일 호출 잔여량 (2026 7일 분할 수집기)
            </h3>
            <div className="space-y-2">
              {(summary?.quotas || []).map((q) => (
                <div key={q.source} className="flex items-center justify-between text-xs bg-slate-50 px-3 py-2 rounded-lg">
                  <span className="font-medium text-slate-700">{q.label}</span>
                  <span className="font-bold text-[#0F2942] tabular-nums">
                    잔여 {q.remaining.toLocaleString()} / {q.daily_limit.toLocaleString()}건
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
};
