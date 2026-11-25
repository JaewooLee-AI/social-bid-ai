"""2026-11-25 재검토 '높음' 항목 회귀 테스트.

H1 실제 수집 실패 시 샘플 공고 혼입 금지 + is_demo 표시
H2 HWPX 본문 문단 슬롯 인덱스 정렬(빈 문단 포함)
H3 나라장터 페이지네이션 및 생략 건수 보고
H4 단가 미등록 모델도 월 예산 상한에 집계
H5 LLM 점수 형식 오류에도 공고 수집 계속
M1 공고 수집 중복 실행 409
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai import job_guard
from app.config import get_db_path, load_config, save_config
from app.server import create_app

TOKEN = "high-findings-token"
H = {"X-PyWebView-Token": TOKEN}


@pytest.fixture(autouse=True)
def _reset_guard() -> None:
    job_guard.reset_all()
    yield
    job_guard.reset_all()


def _client() -> TestClient:
    return TestClient(create_app(session_token=TOKEN, sync_profile_rag=False))


def _set_public_key(field: str, value: str) -> None:
    cfg = load_config()
    cfg["public_api"][field] = value
    save_config(cfg)


# ------------------------------------------------------------------ H1


def test_real_fetch_failure_never_substitutes_demo(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes_bids as rb

    _set_public_key("g2b_service_key", "REAL-KEY")

    async def boom(**_: Any) -> Any:
        raise RuntimeError("나라장터 일시 장애(HTTP 503)")

    monkeypatch.setattr(rb, "fetch_g2b_bids", boom)
    client = _client()
    res = client.post(
        "/api/bids/sync", json={"sources": ["g2b"], "use_demo_if_no_key": True}, headers=H
    ).json()
    report = res["reports"][0]
    assert report["failed"] is True and report["used_demo"] is False
    assert "HTTP 503" in report["message"] and "대체하지 않았습니다" in report["message"]
    assert client.get("/api/bids?source=g2b", headers=H).json() == []


def test_demo_only_without_key_and_flagged(isolated_data_dir: Path) -> None:
    client = _client()
    res = client.post("/api/bids/sync", json={"sources": ["g2b"], "use_demo_if_no_key": True}, headers=H).json()
    assert res["reports"][0]["used_demo"] is True and "실제 공고 아님" in res["reports"][0]["message"]
    bids = client.get("/api/bids?source=g2b", headers=H).json()
    assert bids and all(b["is_demo"] for b in bids)


def test_approving_real_bid_without_attachments_does_not_fabricate_rfp(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes_bids as rb

    _set_public_key("seis_service_key", "REAL-KEY")

    async def real_seis() -> Any:
        return (
            [
                {
                    "source": "seis", "bid_no": "PBLN_REAL_001", "bid_ord": "00",
                    "title": "2026 사회적기업 판로지원 실제 공고", "agency": "한국사회적기업진흥원",
                    "category": "지원사업", "contract_method": "공모", "estimated_price": 0,
                    "deadline": "", "published_at": "", "region_limit": "전국", "license_limit": "",
                    "is_sme_competing": False, "url": "", "raw_json": {}, "attachments": [],
                }
            ],
            "ok",
        )

    monkeypatch.setattr(rb, "fetch_seis_notices", real_seis)
    client = _client()
    client.post("/api/bids/sync", json={"sources": ["seis"]}, headers=H)
    bid = client.get("/api/bids?source=seis", headers=H).json()[0]
    assert bid["is_demo"] is False

    appr = client.post(f"/api/bids/{bid['id']}/decision", json={"status": "approved"}, headers=H).json()
    assert appr["downloaded_files"] == []  # 예전: 가짜 샘플 RFP가 생성됨
    prop = client.get(f"/api/proposal/{appr['created_proposal_id']}", headers=H).json()
    assert prop["source_file_path"] == ""


def test_is_demo_backfilled_for_existing_databases(isolated_data_dir: Path) -> None:
    from app.db import init_db

    con = sqlite3.connect(get_db_path())
    con.execute("ALTER TABLE bids DROP COLUMN is_demo")  # 구버전 스키마 재현
    for bid_no, url in (("DEMO1", "demo://rfp"), ("REAL1", "https://www.g2b.go.kr/file")):
        con.execute(
            "INSERT INTO bids (source, bid_no, title, attachments_json, created_at, updated_at) VALUES ('g2b', ?, 't', ?, 'n', 'n')",
            (bid_no, f'[{{"filename": "a.hwpx", "url": "{url}"}}]'),
        )
    con.commit()
    con.close()

    init_db()
    con = sqlite3.connect(get_db_path())
    flags = dict(con.execute("SELECT bid_no, is_demo FROM bids").fetchall())
    con.close()
    assert flags == {"DEMO1": 1, "REAL1": 0}


# ------------------------------------------------------------------ H2


def test_body_paragraph_patch_targets_the_parsed_slot(tmp_path: Path) -> None:
    import hwpx

    from app.documents.hwpx_engine import parse_hwpx_or_hwp, patch_hwpx

    doc = hwpx.HwpxDocument.new()
    for t in ["사업계획서 지정양식", "", "1. 사업 개요 (여기에 작성)", "2. 아래 서명란은 수정하지 마십시오"]:
        doc.add_paragraph(t)
    doc.save(str(tmp_path / "form.hwpx"), format="hwpx")

    slot = [s for s in parse_hwpx_or_hwp(tmp_path / "form.hwpx").slots if s.where == "body"][0]
    patch_hwpx(tmp_path / "form.hwpx", {slot.slot_id: "AI 본문"}, tmp_path / "out.hwpx")
    texts = [p.paragraph.text for p in hwpx.HwpxDocument.open(str(tmp_path / "out.hwpx")).text.iter_paragraphs()]
    assert texts == ["사업계획서 지정양식", "", "AI 본문", "2. 아래 서명란은 수정하지 마십시오"]


# ------------------------------------------------------------------ H3


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _fake_g2b(total_per_category: int) -> Any:
    async def fake_request(client: Any, url: str, params: dict[str, Any], source: str, max_retries: int = 3) -> Any:
        if "LicenseLimit" in url or "PrtcptPsblRgn" in url:
            return _FakeResp({"response": {"body": {"items": []}}})
        cat = "S" if "Servc" in url else "T"
        page, rows = int(params["pageNo"]), int(params["numOfRows"])
        start = (page - 1) * rows
        n = max(0, min(rows, total_per_category - start))
        items = [
            {"bidNtceNo": f"{cat}{params['inqryBgnDt']}{start + i:04d}", "bidNtceOrd": "00", "bidNtceNm": "공고"}
            for i in range(n)
        ]
        return _FakeResp({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": total_per_category, "items": items}}})

    return fake_request


def test_g2b_paginates_past_first_page(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import app.collectors.g2b as g2b

    _set_public_key("g2b_service_key", "K")
    monkeypatch.setattr(g2b, "request_with_backoff", _fake_g2b(250))
    items, windows, msg = asyncio.run(g2b.fetch_g2b_bids(days_back=6))
    assert windows == 1
    assert len(items) == 500  # 용역 250 + 물품 250 (예전: 각 50건만)
    assert "생략" not in msg


def test_g2b_reports_truncation_by_page_cap_and_quota(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    import app.collectors.g2b as g2b

    _set_public_key("g2b_service_key", "K")
    monkeypatch.setattr(g2b, "request_with_backoff", _fake_g2b(250))
    items, _, msg = asyncio.run(g2b.fetch_g2b_bids(days_back=6, max_pages=2))
    assert len(items) == 400 and "100건 생략" in msg

    monkeypatch.setattr(g2b, "remaining_quota", lambda source: 5)
    items, _, msg = asyncio.run(g2b.fetch_g2b_bids(days_back=6))
    assert items == [] and "일일 호출 한도" in msg


# ------------------------------------------------------------------ H4


def test_unpriced_live_model_counts_toward_budget(isolated_data_dir: Path) -> None:
    from app.ai.llm_budget import BudgetExceededError, ensure_budget_available, get_month_live_cost_krw
    from app.ai.llm_client import record_token_usage

    cfg = load_config()
    cfg["llm"].update(api_key="k", simulation_mode=False, monthly_budget_krw=1000)
    save_config(cfg)
    record_token_usage("gemini", "gemini-9-ultra-unlisted", "test", 200_000, 50_000, 30_000)
    assert get_month_live_cost_krw() > 1000  # 예전: 0원
    with pytest.raises(BudgetExceededError):
        ensure_budget_available()


# ------------------------------------------------------------------ H5


def test_malformed_llm_scores_do_not_break_sync(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai.scoring as scoring

    monkeypatch.setattr(scoring, "is_simulation_active", lambda *a, **k: False)
    weird = iter(["85점", "85.5", None, "n/a", {"x": 1}, ["a"], 140, -5, True, "70"])

    async def fake_generate_json(sys_prompt: str, user_prompt: str, feature: str, tier: str, simulation_payload: Any) -> Any:
        scores = [
            {"bid_no": s["bid_no"], "score": next(weird, "90"), "summary": None, "risks": "단가 낮음"}
            for s in simulation_payload["scores"]
        ]
        return {"scores": scores}

    monkeypatch.setattr(scoring, "generate_json", fake_generate_json)
    client = _client()
    r = client.post("/api/bids/seed-demo", json={}, headers=H)
    assert r.status_code == 200  # 예전: ValueError로 500
    bids = client.get("/api/bids", headers=H).json()
    assert bids and all(0 <= (b["ai_score"] or 0) <= 100 for b in bids)
    assert any(b["ai_risks"] == ["단가 낮음"] for b in bids)  # 문자열이 글자 단위로 쪼개지지 않음


# ------------------------------------------------------------------ M1


def test_bid_sync_rejects_concurrent_runs(isolated_data_dir: Path) -> None:
    client = _client()
    held = job_guard.try_acquire("bids:sync")
    assert client.post("/api/bids/sync", json={}, headers=H).status_code == 409
    assert client.post("/api/bids/seed-demo", json={}, headers=H).status_code == 409
    job_guard.release("bids:sync", held)
    assert client.post("/api/bids/seed-demo", json={}, headers=H).status_code == 200
    assert not job_guard.is_running("bids:sync")
