"""모델 카탈로그 공식 사이트 동기화, 사이트 구조 변경 오류 처리, Gemini 3.x/4 파라미터 호환성 및 토큰/비용 집계 테스트."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.ai.llm_client import (
    _build_gemini_config,
    get_token_usage_report,
    recalculate_token_usage_costs,
    record_token_usage,
)
from app.ai.model_catalog import (
    SiteStructureChangedError,
    load_model_catalog,
    parse_deprecations_document,
    parse_pricing_document,
    sync_model_catalog_from_official,
)
from app.config import get_config_path, get_model_catalog_path, load_config, save_config
from app.db import get_conn
from app.server import create_app


_SAMPLE_PRICING_MD = """
# Gemini Developer API Pricing

## Gemini 3.5 Flash-Lite
Model code: `gemini-3.5-flash-lite`

| Feature | Free Tier | Paid Tier (per 1M tokens in USD) |
| :--- | :--- | :--- |
| Input price | Free | $0.30 |
| Output price (including thinking tokens) | Free | $2.50 |
| Context caching | Free | $0.03 |

## Gemini 3.8 Flash (`gemini-3.8-flash`)

| Feature | Free Tier | Paid Tier (per 1M tokens in USD) |
| :--- | :--- | :--- |
| **Input price** | Free of charge | $0.75 |
| **Output price** (including thinking tokens) | Free of charge | $3.75 |

## Gemini 4.5 Flash (`gemini-4.5-flash`)

| Feature | Free Tier | Paid Tier (per 1M tokens in USD) |
| :--- | :--- | :--- |
| Input price | Free | $0.90 |
| Output price | Free | $4.50 |

## Gemini 4 Neon (`gemini-4-neon`)

| Feature | Free Tier | Paid Tier (per 1M tokens in USD) |
| :--- | :--- | :--- |
| Input price | Not available | $3.00 |
| Output price | Not available | $15.00 |

## Gemini Embedding 001 (`gemini-embedding-001`)

| Feature | Free Tier | Paid Tier (per 1M tokens in USD) |
| :--- | :--- | :--- |
| Input price | Free | $0.15 |
"""

_SAMPLE_PRICING_HTML = """
<!doctype html>
<html>
  <head><title>Gemini API Pricing</title><script>var ignored = "$999.00";</script></head>
  <body>
    <h1>Gemini API Pricing</h1>
    <h2>Gemini 3.8 Flash (gemini-3.8-flash)</h2>
    <table>
      <tr><th>Feature</th><th>Free</th><th>Paid (per 1M tokens)</th></tr>
      <tr><td>Input price</td><td>Free</td><td>$0.80</td></tr>
      <tr><td>Output price</td><td>Free</td><td>$4.00</td></tr>
    </table>
    <h2>Gemini 4 Neon (gemini-4-neon)</h2>
    <table>
      <tr><th>Feature</th><th>Paid</th></tr>
      <tr><td>Input price</td><td>$3.00</td></tr>
      <tr><td>Output price</td><td>$15.00</td></tr>
    </table>
  </body>
</html>
"""

_SAMPLE_DEPRECATIONS_MD = """
# Deprecations

