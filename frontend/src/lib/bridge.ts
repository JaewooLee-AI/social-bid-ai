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

export function isPyWebviewEnv(): boolean {
  return typeof window !== 'undefined' && Boolean(window.pywebview?.api);
}

export async function resolveSessionToken(): Promise<string> {
  if (cachedToken) return cachedToken;

  if (typeof window !== 'undefined') {
    if (window.pywebview?.token) {
      cachedToken = window.pywebview.token;
      return cachedToken;
    }

    if (window.pywebview?.api?.get_runtime_info) {
      try {
        const info = await window.pywebview.api.get_runtime_info();
        if (info?.token) {
          cachedToken = info.token;
          return cachedToken;
        }
      } catch {
        // fallback below
      }
    }

    // pywebviewready 이벤트 최대 600ms 대기 후 dev-bootstrap 확인
    await new Promise<void>((resolve) => {
      let done = false;
      const timer = window.setTimeout(() => {
        if (!done) {
          done = true;
          resolve();
        }
      }, 450);
      window.addEventListener(
        'pywebviewready',
        () => {
          if (!done) {
            done = true;
            window.clearTimeout(timer);
            resolve();
          }
        },
        { once: true }
      );
    });

    if (window.pywebview?.token) {
      cachedToken = window.pywebview.token;
      return cachedToken;
    }
    if (window.pywebview?.api?.get_runtime_info) {
      try {
        const info = await window.pywebview.api.get_runtime_info();
        if (info?.token) {
          cachedToken = info.token;
          return cachedToken;
        }
      } catch {
        // ignore
      }
    }
  }

  // 개발 모드(--dev-server) 부트스트랩
  try {
    const resp = await fetch('/api/dev-bootstrap');
    if (resp.ok) {
      const data = await resp.json();
      if (data?.token) {
        cachedToken = String(data.token);
        return cachedToken;
      }
    }
  } catch {
    // ignore
  }
  return cachedToken;
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
