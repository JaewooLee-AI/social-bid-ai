"""공공 Open API 공통 유틸리티: 7일 윈도우 분할, Encoding/Decoding 키 정규화, 코드 22/23 백오프, 일일 호출량 추적."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from app.config import load_config
from app.db import get_conn

logger = logging.getLogger(__name__)

# 승인 후 첨부 다운로드 1건당 최대 크기(외부 서버가 거대한 파일을 보내도 디스크·메모리 보호)
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024


class QuotaExceededError(RuntimeError):
    """설정의 일일 호출 한도(daily_limits)에 도달해 공공 API 호출을 하지 않음."""


def safe_int_amount(value: Any) -> int:
    """'1,234,000', '1234000원', 1.2e6, None 등을 정수 원 단위로 변환. 해석 불가 시 0."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).replace(",", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", text)
    if not m:
        return 0
    num = float(m.group(0))
    if "억" in text:
        num *= 100_000_000
    elif "천만" in text:
        num *= 10_000_000
    elif "만" in text:
        num *= 10_000
    return max(0, int(num))


_DATE_RE = re.compile(r"(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})")


def normalize_deadline(value: Any) -> str:
    """접수 기간 범위('20261101 ~ 20261130')는 마감일(마지막 날짜)만 'YYYY-MM-DD'로. 날짜가 없으면 원문 유지(예: 상시 접수)."""
    text = str(value or "").strip()
    dates = _DATE_RE.findall(text)
    if not dates:
        return text
    y, mo, d = dates[-1]
    try:
        return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
    except ValueError:
        return text


def stable_notice_id(prefix: str, *parts: Any) -> str:
    """원천 ID가 없을 때 목록 순번 대신 공고 내용으로 만든 고정 ID(다른 날 다른 공고와 겹치지 않게)."""
    raw = "|".join(str(p or "").strip() for p in parts)
    return f"{prefix}-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_path_component(value: Any, fallback: str = "file", max_len: int = 80) -> str:
    """외부 값(공고번호·첨부 파일명)을 폴더/파일 이름 하나로 안전하게 만든다(경로 이탈·예약 문자 제거)."""
    name = _UNSAFE_NAME_RE.sub("_", Path(str(value or "")).name).strip().strip(".")
    if not name:
        return fallback
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 8:
            name = stem[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    return name


async def download_to_file(client: httpx.AsyncClient, url: str, dest: Path, max_bytes: int = MAX_ATTACHMENT_BYTES) -> int:
    """스트리밍으로 받아 max_bytes를 넘으면 중단. 완료된 경우에만 대상 경로로 교체하고 바이트 수를 반환."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    total = 0
    try:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            declared = int(resp.headers.get("content-length") or 0)
            if declared > max_bytes:
                raise ValueError(f"첨부 파일이 너무 큽니다({declared:,} bytes > {max_bytes:,})")
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"첨부 파일이 너무 큽니다(> {max_bytes:,} bytes)")
                    f.write(chunk)
        os.replace(tmp, dest)
        return total
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


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


_ERROR_CODE_RES = (
    re.compile(r"<(?:returnReasonCode|resultCode)>\s*(\d+)\s*</(?:returnReasonCode|resultCode)>"),
    re.compile(r'"(?:returnReasonCode|resultCode)"\s*:\s*"?(\d+)"?'),
)


def _public_api_error_code(text: str) -> str:
    """응답의 resultCode/returnReasonCode 값만 본다. 예전 '>22<' 부분 문자열 검사는 본문 데이터(예: <bidNtceOrd>22<)에도 걸렸다."""
    for pattern in _ERROR_CODE_RES:
        m = pattern.search(text)
        if m:
            return m.group(1).lstrip("0") or "0"
    return ""


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
        if remaining_quota(source) <= 0:
            raise QuotaExceededError(
                f"[{source}] 오늘 일일 호출 한도(설정의 daily_limits)에 도달해 호출하지 않았습니다. 내일 다시 시도하거나 한도를 조정하세요."
            )
        increment_quota(source, 1)
        resp = await client.get(url, params=params)
        head = resp.text[:2000]
        code = _public_api_error_code(head)
        # 공공데이터포털 OpenAPI 오류 코드 판별
        if "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS" in head or code == "22":
            raise RuntimeError(f"[{source}] 공공 API 일일 호출 한도 초과 (코드 22)")
        if resp.status_code == 429 or "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS" in head or code == "23":
            if attempt == max_retries - 1:
                raise RuntimeError(f"[{source}] 초당 호출 한도 초과 (코드 23)")
            await asyncio.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError(f"[{source}] API 요청 재시도 한도 초과")
