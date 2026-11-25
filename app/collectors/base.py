"""공공 Open API 공통 유틸리티: 7일 윈도우 분할, Encoding/Decoding 키 정규화, 코드 22/23 백오프, 일일 호출량 추적."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from app.config import load_config
from app.db import get_conn

logger = logging.getLogger(__name__)


def normalize_service_key(key: str) -> str:
    """공공데이터포털 Encoding/Decoding 인증키 혼동 방지를 위해 디코딩된 원문 키로 정규화한다."""
    raw = (key or "").strip()
    if not raw:
        return ""
    if "%" in raw:
        return unquote(raw)
    return raw


def chunk_date_range(
    start_dt: datetime,
    end_dt: datetime,
    max_days: int = 7,
) -> list[tuple[datetime, datetime]]:
    """
    2026-11-10 나라장터 입찰공고정보서비스 7일 조회 제한에 맞춰
    조회 기간을 max_days 이하의 비중복 윈도우 목록으로 분할한다.
    """
    if end_dt < start_dt:
        return []
    # 안전 마진: 7일 미만(예: 6일 23시간 59분)
    span = timedelta(days=max_days) - timedelta(minutes=1)
    windows: list[tuple[datetime, datetime]] = []
    cur = start_dt
    while cur <= end_dt:
        win_end = min(end_dt, cur + span)
        windows.append((cur, win_end))
        cur = win_end + timedelta(minutes=1)
    return windows


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def increment_quota(source: str, calls: int = 1) -> None:
    cfg = load_config()
    limits = cfg["public_api"].get("daily_limits", {})
    limit = int(limits.get(source, 1000))
    today = _today_str()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT call_count FROM api_quota_log WHERE date_str = ? AND source = ?",
            (today, source),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE api_quota_log SET call_count = call_count + ?, daily_limit = ? WHERE date_str = ? AND source = ?",
                (calls, limit, today, source),
            )
        else:
            conn.execute(
                "INSERT INTO api_quota_log (date_str, source, call_count, daily_limit) VALUES (?, ?, ?, ?)",
                (today, source, calls, limit),
            )


def remaining_quota(source: str) -> int:
    """오늘 남은 공공 API 호출 가능 횟수(설정의 daily_limits 기준)."""
    cfg = load_config()
    limit = int(cfg["public_api"].get("daily_limits", {}).get(source, 1000))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT call_count FROM api_quota_log WHERE date_str = ? AND source = ?",
            (_today_str(), source),
        ).fetchone()
    used = int(row["call_count"]) if row else 0
    return max(0, limit - used)


def get_quota_statuses() -> list[dict[str, Any]]:
    cfg = load_config()
    limits = cfg["public_api"].get("daily_limits", {})
    today = _today_str()
    labels = {
        "g2b": "조달청 나라장터 (BidPublicInfoService02)",
        "seis": "한국사회적기업진흥원 (통합사업신청)",
        "bizinfo": "기업마당 (Bizinfo 지원사업)",
    }
    out: list[dict[str, Any]] = []
    with get_conn() as conn:
        for src in ("g2b", "seis", "bizinfo"):
            limit = int(limits.get(src, 1000))
            qrow = conn.execute(
                "SELECT call_count FROM api_quota_log WHERE date_str = ? AND source = ?",
                (today, src),
            ).fetchone()
            srow = conn.execute(
                "SELECT last_synced_at, last_status FROM sync_state WHERE source = ?",
                (src,),
            ).fetchone()
            used = int(qrow["call_count"]) if qrow else 0
            out.append(
                {
                    "source": src,
                    "label": labels[src],
                    "used_today": used,
                    "daily_limit": limit,
                    "remaining": max(0, limit - used),
                    "last_synced_at": srow["last_synced_at"] if srow else "",
                    "last_status": srow["last_status"] if srow else "idle",
                }
            )
    return out


def update_sync_state(source: str, status: str, count: int) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sync_state (source, last_synced_at, last_status, last_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                last_synced_at = excluded.last_synced_at,
                last_status = excluded.last_status,
                last_count = excluded.last_count
            """,
            (source, now, status, count),
        )


def get_last_synced_at(source: str) -> datetime | None:
    with get_conn() as conn:
        row = conn.execute("SELECT last_synced_at FROM sync_state WHERE source = ?", (source,)).fetchone()
    if not row or not row["last_synced_at"]:
        return None
    try:
        return datetime.fromisoformat(row["last_synced_at"])
    except Exception:
        return None


async def request_with_backoff(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    source: str,
    max_retries: int = 3,
) -> httpx.Response:
    """공공 API 코드 22(일일 한도 초과) 및 코드 23(초당 호출 초과) / 429 대응 지수 백오프 재시도."""
    delay = 0.8
    for attempt in range(max_retries):
        increment_quota(source, 1)
        resp = await client.get(url, params=params)
        text_lower = resp.text[:400]
        # 공공데이터포털 OpenAPI 오류 코드 판별
        if "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in text_lower or ">22<" in text_lower:
            raise RuntimeError(f"[{source}] 공공 API 일일 호출 한도 초과 (코드 22)")
        if (
            resp.status_code == 429
            or "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS" in text_lower
            or ">23<" in text_lower
        ):
            if attempt == max_retries - 1:
                raise RuntimeError(f"[{source}] 초당 호출 한도 초과 (코드 23)")
            await asyncio.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"[{source}] API 요청 재시도 한도 초과")
