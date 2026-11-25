import React from 'react';

interface ScoreRingProps {
  score: number | null;
  size?: number;
}

export const ScoreRing: React.FC<ScoreRingProps> = ({ score, size = 46 }) => {
  const val = Math.max(0, Math.min(100, Math.round(score ?? 0)));
  const stroke = 4;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const offset = circ - (val / 100) * circ;
  const color = val >= 80 ? '#0D9488' : val >= 60 ? '#0F2942' : '#D97706';

  return (
    <div
      className="relative inline-flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
      aria-label={`적합도 ${val}점`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#E2E8F0" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeDasharray={circ}
          strokeDashoffset={offset}
          strokeLinecap="round"
        />
      </svg>
      <span className="absolute text-xs font-bold tabular-nums" style={{ color }}>
        {score !== null ? val : '-'}
      </span>
    </div>
  );
};
