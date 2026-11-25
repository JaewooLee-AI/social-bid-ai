"""한국사회적기업진흥원 통합사업신청 공고 내역 (공공데이터포털 ID 15165134) 설정 기반 수집기."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.collectors.base import (
    normalize_deadline,
    normalize_service_key,
    request_with_backoff,
    safe_int_amount,
    stable_notice_id,
)
from app.config import load_config

logger = logging.getLogger(__name__)


async def fetch_seis_notices() -> tuple[list[dict[str, Any]], str]:
    """외부 config.json의 엔드포인트·추가 파라미터 바인딩으로 진흥원 통합사업신청 공고를 조회한다."""
    cfg = load_config()["public_api"]
    key = normalize_service_key(cfg.get("seis_service_key", ""))
    endpoint = str(cfg.get("seis_endpoint_url") or "").strip()
    if not key or not endpoint:
        return [], "한국사회적기업진흥원 API 키 또는 엔드포인트 미설정"

    params: dict[str, Any] = {
        "serviceKey": key,
        "pageNo": "1",
        "numOfRows": "50",
        "type": "json",
    }
    extra = cfg.get("seis_extra_params") or {}
    if isinstance(extra, dict):
        params.update({k: str(v) for k, v in extra.items() if v})

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await request_with_backoff(client, endpoint, params, source="seis")
        data = resp.json()

    items = (
        data.get("data")
        or data.get("response", {}).get("body", {}).get("items", [])
        or []
    )
    if isinstance(items, dict):
        items = items.get("item", [])

    results: list[dict[str, Any]] = []
    for it in items or []:
        title = str(it.get("pblancNm") or it.get("bizNm") or it.get("title") or "").strip()
        if not title:
            continue
        deadline_raw = it.get("reqstEndDe") or it.get("endDe") or ""
        published = str(it.get("reqstBeginDe") or it.get("creatPnttm") or "").strip()
        url = str(it.get("pblancUrl") or it.get("url") or "").strip()
        # 원천 ID가 없으면 목록 순번(SEIS-1…) 대신 내용 기반 고정 ID: 다른 날 다른 공고가 같은 ID로 덮어쓰이지 않게
        bid_no = str(it.get("pblancId") or it.get("sn") or "").strip() or stable_notice_id(
            "SEIS", title, published, deadline_raw, url
        )
        results.append(
            {
                "source": "seis",
                "bid_no": bid_no,
                "bid_ord": "00",
                "title": title,
                "agency": str(it.get("jrsdInsttNm") or it.get("insttNm") or "한국사회적기업진흥원").strip(),
                "category": str(it.get("bizFld") or "지원사업").strip(),
                "contract_method": "공모·지원사업",
                "estimated_price": safe_int_amount(it.get("supportAmt")),
                "deadline": normalize_deadline(deadline_raw),
                "published_at": published,
                "region_limit": str(it.get("areaNm") or "전국").strip(),
                "license_limit": str(it.get("trgetNm") or "사회적기업·예비사회적기업").strip(),
                "is_sme_competing": False,
                "url": url,
                "raw_json": it,
                "attachments": [],
            }
        )
    return results, f"한국사회적기업진흥원 {len(results)}건 수집 완료"


async def test_seis_connection() -> dict[str, Any]:
    cfg = load_config()["public_api"]
    key = normalize_service_key(cfg.get("seis_service_key", ""))
    endpoint = str(cfg.get("seis_endpoint_url") or "").strip()
    if not key:
        return {"ok": False, "message": "한국사회적기업진흥원 ServiceKey가 비어 있습니다."}
    try:
        items, msg = await fetch_seis_notices()
        return {"ok": True, "message": msg, "details": {"endpoint": endpoint, "count": len(items)}}
    except Exception as exc:
        return {"ok": False, "message": f"진흥원 API 통신 실패: {exc}", "details": {"endpoint": endpoint}}
