import React from 'react';

interface SviGaugeProps {
  currentScore: number;
  targetScore: number;
  gradeLabel: string;
  priority120: boolean;
}

const SEGMENTS = [
  { label: '취약', range: '<45', widthPct: 45, bg: '#E2E8F0', text: '#334155' },
  { label: '미흡', range: '45~60', widthPct: 15, bg: '#94A3B8', text: '#FFFFFF' },
  { label: '양호', range: '60~75', widthPct: 15, bg: '#60A5FA', text: '#0F172A' },
  { label: '우수 (120%)', range: '75~90', widthPct: 15, bg: '#0D9488', text: '#FFFFFF' },
  { label: '탁월 (120%)', range: '90+', widthPct: 10, bg: '#0F2942', text: '#FFFFFF' },
];

export const SviGauge: React.FC<SviGaugeProps> = ({
  currentScore,
  targetScore,
  gradeLabel,
  priority120,
}) => {
  const curPct = Math.max(0, Math.min(100, currentScore));
  const tgtPct = Math.max(0, Math.min(100, targetScore));

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-bold text-[#0F2942]">SVI 5단계 등급 및 공공기관 우선구매 120% 구간</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            2025~2026 개편 기준 75점 이상(우수·탁월) 달성 시 공공기관 우선구매 실적 120% 가중 인정
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`px-2.5 py-1 rounded-full text-xs font-bold tabular-nums ${
              priority120 ? 'bg-teal-100 text-teal-900' : 'bg-slate-100 text-slate-700'
            }`}
          >
            현재 {currentScore.toFixed(1)}점 · {gradeLabel}
          </span>
          <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-100 text-[#0F2942] tabular-nums">
            목표 {targetScore.toFixed(1)}점
          </span>
        </div>
      </div>

      <div className="relative pt-6 pb-4">
        {/* Current & Target markers */}
        <div
          className="absolute top-0 -translate-x-1/2 flex flex-col items-center"
          style={{ left: `${curPct}%` }}
        >
          <span className="text-[11px] font-bold text-[#0D9488] whitespace-nowrap">현재 {curPct.toFixed(0)}점</span>
          <span className="w-0.5 h-3 bg-[#0D9488]" />
        </div>
        <div
          className="absolute top-0 -translate-x-1/2 flex flex-col items-center"
          style={{ left: `${tgtPct}%` }}
        >
          <span className="text-[11px] font-semibold text-[#0F2942] whitespace-nowrap">목표 {tgtPct.toFixed(0)}</span>
        </div>

        <div className="flex h-8 rounded-lg overflow-hidden border border-slate-200">
          {SEGMENTS.map((seg) => (
            <div
              key={seg.label}
              style={{ width: `${seg.widthPct}%`, backgroundColor: seg.bg, color: seg.text }}
              className="flex flex-col items-center justify-center text-[11px] font-bold leading-tight px-1 border-r border-white/40 last:border-0"
              title={`${seg.label} (${seg.range}점)`}
            >
              <span className="truncate max-w-full">{seg.label}</span>
            </div>
          ))}
        </div>

        <div className="flex justify-between text-[11px] text-slate-500 mt-1.5 tabular-nums">
          <span>0점</span>
          <span>45점</span>
          <span>60점</span>
          <span className="font-bold text-[#0D9488]">75점 (120% 인정 기준선)</span>
          <span>90점</span>
          <span>100점</span>
        </div>
      </div>
    </div>
  );
};
