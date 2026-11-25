"""PyWebview js_api 네이티브 브리지: 파일 열기/저장 대화상자, 폴더 열기, 런타임/토큰 정보."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.api.deps import get_session_token
from app.config import get_data_dir, get_exports_dir


class NativeApi:
    """window.pywebview.api 로 프론트엔드에 노출되는 OS 네이티브 브리지."""

    def __init__(self) -> None:
        self._window: Any = None

    def attach_window(self, window: Any) -> None:
        self._window = window

    def get_runtime_info(self) -> dict[str, Any]:
        """세션 토큰과 데이터/출력 경로를 프론트엔드에 안전하게 전달한다."""
        return {
            "is_pywebview": True,
            "platform": sys.platform,
            "token": get_session_token(),
            "data_dir": str(get_data_dir()),
            "exports_dir": str(get_exports_dir()),
        }

    def open_file_dialog(self, mode: str = "document") -> dict[str, Any]:
        """네이티브 파일 선택 대화상자를 열어 절대경로를 반환한다."""
        if self._window is None:
            return {"ok": False, "path": "", "message": "PyWebview 창이 연결되지 않았습니다."}

        import webview

        if mode == "hwpx":
            file_types = ("한글 문서 (*.hwpx;*.hwp)", "모든 파일 (*.*)")
        elif mode == "rag":
            file_types = ("문서 파일 (*.hwpx;*.hwp;*.pdf;*.txt;*.md)", "모든 파일 (*.*)")
        else:
            file_types = ("RFP·양식 문서 (*.hwpx;*.hwp;*.pdf)", "모든 파일 (*.*)")

        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
        )
        if not result:
            return {"ok": False, "path": "", "cancelled": True}
        selected = str(result[0]) if isinstance(result, (list, tuple)) else str(result)
        return {"ok": True, "path": selected, "filename": Path(selected).name}

    def save_file_dialog(self, default_filename: str = "output.hwpx") -> dict[str, Any]:
        """네이티브 저장 대화상자를 열어 사용자가 지정한 출력 경로를 반환한다."""
        if self._window is None:
            return {"ok": False, "path": "", "message": "PyWebview 창이 연결되지 않았습니다."}

        import webview

        ext = Path(default_filename).suffix.lower().lstrip(".") or "hwpx"
        file_types = (f"{ext.upper()} 파일 (*.{ext})", "모든 파일 (*.*)")
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=str(get_exports_dir()),
            save_filename=default_filename,
            file_types=file_types,
        )
        if not result:
            return {"ok": False, "path": "", "cancelled": True}
        selected = str(result[0]) if isinstance(result, (list, tuple)) else str(result)
        return {"ok": True, "path": selected}

    def open_folder(self, target_path: str = "") -> dict[str, Any]:
        """파일 또는 폴더를 OS 기본 탐색기(Finder / Explorer)로 연다."""
        p = Path(target_path) if target_path else get_exports_dir()
        folder = p if p.is_dir() else p.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                if p.exists() and p.is_file():
                    subprocess.Popen(["explorer", "/select,", str(p)])
                else:
                    os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                if p.exists() and p.is_file():
                    subprocess.Popen(["open", "-R", str(p)])
                else:
                    subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return {"ok": True, "folder": str(folder)}
        except Exception as exc:
            return {"ok": False, "folder": str(folder), "message": str(exc)}
