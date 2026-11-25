import React, { useEffect, useId, useRef, useState } from 'react';
import { FileUp, FolderOpen } from 'lucide-react';
import { isPyWebviewEnv, nativeOpenFile } from '../lib/bridge';

interface FileDropzoneProps {
  accept?: string;
  dialogMode?: 'document' | 'hwpx' | 'rag';
  label: string;
  hint: string;
  onFileSelected: (file: File) => void;
  onNativePathSelected?: (path: string) => void;
  disabled?: boolean;
}

const DROP_DEDUP_WINDOW_MS = 1500;
const PYWEBVIEW_DROP_FALLBACK_DELAY_MS = 250;

function extractFileName(pathOrName: string): string {
  return pathOrName.split(/[\\/]/).pop() || pathOrName;
}

export const FileDropzone: React.FC<FileDropzoneProps> = ({
  accept = '.hwpx,.hwp,.pdf',
  dialogMode = 'document',
  label,
  hint,
  onFileSelected,
  onNativePathSelected,
  disabled = false,
}) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const pendingDropTimerRef = useRef<number | null>(null);
  const lastDropRef = useRef<{ name: string; at: number } | null>(null);

  const isRecentDuplicate = (name: string): boolean => {
    const last = lastDropRef.current;
    return Boolean(last && last.name === name && Date.now() - last.at < DROP_DEDUP_WINDOW_MS);
  };

  const markHandled = (name: string): void => {
    lastDropRef.current = { name, at: Date.now() };
  };

  const clearPendingDropTimer = (): void => {
    if (pendingDropTimerRef.current !== null) {
      window.clearTimeout(pendingDropTimerRef.current);
      pendingDropTimerRef.current = null;
    }
  };

  useEffect(() => clearPendingDropTimer, []);

  // PyWebview 강화 드래그앤드롭(pywebviewFullPath) 수신기
  useEffect(() => {
    if (!onNativePathSelected) return;
    const handler = (ev: Event) => {
      // PyWebview 네이티브 드롭이 수신되면 HTML5 onDrop 폴백 타이머를 취소한다.
      clearPendingDropTimer();
      const custom = ev as CustomEvent<{ paths?: string[] }>;
      const paths = custom.detail?.paths || [];
      if (paths.length === 0 || disabled) return;
      const name = extractFileName(paths[0]);
      if (isRecentDuplicate(name)) return;
      markHandled(name);
      onNativePathSelected(paths[0]);
    };
    window.addEventListener('pywebview-file-drop', handler);
    return () => window.removeEventListener('pywebview-file-drop', handler);
  }, [onNativePathSelected, disabled]);

  const handleBrowseClick = async () => {
    if (disabled) return;
    if (isPyWebviewEnv() && onNativePathSelected) {
      const res = await nativeOpenFile(dialogMode);
      if (res.ok && res.path) {
        onNativePathSelected(res.path);
        return;
      }
      if (res.cancelled) return;
    }
    inputRef.current?.click();
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    // PyWebview에서 file.path 또는 pywebviewFullPath가 전달되지 않은 브라우저 환경은 File 업로드로 처리
    const maybePath = (file as File & { pywebviewFullPath?: string; path?: string }).pywebviewFullPath;
    if (maybePath && onNativePathSelected) {
      if (isRecentDuplicate(file.name)) return;
      markHandled(file.name);
      onNativePathSelected(maybePath);
      return;
    }
    if (isPyWebviewEnv() && onNativePathSelected) {
      if (isRecentDuplicate(file.name)) return;
      // WKWebView에서 네이티브 pywebview-file-drop 이벤트가 직전/직후 도착할 수 있으므로 잠시 대기 후 폴백 실행
      clearPendingDropTimer();
      pendingDropTimerRef.current = window.setTimeout(() => {
        pendingDropTimerRef.current = null;
        if (isRecentDuplicate(file.name)) return;
        markHandled(file.name);
        onFileSelected(file);
      }, PYWEBVIEW_DROP_FALLBACK_DELAY_MS);
      return;
    }
    if (isRecentDuplicate(file.name)) return;
    markHandled(file.name);
    onFileSelected(file);
  };

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`rounded-xl border-2 border-dashed p-4 transition-colors ${
        dragging
          ? 'border-[#0D9488] bg-teal-50/70'
          : 'border-slate-300 bg-slate-50/70 hover:border-slate-400'
      }`}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={accept}
        className="visually-hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFileSelected(f);
          e.target.value = '';
        }}
      />
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-white border border-slate-200 text-[#0F2942] shrink-0">
          <FileUp className="w-5 h-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-slate-900">{label}</div>
          <p className="text-xs text-slate-600 mt-0.5">{hint}</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={handleBrowseClick}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-[#0F2942] text-white hover:bg-[#1E3A5F] disabled:opacity-50"
            >
              <FolderOpen className="w-3.5 h-3.5" />
              파일 선택 ({isPyWebviewEnv() ? '네이티브 경로' : '업로드'})
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
