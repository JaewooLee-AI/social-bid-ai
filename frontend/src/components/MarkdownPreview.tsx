import React from 'react';

interface MarkdownPreviewProps {
  content: string;
}

/**
 * 외부 XSS 위험 없이 안전한 React JSX 기반 한국어 마크다운 + 표 렌더러
 */
export const MarkdownPreview: React.FC<MarkdownPreviewProps> = ({ content }) => {
  if (!content.trim()) {
    return <p className="text-sm text-slate-400 italic">아직 생성된 내용이 없습니다.</p>;
  }

  const lines = content.split('\n');
  const blocks: React.ReactNode[] = [];
  let i = 0;

  const renderInline = (text: string) => {
    const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return parts.map((p, idx) => {
      if (p.startsWith('**') && p.endsWith('**')) {
        return (
          <strong key={idx} className="font-bold text-slate-900">
            {p.slice(2, -2)}
          </strong>
        );
      }
      if (p.startsWith('`') && p.endsWith('`')) {
        return (
          <code key={idx} className="px-1 py-0.5 rounded bg-slate-100 text-xs font-mono text-[#0F2942]">
            {p.slice(1, -1)}
          </code>
        );
      }
      return <React.Fragment key={idx}>{p}</React.Fragment>;
    });
  };

  while (i < lines.length) {
    const line = lines[i].trim();
    if (!line) {
      i += 1;
      continue;
    }

    if (line.startsWith('|')) {
      const tblLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tblLines.push(lines[i].trim());
        i += 1;
      }
      const rows = tblLines
        .map((l) =>
          l
            .replace(/^\|/, '')
            .replace(/\|$/, '')
            .split('|')
            .map((c) => c.trim())
        )
        .filter((row) => !row.every((c) => /^:?-{2,}:?$/.test(c || '-')));

      if (rows.length > 0) {
        blocks.push(
          <div key={`tbl-${i}`} className="overflow-x-auto my-3 border border-slate-200 rounded-lg">
            <table className="w-full text-xs text-left border-collapse">
              <thead className="bg-slate-100 text-slate-800">
                <tr>
                  {rows[0].map((cell, ci) => (
                    <th key={ci} className="px-3 py-2 font-bold border-b border-slate-200">
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(1).map((r, ri) => (
                  <tr key={ri} className="border-b border-slate-100 last:border-0">
                    {r.map((cell, ci) => (
                      <td key={ci} className="px-3 py-2 text-slate-700">
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      continue;
    }

    if (line.startsWith('#### ')) {
      blocks.push(
        <h4 key={`h4-${i}`} className="text-sm font-bold text-[#0F2942] mt-4 mb-1.5">
          {renderInline(line.slice(5))}
        </h4>
      );
    } else if (line.startsWith('### ')) {
      blocks.push(
        <h3 key={`h3-${i}`} className="text-base font-bold text-[#0F2942] mt-4 mb-2 pb-1 border-b border-slate-200">
          {renderInline(line.slice(4))}
        </h3>
      );
    } else if (line.startsWith('## ')) {
      blocks.push(
        <h2 key={`h2-${i}`} className="text-lg font-bold text-[#0F2942] mt-5 mb-2">
          {renderInline(line.slice(3))}
        </h2>
      );
    } else if (line.startsWith('> ')) {
      blocks.push(
        <blockquote
          key={`bq-${i}`}
          className="border-l-4 border-[#0D9488] bg-teal-50/60 px-3 py-2 my-2 text-xs text-slate-700 rounded-r"
        >
          {renderInline(line.slice(2))}
        </blockquote>
      );
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      blocks.push(
        <li key={`li-${i}`} className="ml-5 list-disc text-sm text-slate-800 leading-relaxed my-1">
          {renderInline(line.slice(2))}
        </li>
      );
    } else {
      blocks.push(
        <p key={`p-${i}`} className="text-sm text-slate-800 leading-relaxed my-2">
          {renderInline(line)}
        </p>
      );
    }
    i += 1;
  }

  return <div className="space-y-0.5">{blocks}</div>;
};
