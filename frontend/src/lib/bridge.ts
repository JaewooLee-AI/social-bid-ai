// PyWebview js_api + pywebview.token 이중 브리지 유틸리티 (일반 브라우저 폴백 포함)

export interface RuntimeInfo {
  is_pywebview: boolean;
  platform: string;
  token: string;
  data_dir: string;
  exports_dir: string;
}

declare global {
  interface Window {
    pywebview?: {
      token?: string;
      api?: {
        get_runtime_info: () => Promise<RuntimeInfo>;
        open_file_dialog: (mode?: string) => Promise<{ ok: boolean; path: string; filename?: string; cancelled?: boolean }>;
        save_file_dialog: (default_filename?: string) => Promise<{ ok: boolean; path: string; cancelled?: boolean }>;
        open_folder: (target_path?: string) => Promise<{ ok: boolean; folder?: string; message?: string }>;
      };
    };
  }
}

let cachedToken = '';
// 동시에 여러 API가 호출돼도 토큰 조회는 한 번만 진행(예전: 호출마다 450ms 대기 경쟁)
let inflight: Promise<string> | null = null;

export function isPyWebviewEnv(): boolean {
  return typeof window !== 'undefined' && Boolean(window.pywebview?.api);
}

/** 403(토큰 불일치) 후 다시 조회하도록 캐시를 비운다. */
export function invalidateSessionToken(): void {
  cachedToken = '';
}

async function tokenFromPyWebview(): Promise<string> {
  if (window.pywebview?.token) return window.pywebview.token;
  if (window.pywebview?.api?.get_runtime_info) {
    try {
      const info = await window.pywebview.api.get_runtime_info();
      if (info?.token) return info.token;
    } catch {
      // ignore
    }
  }
  return '';
}

function waitForPyWebviewReady(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    if (window.pywebview?.api) {
      resolve();
      return;
    }
    let done = false;
    const finish = () => {
      if (!done) {
        done = true;
        window.clearTimeout(timer);
        window.removeEventListener('pywebviewready', finish);
        resolve();
      }
    };
    const timer = window.setTimeout(finish, ms);
    window.addEventListener('pywebviewready', finish, { once: true });
  });
}

async function devBootstrapToken(): Promise<string> {
  try {
    const resp = await fetch('/api/dev-bootstrap');
    if (resp.ok) {
      const data = await resp.json();
      if (data?.token) return String(data.token);
    }
  } catch {
    // ignore
  }
  return '';
}

async function lookupToken(): Promise<string> {
  if (typeof window === 'undefined') return '';

  let token = await tokenFromPyWebview();
  if (token) return token;

  // 짧게 pywebviewready를 기다린 뒤 개발 서버(--dev-server) 부트스트랩 확인
  await waitForPyWebviewReady(450);
  token = (await tokenFromPyWebview()) || (await devBootstrapToken());
  if (token) return token;

  // 개발 서버가 아니면 데스크톱 앱인데 브리지 준비가 늦은 경우: 빈 토큰으로 403을 받는 대신 더 기다린다
  await waitForPyWebviewReady(8000);
  return await tokenFromPyWebview();
}

export async function resolveSessionToken(): Promise<string> {
  if (cachedToken) return cachedToken;
  if (!inflight) {
    inflight = lookupToken()
      .then((token) => {
        // 빈 결과는 캐시하지 않아 다음 호출에서 다시 시도
        if (token) cachedToken = token;
        return token;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export async function nativeOpenFile(mode: 'document' | 'hwpx' | 'rag' = 'document') {
  if (window.pywebview?.api?.open_file_dialog) {
    return await window.pywebview.api.open_file_dialog(mode);
  }
  return { ok: false, path: '', cancelled: false };
}

export async function nativeSaveFile(defaultFilename: string) {
  if (window.pywebview?.api?.save_file_dialog) {
    return await window.pywebview.api.save_file_dialog(defaultFilename);
  }
  return { ok: false, path: '' };
}

export async function nativeOpenFolder(targetPath = '') {
  if (window.pywebview?.api?.open_folder) {
    return await window.pywebview.api.open_folder(targetPath);
  }
  return { ok: false, message: '브라우저 개발 모드에서는 OS 탐색기 열기를 지원하지 않습니다. 출력 경로: ' + targetPath };
}
