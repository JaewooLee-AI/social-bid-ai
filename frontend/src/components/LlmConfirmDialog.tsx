import React, { useCallback, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { api } from '../lib/api';
import type { LlmBudgetStatus, LlmEstimateKind } from '../types/api';
import { NativeDialog } from './NativeDialog';

export interface LlmConfirmOptions {
  /** 대화상자 제목 */
  title: string;
  /** 실행 내용 요약 (모델·섹션 수 등) */
  details: string[];
  /** 덮어쓰게 될 기존 본문 개수. 0보다 크면 시뮬레이션 모드에서도 확인한다. */
  overwriteCount?: number;
  /** 확인 버튼 문구 */
  confirmLabel?: string;
  /** 대략 비용 추정 대상 (작업 종류·호출 횟수·모델 등급) */
  estimate?: { kind: LlmEstimateKind; calls: number; tier?: 'fast' | 'strong' };
}

interface PendingState extends LlmConfirmOptions {
  live: boolean;
  budget: LlmBudgetStatus | null;
}

const won = (v: number) => `${Math.round(v).toLocaleString()}원`;

/**
 * 유료 LLM 호출 전 사용자 확인 훅.
 * - Live API 모드이거나 기존 본문을 덮어쓰는 경우에만 확인 대화상자를 띄운다.
 * - Live 모드에서는 대략 비용 추정과 이번 달 예산 사용 현황을 보여주고, 예산 소진 시 실행을 막는다.
 * - 시뮬레이션 모드 + 덮어쓰기 없음이면 즉시 true를 반환해 흐름을 방해하지 않는다.
 * - 모드·예산은 호출 시점마다 서버에서 확인한다(설정 변경 즉시 반영). 확인 실패 시 Live로 간주.
 */
export function useLlmConfirm(): {
  confirmLlm: (opts: LlmConfirmOptions) => Promise<boolean>;
  confirmDialog: React.ReactNode;
} {
  const [pending, setPending] = useState<PendingState | null>(null);
  const resolverRef = useRef<((ok: boolean) => void) | null>(null);

  const settle = useCallback((ok: boolean) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setPending(null);
    resolve?.(ok);
  }, []);

  const confirmLlm = useCallback(async (opts: LlmConfirmOptions) => {
    let live = true;
    let budget: LlmBudgetStatus | null = null;
    try {
      budget = await api.getBudget(opts.estimate?.kind, opts.estimate?.calls ?? 0, opts.estimate?.tier ?? 'strong');
      live = !budget.simulation_mode;
    } catch {
      live = true;
    }
    if (!live && !(opts.overwriteCount && opts.overwriteCount > 0)) return true;

    // 이전 대기 중인 확인이 있으면 취소 처리
    resolverRef.current?.(false);
    return new Promise<boolean>((resolve) => {
      resolverRef.current = resolve;
      setPending({ ...opts, live, budget });
    });
  }, []);

  const handleClose = useCallback(() => settle(false), [settle]);

  const budget = pending?.budget ?? null;
  const blocked = Boolean(pending?.live && budget?.exceeded);

  const confirmDialog = (
    <NativeDialog open={pending !== null} title={pending?.title || ''} onClose={handleClose}>
      {pending && (
        <div className="space-y-3 text-xs text-slate-700">
          {pending.live ? (
            <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-300 text-amber-900 flex gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>
                <strong>Live API 모드</strong>입니다. 실행 시 실제 LLM API가 호출되어 토큰 비용이 발생합니다.
              </span>
            </div>
          ) : (
            <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-200">
              시뮬레이션 모드입니다 (API 비용 없음).
            </div>
          )}
          <ul className="list-disc pl-5 space-y-1">
            {pending.details.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>

          {pending.live && budget && (
            <div className="p-2.5 rounded-lg border border-slate-200 bg-white space-y-1 tabular-nums">
              {pending.estimate && (
                <div className="flex justify-between gap-2">
                  <span>예상 비용 (대략 추정)</span>
                  <strong className="text-[#0F2942]">약 {won(budget.estimate_krw)}</strong>
                </div>
              )}
              <div className="flex justify-between gap-2">
                <span>이번 달 Live 사용액</span>
                <span>
                  {won(budget.used_krw)}
                  {budget.monthly_budget_krw > 0 ? ` / 예산 ${won(budget.monthly_budget_krw)}` : ' (월 예산 미설정)'}
                </span>
              </div>
              <p className="text-[10px] text-slate-500">
                추정치는 평균 토큰 가정 기반이며 실제 비용은 본문 길이·thinking 토큰에 따라 달라집니다.
              </p>
            </div>
          )}

          {blocked ? (
            <p className="font-semibold text-rose-700">
              이번 달 LLM 예산을 모두 사용해 실행할 수 없습니다. 설정에서 월 예산을 늘리세요.
            </p>
          ) : pending.live && budget?.would_exceed ? (
            <p className="font-semibold text-amber-700">이 작업으로 이번 달 예산을 초과할 수 있습니다.</p>
          ) : null}

          {pending.overwriteCount ? (
            <p className="font-semibold text-rose-700">
              이미 작성된 본문 {pending.overwriteCount}개 섹션이 새 초안으로 덮어써집니다.
            </p>
          ) : null}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              autoFocus
              onClick={() => settle(false)}
              className="px-3 py-2 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700"
            >
              취소
            </button>
            <button
              type="button"
              disabled={blocked}
              onClick={() => settle(true)}
              className="px-4 py-2 rounded-lg text-xs font-bold bg-[#0D9488] text-white hover:bg-teal-700 disabled:opacity-50"
            >
              {pending.confirmLabel || '실행'}
            </button>
          </div>
        </div>
      )}
    </NativeDialog>
  );

  return { confirmLlm, confirmDialog };
}
