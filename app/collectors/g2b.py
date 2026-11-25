"""조달청 나라장터 입찰공고정보서비스 (BidPublicInfoService02) 7일 분할 수집기 및 온디맨드 첨부 다운로더."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.collectors.base import (
    QuotaExceededError,
    chunk_date_range,
    download_to_file,
    safe_int_amount,
    safe_path_component,
    normalize_service_key,
    remaining_quota,
    request_with_backoff,
)

# 페이지 수집 중 남겨둘 일일 호출 여유분(세부 면허·지역 보강, 첨부 조회, 연결 테스트용)
_QUOTA_RESERVE = 20
from app.config import get_downloads_dir, load_config

logger = logging.getLogger(__name__)

CATEGORY_OPERATIONS = {
    "용역": "getBidPblancListInfoServcPPSSrch02",
    "물품": "getBidPblancListInfoThngPPSSrch02",
    "공사": "getBidPblancListInfoCnstwkPPSSrch02",
    "외자/기타": "getBidPblancListInfoEtcPPSSrch02",
}


def _extract_g2b_error(data: dict[str, Any]) -> tuple[str, str]:
    """나라장터 HTTP 200 응답 내부의 header.resultCode / ResponseError 오류를 추출한다."""
    if not isinstance(data, dict):
        return "", ""
    for root_key in ("nkoneps.com.response.ResponseError", "response"):
        block = data.get(root_key)
        header = block.get("header") if isinstance(block, dict) else None
        if not isinstance(header, dict):
            continue
        code = str(header.get("resultCode") or "").strip()
        if code and code != "00":
            msg = str(header.get("resultMsg") or header.get("errMsg") or "").strip()
            return code, msg
    return "", ""


def _parse_g2b_items(payload: dict[str, Any], category: str) -> list[dict[str, Any]]:
    body = payload.get("response", {}).get("body", {})
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    results: list[dict[str, Any]] = []
    for it in items or []:
        bid_no = str(it.get("bidNtceNo") or "").strip()
        if not bid_no:
            continue
        bid_ord = str(it.get("bidNtceOrd") or "00").zfill(2)
        est_price = safe_int_amount(it.get("presmptPrce") or it.get("asignBdgtAmt"))

        results.append(
            {
                "source": "g2b",
                "bid_no": bid_no,
                "bid_ord": bid_ord,
                "title": str(it.get("bidNtceNm") or "").strip(),
                "agency": str(it.get("ntceInsttNm") or it.get("dminsttNm") or "").strip(),
                "category": category,
                "contract_method": str(it.get("cntrctCnclsMthdNm") or "").strip(),
                "estimated_price": est_price,
                "deadline": str(it.get("bidClseDt") or "").strip(),
                "published_at": str(it.get("bidNtceDt") or "").strip(),
                "region_limit": str(it.get("prtcptPsblRgnNm") or "전국").strip(),
                "license_limit": str(it.get("lcnsLmtNm") or "").strip(),
                "is_sme_competing": "중소기업자간 경쟁" in str(it.get("bidNtceNm") or "")
                or "직접생산" in str(it.get("bidNtceNm") or ""),
                "url": str(it.get("bidNtceDtlUrl") or it.get("bidNtceUrl") or "").strip(),
                "raw_json": it,
                "attachments": [
                    {
                        "filename": str(it.get(f"ntceSpecFileNm{i}") or "").strip(),
                        "url": str(it.get(f"ntceSpecDocUrl{i}") or "").strip(),
                    }
                    for i in range(1, 6)
                    if it.get(f"ntceSpecDocUrl{i}")
                ],
            }
        )
    return results


async def enrich_g2b_bid_limits(
    client: httpx.AsyncClient,
    base_url: str,
    service_key: str,
    bid_no: str,
    bid_ord: str,
) -> dict[str, str]:
    """나라장터 면허제한(LicenseLimit02) 및 참가가능지역(PrtcptPsblRgn02) 세부 오퍼레이션을 보강 조회한다."""
    out = {"license_limit": "", "region_limit": ""}
    common_params = {
        "serviceKey": service_key,
        "pageNo": "1",
        "numOfRows": "10",
        "type": "json",
        "bidNtceNo": bid_no,
        "bidNtceOrd": bid_ord,
    }
    try:
        r_lic = await request_with_backoff(
            client, f"{base_url}/getBidPblancListInfoLicenseLimit02", common_params, source="g2b"
        )
        items = r_lic.json().get("response", {}).get("body", {}).get("items", [])
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        names = [str(it.get("lcnsLmtNm") or "").strip() for it in items or [] if it.get("lcnsLmtNm")]
        if names:
            out["license_limit"] = ", ".join(dict.fromkeys(names))
    except Exception as exc:
        logger.debug("LicenseLimit02 lookup skipped(%s): %s", bid_no, exc)

    try:
        r_rgn = await request_with_backoff(
            client, f"{base_url}/getBidPblancListInfoPrtcptPsblRgn02", common_params, source="g2b"
        )
        items = r_rgn.json().get("response", {}).get("body", {}).get("items", [])
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]
        regions = [str(it.get("prtcptPsblRgnNm") or "").strip() for it in items or [] if it.get("prtcptPsblRgnNm")]
        if regions:
            out["region_limit"] = ", ".join(dict.fromkeys(regions))
    except Exception as exc:
        logger.debug("PrtcptPsblRgn02 lookup skipped(%s): %s", bid_no, exc)

    return out


async def fetch_g2b_bids(
    days_back: int = 7,
    max_pages: int | None = None,
    num_of_rows: int = 100,
    keyword: str = "",
) -> tuple[list[dict[str, Any]], int, str]:
    """
    최근 조회 기간을 최대 7일 윈도우로 분할하고, 윈도우·분류별로 totalCount까지 페이지를 넘기며
    용역·물품 목록 및 세부 면허·지역 정보를 함께 수집한다.

    예전에는 윈도우·분류당 첫 50건만 가져와 나머지를 조용히 누락했다. 이제는 페이지 상한(g2b_max_pages)
    또는 일일 호출 한도 때문에 다 못 가져온 경우 결과 메시지에 생략 건수를 명시한다.
    """
    cfg = load_config()["public_api"]
    if max_pages is None:
        max_pages = max(1, int(cfg.get("g2b_max_pages") or 10))
    key = normalize_service_key(cfg.get("g2b_service_key", ""))
    if not key:
        return [], 0, "나라장터 API 키 미설정"

    base_url = str(cfg.get("g2b_base_url") or "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02").rstrip("/")
    now = datetime.now()
    bounded_days = min(max(int(days_back), 1), 30)
    start_dt = now - timedelta(days=bounded_days)

    windows = chunk_date_range(start_dt, now, max_days=7)
    collected: dict[tuple[str, str], dict[str, Any]] = {}
    last_api_error: tuple[str, str] | None = None
    reported_total = 0  # 윈도우·분류별 totalCount 합(중복 공고 포함 가능)
    skipped_by_page_cap = 0
    stopped_by_quota = False

    async with httpx.AsyncClient(timeout=20.0) as client:
        for cat, op in [("용역", CATEGORY_OPERATIONS["용역"]), ("물품", CATEGORY_OPERATIONS["물품"])]:
            for w_start, w_end in windows:
                if stopped_by_quota:
                    break
                fetched_here = 0
                total_here = 0
                for page_no in range(1, max_pages + 1):
                    if remaining_quota("g2b") <= _QUOTA_RESERVE:
                        stopped_by_quota = True
                        break
                    params: dict[str, Any] = {
                        "serviceKey": key,
                        "pageNo": str(page_no),
                        "numOfRows": str(num_of_rows),
                        "type": "json",
                        "inqryDiv": "1",
                        "inqryBgnDt": w_start.strftime("%Y%m%d%H%M"),
                        "inqryEndDt": w_end.strftime("%Y%m%d%H%M"),
                    }
                    if keyword.strip():
                        params["bidNtceNm"] = keyword.strip()
                    try:
                        resp = await request_with_backoff(client, f"{base_url}/{op}", params, source="g2b")
                    except QuotaExceededError:
                        if not collected:
                            raise
                        stopped_by_quota = True  # 이미 받은 공고는 살리고 한도 도달을 보고
                        break
                    data = resp.json()
                    err_code, err_msg = _extract_g2b_error(data)
                    if err_code == "03":
                        break
                    if err_code:
                        logger.warning("G2B API error (%s/%s): code=%s msg=%s", cat, op, err_code, err_msg)
                        last_api_error = (err_code, err_msg)
                        break
                    body = data.get("response", {}).get("body", {})
                    try:
                        total_count = int(body.get("totalCount") or 0)
                    except (TypeError, ValueError):
                        total_count = 0
                    total_here = max(total_here, total_count)
                    parsed_items = _parse_g2b_items(data, cat)
                    fetched_here += len(parsed_items)
                    for item in parsed_items:
                        collected[(item["bid_no"], item["bid_ord"])] = item
                    if not parsed_items or page_no * num_of_rows >= total_count:
                        break
                reported_total += total_here
                if total_here > fetched_here and not stopped_by_quota:
                    skipped_by_page_cap += total_here - fetched_here

        if not collected and last_api_error:
            err_code, err_msg = last_api_error
            raise RuntimeError(f"나라장터 API 오류 (코드 {err_code}): {err_msg}")

        # 면허·지역 정보가 비어있는 공고 상위 일부에 대해 세부 API 보강 (속도·일일 한도 보호를 위해 최대 5건)
        missing_detail = [
            it for it in collected.values() if not it.get("license_limit") or it.get("region_limit") == "전국"
        ][:5]
        for item in missing_detail:
            extra = await enrich_g2b_bid_limits(client, base_url, key, item["bid_no"], item["bid_ord"])
            if extra["license_limit"]:
                item["license_limit"] = extra["license_limit"]
            if extra["region_limit"]:
                item["region_limit"] = extra["region_limit"]

    msg = f"나라장터 {len(windows)}개 7일 윈도우({len(collected)}건) 수집 완료"
    if stopped_by_quota:
        msg += f" — 일일 호출 한도 보호로 일부만 수집(조회된 총 {reported_total:,}건 중 {len(collected):,}건)"
    elif skipped_by_page_cap > 0:
        msg += (
            f" — 페이지 상한({max_pages}쪽×{num_of_rows}건)으로 {skipped_by_page_cap:,}건 생략."
            " 키워드로 좁히거나 설정의 g2b_max_pages를 늘리세요"
        )
    return list(collected.values()), len(windows), msg


async def download_g2b_attachments(bid_no: str, bid_ord: str, existing_attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[제안 진행(승인)] 이후에만 온디맨드로 나라장터 첨부파일 API를 호출하고 파일을 다운로드한다."""
    cfg = load_config()["public_api"]
    key = normalize_service_key(cfg.get("g2b_service_key", ""))
    base_url = str(cfg.get("g2b_base_url") or "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02").rstrip("/")
    target_dir = get_downloads_dir() / safe_path_component(bid_no, fallback="g2b_notice")
    target_dir.mkdir(parents=True, exist_ok=True)

    attachments = list(existing_attachments)
    if key and not attachments:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                params = {
                    "serviceKey": key,
                    "pageNo": "1",
                    "numOfRows": "20",
                    "type": "json",
                    "bidNtceNo": bid_no,
                    "bidNtceOrd": bid_ord,
                }
                resp = await request_with_backoff(
                    client, f"{base_url}/getBidPblancListInfoAtchFileInfo02", params, source="g2b"
                )
                body = resp.json().get("response", {}).get("body", {})
                items = body.get("items", [])
                if isinstance(items, dict):
                    items = items.get("item", [])
                for it in items or []:
                    if it.get("atchFileUrl"):
                        attachments.append(
                            {
                                "filename": str(it.get("atchFileNm") or f"{bid_no}_rfp.hwpx"),
                                "url": str(it.get("atchFileUrl")),
                            }
                        )
        except Exception as exc:
            logger.warning("나라장터 첨부 조회 실패(%s): %s", bid_no, exc)

    downloaded: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for att in attachments:
            url = str(att.get("url") or "")
            fname = safe_path_component(att.get("filename"), fallback="attachment.bin")
            local_path = target_dir / fname
            if url.startswith("http"):
                try:
                    size = await download_to_file(client, url, local_path)
                    downloaded.append(
                        {
                            "filename": fname,
                            "url": url,
                            "local_path": str(local_path),
                            "size_bytes": size,
                            "file_type": local_path.suffix.lower().lstrip("."),
                        }
                    )
                    continue
                except Exception as exc:
                    logger.warning("첨부 다운로드 실패(%s): %s", url, exc)
            downloaded.append(
                {
                    "filename": fname,
                    "url": url,
                    "local_path": str(local_path) if local_path.exists() else "",
                    "size_bytes": local_path.stat().st_size if local_path.exists() else 0,
                    "file_type": local_path.suffix.lower().lstrip("."),
                }
            )
    return downloaded