| Model | Shutdown Date | Recommended Replacement |
| :--- | :--- | :--- |
| `gemini-2.5-pro` | 2026-12-01 | `gemini-3.8-flash` |
| `gemini-2.5-flash` | 2026-12-01 | `gemini-3.5-flash-lite` |
"""


def test_parse_pricing_markdown_and_html() -> None:
    parsed_md = parse_pricing_document(_SAMPLE_PRICING_MD, source_url="https://aistudio.google.com/docs/pricing.md")
    by_id = {item["model_id"]: item for item in parsed_md}

    assert by_id["gemini-3.5-flash-lite"]["input_price"] == 0.30
    assert by_id["gemini-3.5-flash-lite"]["output_price"] == 2.50
    assert by_id["gemini-3.8-flash"]["input_price"] == 0.75
    assert by_id["gemini-3.8-flash"]["output_price"] == 3.75
    assert by_id["gemini-4.5-flash"]["input_price"] == 0.90
    assert by_id["gemini-4.5-flash"]["output_price"] == 4.50
    assert by_id["gemini-embedding-001"]["input_price"] == 0.15
    assert by_id["gemini-embedding-001"]["output_price"] == 0.0

    parsed_html = parse_pricing_document(_SAMPLE_PRICING_HTML, source_url="https://ai.google.dev/gemini-api/docs/pricing")
    html_by_id = {item["model_id"]: item for item in parsed_html}
    assert html_by_id["gemini-3.8-flash"]["input_price"] == 0.80
    assert html_by_id["gemini-3.8-flash"]["output_price"] == 4.00
    assert html_by_id["gemini-4-neon"]["input_price"] == 3.00


def test_parse_pricing_raises_on_changed_site_structure() -> None:
    changed_html = (
        "<html><body><h1>New Pricing Experience</h1>"
        "<p>Pricing tables moved to dynamic client widgets. Contact Google Cloud Sales for Gemini 3.8 Flash quotes.</p>"
        "</body></html>"
    )
    try:
        parse_pricing_document(changed_html, source_url="https://aistudio.google.com/docs/pricing.md")
        raise AssertionError("Expected SiteStructureChangedError")
    except SiteStructureChangedError as exc:
        assert exc.error_code == "SITE_STRUCTURE_CHANGED"

    login_wall = (
        "<html><body><a href='https://accounts.google.com/ServiceLogin'>Sign in</a>"
        "<p>Please verify you are human before continuing to Google AI Studio.</p></body></html>"
    )
    try:
        parse_pricing_document(login_wall, source_url="https://aistudio.google.com/docs/pricing.md")
        raise AssertionError("Expected SiteStructureChangedError on login wall")
    except SiteStructureChangedError as exc:
        assert exc.error_code == "SITE_STRUCTURE_CHANGED"


def test_sync_success_and_future_model_discovery(isolated_data_dir: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "pricing.md" in url:
            return httpx.Response(200, text=_SAMPLE_PRICING_MD)
        if "deprecations.md" in url:
            return httpx.Response(200, text=_SAMPLE_DEPRECATIONS_MD)
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash-lite",
                            "displayName": "Gemini 3.5 Flash-Lite",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/gemini-4.5-flash",
                            "displayName": "Gemini 4.5 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/gemini-embedding-001",
                            "displayName": "Gemini Embedding 001",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        return httpx.Response(404, text="not found")

    cfg = load_config()
    cfg["llm"]["api_key"] = "AIzaTestFakeKey1234"
    save_config(cfg)

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await sync_model_catalog_from_official(http_client=client)

    res = asyncio.run(_run())
    assert res.ok is True
    assert res.status == "ok"
    assert res.updated_pricing_count == 5
    assert get_model_catalog_path().exists()

    # 신규 미래 모델이 카탈로그 및 config.json 단가표에 자동 등록되고 권장 모델로 반영됨
    ids = {m.model_id: m for m in res.catalog.models}
    assert "gemini-4.5-flash" in ids
    assert ids["gemini-4.5-flash"].input_price_per_1m == 0.90
    assert ids["gemini-4.5-flash"].output_price_per_1m == 4.50
    assert ids["gemini-4.5-flash"].verified_live is True
    assert res.catalog.recommended_strong == "gemini-4.5-flash"

    saved_cfg = json.loads(get_config_path().read_text(encoding="utf-8"))
    assert saved_cfg["llm"]["pricing_per_1m"]["gemini-4.5-flash"] == {"input": 0.90, "output": 4.50}


def test_sync_site_structure_changed_preserves_existing_settings(isolated_data_dir: Path) -> None:
    cfg = load_config()
    cfg["llm"]["api_key"] = "AIzaTestFakeKey1234"
    cfg["llm"]["pricing_per_1m"]["gemini-3.8-flash"] = {"input": 0.77, "output": 3.88}
    save_config(cfg)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "pricing" in url:
            return httpx.Response(
                200,
                text="<html><body><h1>Pricing Page Redesigned</h1><p>No structured markdown tables anymore.</p></body></html>",
            )
        if "deprecations" in url:
            return httpx.Response(200, text=_SAMPLE_DEPRECATIONS_MD)
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-5-flash",
                            "displayName": "Gemini 5 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                        }
                    ]
                },
            )
        return httpx.Response(404, text="not found")

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await sync_model_catalog_from_official(http_client=client)

    res = asyncio.run(_run())
    assert res.ok is False
    assert res.error_code == "SITE_STRUCTURE_CHANGED"
    assert res.status == "partial_schema_changed"
    assert "사이트 구조가 변경" in res.message
    assert res.recovery_hint

    # 기존 사용자 단가가 절대 덮어쓰기 되지 않음
    reloaded = load_config()
    assert reloaded["llm"]["pricing_per_1m"]["gemini-3.8-flash"] == {"input": 0.77, "output": 3.88}

    # live API로 확인된 신규 모델은 단가 미수집(None) 상태로 선택 가능하게 병합됨
    by_id = {m.model_id: m for m in res.catalog.models}
    assert "gemini-5-flash" in by_id
    assert by_id["gemini-5-flash"].input_price_per_1m is None


def test_sync_network_error_preserves_existing_data(isolated_data_dir: Path) -> None:
    cfg = load_config()
    cfg["llm"]["api_key"] = ""
    cfg["llm"]["pricing_per_1m"]["gemini-3.8-flash"] = {"input": 0.75, "output": 3.75}
    save_config(cfg)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed", request=request)

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)) as client:
            return await sync_model_catalog_from_official(http_client=client)

    res = asyncio.run(_run())
    assert res.ok is False
    assert res.error_code == "NETWORK_ERROR"
    assert res.status == "network_error"
    assert load_config()["llm"]["pricing_per_1m"]["gemini-3.8-flash"] == {"input": 0.75, "output": 3.75}


def test_gemini_config_temperature_and_thinking_level() -> None:
    cfg_38_strong = _build_gemini_config("gemini-3.8-flash", "sys", tier="strong", temperature=0.35)
    assert getattr(cfg_38_strong, "temperature", None) is None
    assert cfg_38_strong.thinking_config is not None
    assert str(cfg_38_strong.thinking_config.thinking_level).upper().endswith("MEDIUM")

    cfg_38_json = _build_gemini_config("gemini-3.8-flash", "sys", tier="fast", json_mode=True, temperature=0.2)
    assert getattr(cfg_38_json, "temperature", None) is None
    # 3.8-flash는 minimal 미지원 -> LOW 사용
    assert str(cfg_38_json.thinking_config.thinking_level).upper().endswith("LOW")

    cfg_35_json = _build_gemini_config("gemini-3.5-flash-lite", "sys", tier="fast", json_mode=True, temperature=0.2)
    assert getattr(cfg_35_json, "temperature", None) is None
    # 3.5-flash-lite는 minimal 지원 -> MINIMAL 사용
    assert str(cfg_35_json.thinking_config.thinking_level).upper().endswith("MINIMAL")

    cfg_25 = _build_gemini_config("gemini-2.5-flash", "sys", tier="fast", temperature=0.35)
    assert cfg_25.temperature == 0.35
    assert getattr(cfg_25, "thinking_config", None) is None


def test_usage_tracking_thinking_tokens_and_recalculation(isolated_data_dir: Path) -> None:
    # 1. gemini-3.8-flash: thinking_tokens가 출력 단가($3.75/1M)에 합산 청구됨
    res_known = record_token_usage(
        "gemini",
        "gemini-3.8-flash",
        "proposal_section:sec_1",
        input_tokens=1_000_000,
        output_tokens=500_000,
        thinking_tokens=500_000,
    )
    assert res_known["pricing_missing"] is False
    # 1.0M * 0.75 + (0.5M + 0.5M) * 3.75 = 4.50 USD
    assert abs(res_known["cost_usd"] - 4.50) < 1e-6

    # 2. 단가 미등록 모델: 가짜 0.15/0.60으로 계산하지 않고 pricing_missing=True 기록
    res_unknown = record_token_usage(
        "gemini",
        "gemini-9-experimental",
        "bid_scoring",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        thinking_tokens=0,
    )
    assert res_unknown["pricing_missing"] is True
    assert res_unknown["cost_usd"] == 0.0

    # 3. API로 사용량 리포트 조회 -> 단가표 추가 등록 -> 재계산 실행
    token = "test-usage-token"
    app = create_app(session_token=token, sync_profile_rag=False)
    client = TestClient(app)
    headers = {"X-PyWebView-Token": token}

    r_rep = client.get("/api/settings/token-usage?scope=month", headers=headers)
    assert r_rep.status_code == 200
    rep = r_rep.json()
    assert rep["total_calls"] == 2
    assert rep["unpriced_calls"] == 1
    assert rep["thinking_tokens"] == 500_000

    # 단가표에 gemini-9-experimental 등록 후 재계산
    cfg = load_config()
    cfg["llm"]["pricing_per_1m"]["gemini-9-experimental"] = {"input": 1.00, "output": 5.00}
    save_config(cfg)

    r_recalc = client.post("/api/settings/token-usage/recalculate", json={}, headers=headers)
    assert r_recalc.status_code == 200
    recalc = r_recalc.json()
    assert recalc["ok"] is True
    assert recalc["updated_rows"] == 1
    assert recalc["still_unpriced_rows"] == 0

    rep_after = get_token_usage_report("month")
    assert rep_after.unpriced_calls == 0
    # 4.50 + (1.00 + 5.00) = 10.50 USD
    assert abs(rep_after.cost_usd - 10.50) < 1e-6


def test_settings_models_endpoints_and_deprecation_warning(isolated_data_dir: Path, monkeypatch) -> None:
    cfg = load_config()
    cfg["llm"]["fast_model"] = "gemini-2.5-flash"
    cfg["llm"]["strong_model"] = "gemini-2.5-pro"
    save_config(cfg)

    token = "test-models-token"
    app = create_app(session_token=token, sync_profile_rag=False)
    client = TestClient(app)
    headers = {"X-PyWebView-Token": token}

    r_models = client.get("/api/settings/models", headers=headers)
    assert r_models.status_code == 200
    data = r_models.json()
    assert data["recommended_fast"] == "gemini-3.5-flash-lite"
    assert data["recommended_strong"] == "gemini-3.8-flash"
    codes = {(w["slot"], w["code"]) for w in data["active_warnings"]}
    assert ("fast_model", "DEPRECATED") in codes
    assert ("strong_model", "DEPRECATED") in codes

    # /api/settings/models/sync 엔드포인트가 모의 사이트 구조 변경 오류를 안전하게 전달하는지 검증
    async def fake_sync():
        def bad_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>Changed layout without tables</body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(bad_handler)) as mock_client:
            return await sync_model_catalog_from_official(http_client=mock_client)

    monkeypatch.setattr("app.api.routes_settings.sync_model_catalog_from_official", fake_sync)
    r_sync = client.post("/api/settings/models/sync", json={}, headers=headers)
    assert r_sync.status_code == 200
    sync_data = r_sync.json()
    assert sync_data["ok"] is False
    assert sync_data["error_code"] == "SITE_STRUCTURE_CHANGED"


_SAMPLE_MARKDOC_PRICING = """
> ## AI Agent Context
> - Full documentation index: https://aistudio.google.com/llms.txt

