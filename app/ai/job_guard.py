"""동일 리소스(사업계획서·제안서·RAG 재인덱싱)에 대한 LLM 작업 중복 실행 방지 가드.

로컬 단일 프로세스 앱이므로 프로세스 메모리 기반 잠금으로 충분하다.
정상 종료·예외·클라이언트 연결 끊김 모두에서 release()가 호출되도록 사용처에서 보장하고,
만약을 대비해 TTL이 지난 잠금은 자동 만료시켜 영구 잠김을 막는다.
"""

from __future__ import annotations

import secrets
import threading
import time

from fastapi import HTTPException

DEFAULT_TTL_SEC = 15 * 60

_lock = threading.Lock()
# key -> (작업 토큰, 시작 시각). 토큰은 늦게 도착한 release가 새 작업의 잠금을 풀지 않도록 구분한다.
_running: dict[str, tuple[str, float]] = {}


def try_acquire(key: str, ttl_sec: float = DEFAULT_TTL_SEC) -> str | None:
    """잠금을 얻으면 작업 토큰을, 이미 실행 중이면 None을 반환한다."""
    now = time.monotonic()
    with _lock:
        cur = _running.get(key)
        if cur is not None and now - cur[1] < ttl_sec:
            return None
        token = secrets.token_hex(8)
        _running[key] = (token, now)
        return token


def release(key: str, token: str) -> None:
    """해당 토큰의 잠금일 때만 해제한다(여러 번 호출해도 안전)."""
    with _lock:
        cur = _running.get(key)
        if cur is not None and cur[0] == token:
            _running.pop(key, None)


def is_running(key: str, ttl_sec: float = DEFAULT_TTL_SEC) -> bool:
    with _lock:
        cur = _running.get(key)
        return cur is not None and time.monotonic() - cur[1] < ttl_sec


def acquire_or_409(key: str, label: str) -> str:
    """잠금을 얻지 못하면 409 Conflict로 즉시 거절한다(추가 LLM 호출 차단). 성공 시 작업 토큰 반환."""
    token = try_acquire(key)
    if token is None:
        raise HTTPException(
            status_code=409,
            detail=f"{label} 작업이 이미 진행 중입니다. 완료되거나 중지된 뒤 다시 시도하세요.",
        )
    return token


def reset_all() -> None:
    """테스트용: 모든 잠금 해제."""
    with _lock:
        _running.clear()