async def test_g2b_connection() -> dict[str, Any]:
    cfg = load_config()["public_api"]
    key = normalize_service_key(cfg.get("g2b_service_key", ""))
    if not key:
        return {"ok": False, "message": "나라장터 ServiceKey가 비어 있습니다."}
    base_url = str(cfg.get("g2b_base_url") or "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02").rstrip("/")
    now = datetime.now()
    bgn = (now - timedelta(days=2)).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d2359")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await request_with_backoff(
                client,
                f"{base_url}/{CATEGORY_OPERATIONS['용역']}",
                {
                    "serviceKey": key,
                    "pageNo": "1",
                    "numOfRows": "3",
                    "type": "json",
                    "inqryDiv": "1",
                    "inqryBgnDt": bgn,
                    "inqryEndDt": end,
                },
                source="g2b",
            )
            data = resp.json()
            err_code, err_msg = _extract_g2b_error(data)
            if err_code and err_code != "03":
                return {"ok": False, "message": f"나라장터 API 오류 (코드 {err_code}): {err_msg}"}
            total = data.get("response", {}).get("body", {}).get("totalCount", 0)
            return {"ok": True, "message": f"나라장터 API 통신 정상 (최근 2일 용역 {total}건)", "details": {"totalCount": total}}
    except Exception as exc:
        return {"ok": False, "message": f"나라장터 API 통신 실패: {exc}"}
