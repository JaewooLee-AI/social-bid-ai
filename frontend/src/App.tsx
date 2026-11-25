import React, { useEffect, useRef, useState } from 'react';
import {
  FileText,
  LayoutDashboard,
  Lightbulb,
  Radar,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import { api } from './lib/api';
import type { DashboardSummary, HealthResponse } from './types/api';
import { DashboardPage } from './pages/DashboardPage';
import { BidsInboxPage } from './pages/BidsInboxPage';
import { ProposalWorkspacePage } from './pages/ProposalWorkspacePage';
import { BizPlanWizardPage } from './pages/BizPlanWizardPage';
import { SettingsPage } from './pages/SettingsPage';

type PageKey = 'dashboard' | 'bids' | 'proposals' | 'bizplan' | 'settings';

const NAV_ITEMS: { key: PageKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'dashboard', label: '대시보드', icon: LayoutDashboard },
  { key: 'bids', label: '맞춤 공고 인박스', icon: Radar },
  { key: 'proposals', label: '제안서 워크스페이스', icon: FileText },
  { key: 'bizplan', label: '소셜 사업계획서', icon: Lightbulb },
  { key: 'settings', label: '마스터 프로필·설정', icon: Settings },
];

export function App() {
  const [page, setPage] = useState<PageKey>('dashboard');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loadingDash, setLoadingDash] = useState(false);
  const [selectedProposalId, setSelectedProposalId] = useState<number | null>(null);
  const [selectedBizPlanId, setSelectedBizPlanId] = useState<number | null>(null);
  const [toast, setToast] = useState<string>('');
  // 공고 수집 실행 잠금: ref는 await 이전 동기 중복 클릭 차단, state는 버튼 비활성화
  const syncLockRef = useRef(false);
  const [syncingBids, setSyncingBids] = useState(false);

  const notify = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => {
      setToast((cur) => (cur === msg ? '' : cur));
    }, 5500);
  };

  const refreshDashboard = async () => {
    setLoadingDash(true);
    try {
      const [h, s] = await Promise.all([api.health(), api.dashboardSummary()]);
      setHealth(h);
      setSummary(s);
    } catch (e) {
      notify(`백엔드 연결 대기 중: ${(e as Error).message}`);
    } finally {
      setLoadingDash(false);
    }
  };

  useEffect(() => {
    void refreshDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleNavigate = (target: PageKey, id?: number) => {
    if (target === 'proposals' && id) setSelectedProposalId(id);
    if (target === 'bizplan' && id) setSelectedBizPlanId(id);
    setPage(target);
    if (target === 'dashboard') void refreshDashboard();
  };

  const handleSyncBids = async () => {
    if (syncLockRef.current) return;
    syncLockRef.current = true;
    setSyncingBids(true);
    try {
      const res = await api.syncBids(7);
      const failed = res.reports.filter((r) => r.failed).map((r) => r.message);
      notify(
        failed.length > 0
          ? `공고 ${res.total_saved}건 저장 · 일부 수집 실패: ${failed.join(' / ')}`
          : `공고 ${res.total_saved}건 수집·평가 완료`
      );
      await refreshDashboard();
    } catch (e) {
      notify(`수집 실패: ${(e as Error).message}`);
    } finally {
      syncLockRef.current = false;
      setSyncingBids(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col md:flex-row bg-[#F6F8FB] text-[#0F172A]">
      {/* 좌측 고정 내비게이션 (<nav> + aria-current="page") */}
      <aside className="w-full md:w-60 bg-[#0F2942] text-white flex flex-col shrink-0">
        <div className="px-4 py-4 border-b border-white/10">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-[#0D9488] flex items-center justify-center font-extrabold text-sm">
              SB
            </div>
            <div>
              <div className="text-sm font-bold tracking-tight">SocialBidAI</div>
              <div className="text-[11px] text-slate-300">사회적기업 공공입찰·사업계획</div>
            </div>
          </div>
        </div>

        <nav aria-label="주 메뉴" className="p-2.5 flex md:flex-col gap-1 overflow-x-auto flex-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isCurrent = page === item.key;
            return (
              <button
                key={item.key}
                type="button"
                aria-current={isCurrent ? 'page' : undefined}
                onClick={() => handleNavigate(item.key)}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors ${
                  isCurrent
                    ? 'bg-[#0D9488] text-white'
                    : 'text-slate-200 hover:bg-white/10'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="hidden md:block p-3.5 border-t border-white/10 text-[11px] space-y-1.5 text-slate-300">
          <div className="flex items-center gap-1.5 font-semibold text-white">
            <ShieldCheck className="w-3.5 h-3.5 text-[#0D9488]" />
            <span>월 고정비 0원 로컬 실행</span>
          </div>
          <div className="flex items-center justify-between">
            <span>AI 모드</span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                health?.simulation_mode ? 'bg-amber-400/20 text-amber-200' : 'bg-teal-400/20 text-teal-200'
              }`}
            >
              {health?.simulation_mode ? '시뮬레이션 모드' : 'Live Gemini API'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span>하이브리드 RAG</span>
            <span className="text-[10px] font-mono">
              FTS5 + {health?.vec_available ? 'vec0' : 'Fallback'}
            </span>
          </div>
        </div>
      </aside>

      {/* 우측 메인 콘텐츠 */}
      <main className="flex-1 min-w-0 p-4 md:p-6 max-w-[1440px]">
        {toast && (
          <div
            role="status"
            aria-live="polite"
            className="mb-4 px-4 py-2.5 rounded-lg bg-[#0F2942] text-white text-xs font-semibold flex items-center justify-between shadow-sm"
          >
            <span>{toast}</span>
            <button
              type="button"
              onClick={() => setToast('')}
              className="ml-4 text-slate-300 hover:text-white"
              aria-label="알림 닫기"
            >
              닫기
            </button>
          </div>
        )}

        {page === 'dashboard' && (
          <DashboardPage
            summary={summary}
            loading={loadingDash}
            onRefresh={refreshDashboard}
            onSyncBids={handleSyncBids}
            syncing={syncingBids}
            onNavigate={handleNavigate}
          />
        )}
        {page === 'bids' && (
          <BidsInboxPage
            onOpenProposal={(pid) => handleNavigate('proposals', pid)}
            onNotify={notify}
          />
        )}
        {page === 'proposals' && (
          <ProposalWorkspacePage initialProposalId={selectedProposalId} onNotify={notify} />
        )}
        {page === 'bizplan' && (
          <BizPlanWizardPage initialBizPlanId={selectedBizPlanId} onNotify={notify} />
        )}
        {page === 'settings' && (
          <SettingsPage onNotify={notify} onConfigUpdated={refreshDashboard} />
        )}
      </main>
    </div>
  );
}

export default App;
