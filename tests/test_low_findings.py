"""2026-11-25 재검토 '낮음' 항목 회귀 테스트(백엔드 부분).

L1 설정·프로필 로드 캐시(매 호출 시드·파싱 제거, 복사본 반환, 파일 변경 반영)
L3 Gemini 모델 목록 조회 시 API 키를 URL이 아닌 헤더로 전송
L4 공공 API 오류 코드 판별이 본문 데이터('>22<')에 오탐하지 않음
L5 RFP 시나리오 A 판정·제안서 쪽수 제한 반영
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

import app.config as config_mod
from app.config import get_config_path, load_config, load_profile, save_config


# ------------------------------------------------------------------ L1


def test_config_is_seeded_once_and_returned_as_copy(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    real_seed = config_mod.seed_defaults

    def counting_seed() -> None:
        calls["n"] += 1
        real_seed()

    monkeypatch.setattr(config_mod, "seed_defaults", counting_seed)
    for _ in range(50):
        load_config()
        load_profile()
    assert calls["n"] <= 1  # 예전: 호출마다 시드(100회)

    cfg = load_config()
    cfg["llm"]["api_key"] = "mutated-in-memory"
    cfg["public_api"]["daily_limits"]["g2b"] = -1
    assert load_config()["llm"]["api_key"] != "mutated-in-memory"  # 캐시 오염 없음
    assert config_mod.DEFAULT_CONFIG["public_api"]["daily_limits"].get("g2b") != -1


def test_config_cache_sees_saves_and_external_edits(isolated_data_dir: Path) -> None:
    cfg = load_config()
    cfg["llm"]["monthly_budget_krw"] = 12345
    save_config(cfg)
    assert load_config()["llm"]["monthly_budget_krw"] == 12345

    path = get_config_path()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["llm"]["monthly_budget_krw"] = 67890
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=4), encoding="utf-8")  # 다른 크기로 외부 수정
    assert load_config()["llm"]["monthly_budget_krw"] == 67890

    path.unlink()  # 실행 중 설정 파일 삭제 → 다시 시드
    assert load_config()["llm"]


# ------------------------------------------------------------------ L3


def test_gemini_model_list_sends_key_in_header_not_url() -> None:
    from app.ai.model_catalog import fetch_live_gemini_models

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("x-goog-api-key", "")
        return httpx.Response(200, json={"models": []})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_live_gemini_models("SECRET-KEY-123", client)

    asyncio.run(run())
    assert "SECRET-KEY-123" not in seen["url"]  # 예전: ?key=SECRET-KEY-123
    assert seen["header"] == "SECRET-KEY-123"


# ------------------------------------------------------------------ L4


def _run_backoff(isolated_data_dir: Path, body: str, content_type: str) -> None:
    from app.collectors.base import request_with_backoff

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await request_with_backoff(client, "https://apis.example/x", {}, source="g2b", max_retries=1)

    asyncio.run(run())


def test_data_containing_22_is_not_treated_as_quota_error(isolated_data_dir: Path) -> None:
    body = '<response><header><resultCode>00</resultCode></header><body><item><bidNtceOrd>22</bidNtceOrd></item></body></response>'
    _run_backoff(isolated_data_dir, body, "application/xml")  # 예전: '>22<' 부분 문자열로 '일일 한도 초과' 오탐


def test_real_quota_error_code_still_detected(isolated_data_dir: Path) -> None:
    body = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
        "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    )
    with pytest.raises(RuntimeError, match="코드 22"):
        _run_backoff(isolated_data_dir, body, "application/xml")


# ------------------------------------------------------------------ L5


def _doc(text: str):  # type: ignore[no-untyped-def]
    from app.schemas.docs import ParsedDocument

    return ParsedDocument(file_path="r.pdf", filename="r.pdf", file_type="pdf", full_text=text)


def test_plain_toc_word_does_not_force_scenario_a() -> None:
    from app.documents.rfp_analyzer import detect_scenario

    assert detect_scenario(_doc("목차\n1. 사업 개요\n2. 과업 내용"), []) == "B"  # 예전: '목차' 단어만으로 A
    assert detect_scenario(_doc("제안서 작성 목차는 아래를 따른다"), []) == "A"


def test_page_limit_scales_section_lengths() -> None:
    from app.documents.rfp_analyzer import DEFAULT_TOTAL_CHARS, build_outline_from_document
    from app.schemas.docs import ScoringCriterion

    scoring = [ScoringCriterion(category="기술", item="사업 이해도", max_score=50.0, criteria=""), ScoringCriterion(category="기술", item="수행 방안", max_score=50.0, criteria="")]
    default_total = sum(n.target_chars for n in build_outline_from_document(_doc("배점표"), scoring, "A"))
    assert abs(default_total - DEFAULT_TOTAL_CHARS) <= 100

    long_total = sum(
        n.target_chars for n in build_outline_from_document(_doc("제안서는 A4 용지 40쪽 이내로 작성"), scoring, "A")
    )
    short_total = sum(n.target_chars for n in build_outline_from_document(_doc("제안서는 5쪽 이내로 작성"), scoring, "A"))
    assert long_total > default_total * 2  # 예전: 쪽수 제한과 무관하게 6,500자 고정
    assert short_total < default_total
