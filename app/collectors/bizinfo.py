"""기업마당(Bizinfo) Open API 수집기 및 승인 공고 URL 첨부 링크 추출."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from app.collectors.base import request_with_backoff
from app.config import get_downloads_dir, load_config

logger = logging.getLogger(__name__)

_FILE_LINK_RE = re.compile(r'href=["\']([^"\']+\.(?:hwpx|hwp|pdf))["\']', re.IGNORECASE)


async def fetch_bizinfo_notices() -> tuple[list[dict[str, Any]], str]:
    cfg = load_config()["public_api"]
    key = str(cfg.get("bizinfo_api_key") or "").strip()
    endpoint = str(cfg.get("bizinfo_endpoint_url") or "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do").strip()
    if not key:
        return [], "기업마당 API 키 미설정"

    params = {
        "crtfcKey": key,
        "dataType": "json",
        "searchCnt": "40",
        "hashtags": str(cfg.get("bizinfo_hashtags") or "사회적기업,ESG"),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await request_with_backoff(client, endpoint, params, source="bizinfo")
        data = resp.json()

    items = data.get("jsonArray") or data.get("items") or []
    results: list[dict[str, Any]] = []
    for it in items:
        title = str(it.get("pblancNm") or "").strip()
        if not title:
            continue
        bid_no = str(it.get("pblancId") or it.get("seq") or title[:24]).strip()
        results.append(
            {
                "source": "bizinfo",
                "bid_no": bid_no,
                "bid_ord": "00",
                "title": title,
                "agency": str(it.get("jrsdInsttNm") or it.get("excInsttNm") or "중소벤처기업부/지자체").strip(),
                "category": str(it.get("pldirSportRealmLclasCodeNm") or "지원사업").strip(),
                "contract_method": "정부지원사업 공모",
                "estimated_price": 0,
                "deadline": str(it.get("reqstBeginEndDe") or "").strip(),
                "published_at": str(it.get("creatPnttm") or "").strip(),
                "region_limit": str(it.get("areaNm") or "전국").strip(),
                "license_limit": str(it.get("trgetNm") or "중소·사회적기업").strip(),
                "is_sme_competing": False,
                "url": str(it.get("pblancUrl") or "").strip(),
                "raw_json": it,
                "attachments": [],
            }
        )
    return results, f"기업마당 {len(results)}건 수집 완료"


async def extract_and_download_bizinfo_attachments(bid_no: str, page_url: str) -> list[dict[str, Any]]:
    """사람 승인 후 기업마당 공고 URL을 열어 .hwpx/.hwp/.pdf 첨부 링크를 추출·저장한다."""
    if not page_url or not page_url.startswith("http"):
        return []
    target_dir = get_downloads_dir() / bid_no
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(page_url)
            resp.raise_for_status()
            links = _FILE_LINK_RE.findall(resp.text)[:4]
            for raw_link in links:
                abs_url = urljoin(page_url, raw_link)
                fname = Path(abs_url.split("?")[0]).name or f"{bid_no}_notice.pdf"
                local_path = target_dir / fname
                try:
                    fr = await client.get(abs_url)
                    fr.raise_for_status()
                    local_path.write_bytes(fr.content)
                    downloaded.append(
                        {
                            "filename": fname,
                            "url": abs_url,
                            "local_path": str(local_path),
                            "size_bytes": len(fr.content),
                            "file_type": local_path.suffix.lower().lstrip("."),
                        }
                    )
                except Exception as exc:
                    logger.warning("기업마당 첨부 저장 실패(%s): %s", abs_url, exc)
    except Exception as exc:
        logger.warning("기업마당 공고 URL 조회 실패(%s): %s", page_url, exc)
    return downloaded


async def test_bizinfo_connection() -> dict[str, Any]:
    cfg = load_config()["public_api"]
    key = str(cfg.get("bizinfo_api_key") or "").strip()
    if not key:
        return {"ok": False, "message": "기업마당 crtfcKey가 비어 있습니다."}
    try:
        items, msg = await fetch_bizinfo_notices()
        return {"ok": True, "message": msg, "details": {"count": len(items)}}
    except Exception as exc:
        return {"ok": False, "message": f"기업마당 API 통신 실패: {exc}"}
