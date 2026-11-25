"""세션 CSRF 토큰 검증(pywebview.token) 및 공통 API 의존성."""

from __future__ import annotations

import os
import secrets
from fastapi import Header, HTTPException, Request

_SESSION_TOKEN: str = os.environ.get("SOCIALBID_DEV_TOKEN") or secrets.token_hex(16)


def set_session_token(token: str) -> None:
    global _SESSION_TOKEN
    _SESSION_TOKEN = token


def get_session_token() -> str:
    return _SESSION_TOKEN


async def verify_csrf_token(
    request: Request,
    x_pywebview_token: str | None = Header(default=None, alias="X-PyWebView-Token"),
) -> str:
    """모든 로컬 API 호출에서 pywebview.token(X-PyWebView-Token)을 검증해 CSRF 공격을 방지한다."""
    token = x_pywebview_token or request.query_params.get("token") or ""
    if not token or not secrets.compare_digest(token, _SESSION_TOKEN):
        raise HTTPException(
            status_code=403,
            detail="유효하지 않은 PyWebview 세션 토큰입니다 (X-PyWebView-Token 누락 또는 불일치).",
        )
    return token
