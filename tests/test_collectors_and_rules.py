"""나라장터 7일 윈도우 분할, 키 정규화 및 사회적기업 수의계약 특례·직접생산 규칙 필터 검증."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.collectors.base import chunk_date_range, normalize_service_key
from app.collectors.g2b import _extract_g2b_error
from app.collectors.rule_filter import apply_social_enterprise_rules
from app.config import load_profile


def test_g2b_7day_chunking_covers_full_range() -> None:
    end_dt = datetime(2026, 11, 22, 12, 0)
    start_dt = end_dt - timedelta(days=18)
    windows = chunk_date_range(start_dt, end_dt, max_days=7)

    assert len(windows) == 3
    for w_start, w_end in windows:
        assert (w_end - w_start) <= timedelta(days=7)
    assert windows[0][0] == start_dt
    assert windows[-1][1] == end_dt


def test_service_key_normalization() -> None:
    encoded = "abc%2Bdef%2Fghi%3D%3D"
    assert normalize_service_key(encoded) == "abc+def/ghi=="
    assert normalize_service_key("plain+key==") == "plain+key=="


def test_social_enterprise_privilege_and_direct_production_rules(isolated_data_dir) -> None:
    profile = load_profile()

    # 1. 5,000만 원 이하 -> 1인 견적 수의계약 특례
    bid_50m = {
        "source": "g2b",
        "title": "어르신 디지털 돌봄 매니저 용역",
        "category": "용역",
        "estimated_price": 48_000_000,
        "region_limit": "서울특별시",
        "is_sme_competing": False,
    }
    res_50m = apply_social_enterprise_rules(bid_50m, profile)
    assert res_50m["privilege_tier"] == "private_1_quote"
    assert res_50m["rule_passed"] is True

    # 2. 1억 원 이하 -> 2인 이상 견적 수의계약 특례
    bid_100m = {
        "source": "g2b",
        "title": "사회적경제 포털 고도화 용역",
        "category": "용역",
        "estimated_price": 88_000_000,
        "region_limit": "전국",
        "is_sme_competing": False,
    }
    res_100m = apply_social_enterprise_rules(bid_100m, profile)
    assert res_100m["privilege_tier"] == "private_2_quotes"
    assert res_100m["rule_passed"] is True

    # 3. 중기간 경쟁제품 1,000만 원+ & 미보유 품목 -> 직접생산확인 경고
    bid_sme_warn = {
        "source": "g2b",
        "title": "공공청사 LED 조명기구 교체 납품",
        "category": "물품",
        "estimated_price": 32_000_000,
        "region_limit": "서울특별시",
        "is_sme_competing": True,
    }
    res_warn = apply_social_enterprise_rules(bid_sme_warn, profile)
    assert res_warn["privilege_tier"] == "private_1_quote"
    assert any("직접생산확인" in w for w in res_warn["rule_warnings"])

    # 4. 지역 불일치 -> 1차 규칙 필터 제외(rule_passed=False)
    bid_region_mismatch = {
        "source": "g2b",
        "title": "부산광역시 돌봄 용역",
        "category": "용역",
        "estimated_price": 35_000_000,
        "region_limit": "부산광역시",
        "is_sme_competing": False,
    }
    res_reg = apply_social_enterprise_rules(bid_region_mismatch, profile)
    assert res_reg["rule_passed"] is False


def test_batch_bid_scoring(isolated_data_dir) -> None:
    import asyncio
    from app.ai.scoring import score_bids_batch
    from app.collectors.demo_seed import build_demo_bids

    profile = load_profile()
    enriched = [apply_social_enterprise_rules(b, profile) for b in build_demo_bids()[:5]]
    scores = asyncio.run(score_bids_batch(enriched, profile, batch_size=3))
    assert len(scores) == 5
    for val in scores.values():
        assert 15 <= val["score"] <= 98
        assert val["summary"]


def test_extract_g2b_error_envelopes() -> None:
    assert _extract_g2b_error({"response": {"header": {"resultCode": "00", "resultMsg": "정상"}}}) == ("", "")
    assert _extract_g2b_error({"response": {"header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"}}}) == (
        "03",
        "NODATA_ERROR",
    )
    assert _extract_g2b_error(
        {
            "nkoneps.com.response.ResponseError": {
                "header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED ERROR."}
            }
        }
    ) == ("30", "SERVICE KEY IS NOT REGISTERED ERROR.")


def test_score_bids_batch_caps_llm_calls(isolated_data_dir, monkeypatch) -> None:
    import asyncio
    from app.ai import scoring
    from app.collectors.demo_seed import build_demo_bids

    profile = load_profile()
    enriched = [apply_social_enterprise_rules(b, profile) for b in build_demo_bids()[:5]]
    for idx, item in enumerate(enriched):
        item["rule_passed"] = True
        item["bid_no"] = f"TEST-CAP-{idx}"

    called_bid_nos: list[str] = []

    async def fake_generate_json(sys_prompt, user_prompt, feature="default", tier="fast", simulation_payload=None):
        import json as _json

        payload = _json.loads(user_prompt)
        batch_nos = [b["bid_no"] for b in payload["bids"]]
        called_bid_nos.extend(batch_nos)
        return {
            "scores": [
                {"bid_no": bno, "score": 91, "summary": f"LLM 평가 {bno}", "risks": []}
                for bno in batch_nos
            ]
        }

    monkeypatch.setattr(scoring, "is_simulation_active", lambda: False)
    monkeypatch.setattr(scoring, "generate_json", fake_generate_json)

    scores = asyncio.run(scoring.score_bids_batch(enriched, profile, batch_size=2, max_llm_bids=2))
    assert len(scores) == 5
    assert len(called_bid_nos) == 2
    llm_count = sum(1 for v in scores.values() if v["summary"].startswith("LLM 평가 "))
    assert llm_count == 2
    assert all(v["summary"] for v in scores.values())