# Gemini Developer API pricing

## Gemini 3.8 Flash  {% id="gemini-3.8-flash" %}

*[`gemini-3.8-flash`](/docs/models/gemini-3.8-flash)*

{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Input price
  * Free of charge
  * $0.75 through December 31, 2026.
  $1.50 starting January 1, 2027.

  ---

  * Output price (including thinking tokens)
  * Free of charge
  * $3.75 through December 31, 2026.
  $7.50 starting January 1, 2027.
  {% /table %}
  {% /tab %}

  {% tab title="Batch" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Input price
  * Not available
  * $0.375
  {% /table %}
  {% /tab %}
{% /tabs %}

## Gemini 3.5 Flash-Lite  {% id="gemini-3.5-flash-lite" %}

*[`gemini-3.5-flash-lite`](/docs/models/gemini-3.5-flash-lite)*

{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Input price
  * Free of charge
  * $0.30 (text / image / video / audio)

  ---

  * Output price (including thinking tokens)
  * Free of charge
  * $2.50
  {% /table %}
  {% /tab %}
{% /tabs %}

## Gemini 3.1 Pro Preview  {% id="gemini-3.1-pro-preview" %}

*[`gemini-3.1-pro-preview`](/docs/models/gemini-3.1-pro-preview) and [`gemini-3.1-pro-preview-customtools`](/docs/models/gemini-3.1-pro-preview#custom)*

{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Input price
  * Not available
  * $2.00, prompts <= 200k tokens
  $4.00, prompts > 200k tokens

  ---

  * Output price (including thinking tokens)
  * Not available
  * $12.00, prompts <= 200k tokens
  $18.00, prompts > 200k
  {% /table %}
  {% /tab %}
{% /tabs %}

## Gemini Embedding 2  {% id="gemini-embedding-2" %}

*[`gemini-embedding-2`](/docs/models/gemini-embedding-2)*

{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Text input price
  * Free of charge
  * $0.20

  ---

  * Image input price
  * Free of charge
  * $0.45 ($0.00012 per image)
  {% /table %}
  {% /tab %}
{% /tabs %}

## Gemini Embedding  {% id="gemini-embedding" %}

*[`gemini-embedding-001`](/docs/models/gemini-embedding-001)*

{% tabs %}
  {% tab title="Standard" %}
  {% table %}
  * Feature
  * Free Tier
  * Paid Tier, per 1M tokens in USD

  ---

  * Input price
  * Free of charge
  * $0.15
  {% /table %}
  {% /tab %}
{% /tabs %}
"""

_SAMPLE_MARKDOC_DEPRECATIONS = """
# Gemini deprecations

## Gemini 3 models  {% id="gemini-3-models" %}

{% table %}
  * Model
  * Release date
  * Shutdown date
  * Recommended replacement

  ---

  * `gemini-3.8-flash`
  * September 2, 2026
  * No shutdown date announced
  * 

  ---

  * `gemini-3.7-flash`
  * August 13, 2026
  * N/A
  * `gemini-3.8-flash` (`gemini-3.7-flash` is no longer available in Google AI Studio)
{% /table %}

## Gemini 2.5 Pro models  {% id="gemini-2.5-pro-models" %}

{% table %}
  * Model
  * Release date
  * Shutdown date
  * Recommended replacement

  ---

  * `gemini-2.5-pro`
  * June 17, 2025
  * December 1, 2026
  * `gemini-3.8-flash` (`gemini-2.5-pro` automatically routes to `gemini-3.8-flash`)
{% /table %}
"""


def test_parse_markdoc_pricing_and_deprecations() -> None:
    parsed = parse_pricing_document(
        _SAMPLE_MARKDOC_PRICING,
        source_url="https://aistudio.google.com/docs/pricing.md",
        today=date(2026, 11, 22),
    )
    by_id = {item["model_id"]: item for item in parsed}

    assert by_id["gemini-3.8-flash"]["input_price"] == 0.75
    assert by_id["gemini-3.8-flash"]["output_price"] == 3.75
    assert "프로모션" in by_id["gemini-3.8-flash"]["price_note"]

    # Standard 탭의 (text / image / video / audio) 셀이 skip되지 않고 정상 추출됨
    assert by_id["gemini-3.5-flash-lite"]["input_price"] == 0.30
    assert by_id["gemini-3.5-flash-lite"]["output_price"] == 2.50

    assert by_id["gemini-3.1-pro-preview"]["input_price"] == 2.00
    assert by_id["gemini-3.1-pro-preview"]["output_price"] == 12.00
    assert "≤200K" in by_id["gemini-3.1-pro-preview"]["price_note"]

    # 임베딩 2종: Text input만 취하고 Image input($0.45)으로 덮어쓰지 않음, gemini-embedding-001 매핑
    assert by_id["gemini-embedding-2"]["input_price"] == 0.20
    assert by_id["gemini-embedding-001"]["input_price"] == 0.15

    dep_map, dep_ok = parse_deprecations_document(_SAMPLE_MARKDOC_DEPRECATIONS)
    assert dep_ok is True
    assert "gemini-3.8-flash" not in dep_map
    assert dep_map["gemini-3.7-flash"] == "gemini-3.8-flash"
    assert dep_map["gemini-2.5-pro"] == "gemini-3.8-flash"


def test_markdoc_pricing_date_transition() -> None:
    parsed_2027 = parse_pricing_document(
        _SAMPLE_MARKDOC_PRICING,
        source_url="https://aistudio.google.com/docs/pricing.md",
        today=date(2027, 1, 5),
    )
    by_id = {item["model_id"]: item for item in parsed_2027}
    assert by_id["gemini-3.8-flash"]["input_price"] == 1.50
    assert by_id["gemini-3.8-flash"]["output_price"] == 7.50


def test_gemini_connection_response_shape(isolated_data_dir: Path) -> None:
    token = "test-conn-token"
    app = create_app(session_token=token, sync_profile_rag=False)
    client = TestClient(app)
    headers = {"X-PyWebView-Token": token}

    r_empty = client.post("/api/settings/test-api", json={"target": "gemini"}, headers=headers)
    assert r_empty.status_code == 200
    body = r_empty.json()
    assert body["ok"] is False
    assert "비정상" in body["message"]
    assert body["details"]["model"] == "gemini-3.5-flash-lite"
    assert body["details"]["tested_at"]

