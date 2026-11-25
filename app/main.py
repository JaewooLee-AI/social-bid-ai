"""SocialBidAI 앱 진입점: PyWebview 창 + FastAPI 루프백 서버 + OS 드래그앤드롭 브리지."""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn

from app import APP_NAME, __version__
from app.bridge.native_api import NativeApi
from app.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _find_free_port(preferred: int = 0) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", preferred))
        return int(s.getsockname()[1])


def _wait_for_server(port: int, timeout_sec: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_sec
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"로컬 FastAPI 서버가 {timeout_sec}초 내에 응답하지 않았습니다.")


def _run_smoke_check() -> int:
    from fastapi.testclient import TestClient

    token = "smoke-test-token"
    app = create_app(session_token=token)
    client = TestClient(app)
    r_health = client.get("/api/health")
    assert r_health.status_code == 200, r_health.text

    # CSRF 보호 검증
    r_unauth = client.get("/api/dashboard/summary")
    assert r_unauth.status_code == 403, f"Expected 403 without token, got {r_unauth.status_code}"

    headers = {"X-PyWebView-Token": token}
    r_dash = client.get("/api/dashboard/summary", headers=headers)
    assert r_dash.status_code == 200, r_dash.text

    r_rubric = client.get("/api/settings/svi-rubric", headers=headers)
    assert r_rubric.status_code == 200, r_rubric.text

    api = NativeApi()
    rt = api.get_runtime_info()
    assert rt["token"] == token

    print(
        json.dumps(
            {
                "smoke_check": "ok",
                "health": r_health.json(),
                "svi_indicators": len(r_rubric.json().get("indicators", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _bind_webview_drop_handler(window: Any) -> None:
    """PyWebview DOM 드롭 이벤트에서 파일 절대경로(pywebviewFullPath)를 추출해 프론트엔드로 전달한다."""
    try:
        from webview.dom import DOMEventHandler

        def on_drop(e: Any) -> None:
            try:
                files = (e.get("dataTransfer") or {}).get("files") or []
                paths = [f.get("pywebviewFullPath") for f in files if isinstance(f, dict) and f.get("pywebviewFullPath")]
                if paths:
                    payload = json.dumps(paths, ensure_ascii=False)
                    window.evaluate_js(
                        f"window.dispatchEvent(new CustomEvent('pywebview-file-drop', {{ detail: {{ paths: {payload} }} }}));"
                    )
            except Exception as exc:
                logger.debug("Drop handler payload parse error: %s", exc)

        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=False)
    except Exception as exc:
        logger.debug("PyWebview DOM drop handler unavailable: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} v{__version__}")
    parser.add_argument("--dev-server", action="store_true", help="PyWebview 없이 FastAPI 백엔드만 단독 실행")
    parser.add_argument("--port", type=int, default=8000, help="--dev-server 실행 포트")
    parser.add_argument("--smoke-check", action="store_true", help="헤드리스 초기화 및 라우터 자가검증 후 종료")
    args = parser.parse_args()

    if args.smoke_check:
        return _run_smoke_check()

    if args.dev_server:
        os.environ["SOCIALBID_DEV_MODE"] = "1"
        dev_token = os.environ.get("SOCIALBID_DEV_TOKEN") or "dev-local-token"
        app = create_app(session_token=dev_token)
        logger.info("SocialBidAI Dev API Server running on http://127.0.0.1:%d (token=%s)", args.port, dev_token)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
        return 0

    import webview

    session_token = str(webview.token)
    port = _find_free_port(0)
    app = create_app(session_token=session_token)

    server_config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    _wait_for_server(port)

    native_api = NativeApi()
    window = webview.create_window(
        title=f"{APP_NAME} — 사회적기업 공공입찰·제안서·사업계획서 AI 스튜디오",
        url=f"http://127.0.0.1:{port}/",
        js_api=native_api,
        width=1440,
        height=920,
        min_size=(1080, 720),
    )
    native_api.attach_window(window)

    def _on_loaded() -> None:
        _bind_webview_drop_handler(window)

    window.events.loaded += _on_loaded

    try:
        webview.start(debug=os.environ.get("SOCIALBID_WEBVIEW_DEBUG") == "1")
    finally:
        server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
