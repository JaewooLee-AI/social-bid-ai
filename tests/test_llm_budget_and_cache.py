"""P1 회귀 테스트: 월 LLM 예산 상한(402·SSE 중단), 대략 비용 추정, 임베딩·Vision OCR 캐시."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai import job_guard
from app.ai.llm_budget import BudgetExceededError, ensure_budget_available, estimate_cost_krw
from app.config import load_config, save_config
from app.db import get_conn
from app.server import create_app

TOKEN = "budget-test-token"
HEADERS = {"X-PyWebView-Token": TOKEN}


@pytest.fixture(autouse=True)
def _reset_guard() -> None:
    job_guard.reset_all()
    yield
    job_guard.reset_all()


def _client() -> TestClient:
    return TestClient(create_app(session_token=TOKEN, sync_profile_rag=False))


def _set_llm(**kwargs: Any) -> None:
    cfg = load_config()
    cfg["llm"].update(kwargs)
    save_config(cfg)


def _live(budget: float = 0.0) -> None:
    _set_llm(api_key="test-key", simulation_mode=False, provider="gemini", monthly_budget_krw=budget)


def _insert_live_cost(krw: float, provider: str = "gemini") -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO token_usage (provider, model, feature, input_tokens, output_tokens, thinking_tokens,
                                     cost_usd, cost_krw, pricing_missing, created_at)
            VALUES (?, 'gemini-3.8-flash', 'test', 0, 0, 0, 0.0, ?, 0, ?)
            """,
            (provider, krw, now),
        )


# ---------------------------------------------------------------- 예산


def test_budget_unlimited_simulation_and_threshold(isolated_data_dir: Path) -> None:
    _insert_live_cost(5_000)
    _live(budget=0)  # 0 = 무제한
    ensure_budget_available()

    _live(budget=10_000)
    ensure_budget_available()  # 5,000 < 10,000

    _insert_live_cost(6_000)
    with pytest.raises(BudgetExceededError):
        ensure_budget_available()

    # 시뮬레이션 모드는 과금이 없으므로 예산과 무관
    _set_llm(simulation_mode=True)
    ensure_budget_available()


def test_simulation_usage_does_not_count_toward_budget(isolated_data_dir: Path) -> None:
    _insert_live_cost(50_000, provider="simulation")
    _live(budget=1_000)
    ensure_budget_available()


def test_budget_exceeded_blocks_expensive_routes_with_402(isolated_data_dir: Path) -> None:
    client = _client()
    bp = client.post("/api/bizplan", json={"title": "예산 테스트"}, headers=HEADERS).json()
    prop = client.post("/api/proposal", json={"title": "예산 제안서", "scenario": "B"}, headers=HEADERS).json()

    _insert_live_cost(2_000)
    _live(budget=1_000)

    status = client.get("/api/settings/budget", headers=HEADERS).json()
    assert status["exceeded"] is True
    assert status["remaining_krw"] == 0

    for path, body in (
        ("/api/bizplan/generate", {"bizplan_id": bp["id"]}),
        ("/api/bizplan/remediate", {"bizplan_id": bp["id"], "flag_code": "subsidy_dependency", "section_key": "scaleup"}),
        ("/api/proposal/generate", {"proposal_id": prop["id"]}),
        ("/api/rag/reindex", {}),
    ):
        r = client.post(path, json=body, headers=HEADERS)
        assert r.status_code == 402, path
        assert "예산" in r.json()["detail"]

    # 402로 거절된 뒤 작업 잠금이 남아있지 않아야 한다
    assert not job_guard.is_running(f"bizplan:{bp['id']}")
    assert not job_guard.is_running(f"proposal:{prop['id']}")

    # 프로필 저장은 예산 초과(RAG 재임베딩 실패)와 무관하게 성공해야 한다
    profile = client.get("/api/settings/profile", headers=HEADERS).json()
    profile["summary"] = "예산 초과 상태에서 수정한 요약"
    r_prof = client.put("/api/settings/profile", json=profile, headers=HEADERS)
    assert r_prof.status_code == 200
    assert r_prof.json()["summary"] == "예산 초과 상태에서 수정한 요약"


def test_budget_reached_mid_stream_emits_sse_error(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    import app.ai.llm_client as llm_client

    client = _client()
    bp = client.post("/api/bizplan", json={"title": "중간 예산 소진"}, headers=HEADERS).json()
    _live(budget=1_000_000)

    state = {"exhausted": False}

    class _FakeModels:
        def generate_content_stream(self, model: str, contents: Any, config: Any) -> Any:
            def gen() -> Any:
                yield SimpleNamespace(text="첫 섹션 본문 ", usage_metadata=None)
                state["exhausted"] = True  # 첫 섹션 생성으로 예산 소진된 상황 가정

            return gen()

    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=_FakeModels()))

    real_ensure = llm_client.ensure_budget_available

    def fake_ensure(cfg: Any = None) -> None:
        if state["exhausted"]:
            raise BudgetExceededError("이번 달 LLM API 예산을 모두 사용했습니다(테스트).")
        real_ensure(cfg)

    monkeypatch.setattr(llm_client, "ensure_budget_available", fake_ensure)

    r = client.post("/api/bizplan/generate", json={"bizplan_id": bp["id"]}, headers=HEADERS)
    assert r.status_code == 200
    assert "event: error" in r.text
    assert "budget_exceeded" in r.text
    assert "event: done" not in r.text
    assert not job_guard.is_running(f"bizplan:{bp['id']}")

    saved = client.get(f"/api/bizplan/{bp['id']}", headers=HEADERS).json()
    assert saved["sections_content"].get("problem", "").startswith("첫 섹션 본문")
    assert "solution" not in saved["sections_content"]


def test_estimate_scales_with_calls_and_tier(isolated_data_dir: Path) -> None:
    one, model = estimate_cost_krw("section", 1, "strong")
    four, _ = estimate_cost_krw("section", 4, "strong")
    fast_four, fast_model = estimate_cost_krw("section", 4, "fast")
    assert one > 0 and model == load_config()["llm"]["strong_model"]
    assert four == pytest.approx(one * 4)
    assert fast_model == load_config()["llm"]["fast_model"]
    assert fast_four < four  # 기본 단가표상 fast 모델이 더 저렴
    emb, emb_model = estimate_cost_krw("embedding", 100)
    assert emb > 0 and "embedding" in emb_model


# ---------------------------------------------------------------- 캐시


class _FakeEmbedGenai:
    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.embedded: list[str] = []
        self.ocr_calls = 0
        self.ocr_fail = False

    def __call__(self, api_key: str) -> Any:
        return SimpleNamespace(models=self)

    def embed_content(self, model: str, contents: list[str], config: Any) -> Any:
        self.embedded.extend(contents)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[float(len(t) % 7) + 0.5] * self.dim) for t in contents]
        )

    def generate_content(self, model: str, contents: Any, config: Any) -> Any:
        self.ocr_calls += 1
        if self.ocr_fail:
            raise RuntimeError("vision 500")
        return SimpleNamespace(text="| 항목 | 배점 |\n| 사업계획 | 30 |", usage_metadata=None)


def _embedding_usage_rows() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM token_usage WHERE feature = 'embedding'").fetchone()["c"])


def test_embedding_cache_skips_repeated_texts(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    from app.ai.llm_client import embed_texts

    _live()
    dim = int(load_config()["llm"]["embedding_dim"])
    fake = _FakeEmbedGenai(dim)
    monkeypatch.setattr(genai, "Client", fake)

    v1 = embed_texts(["가나다", "라마바", "가나다"])
    assert fake.embedded == ["가나다", "라마바"]  # 요청 내 중복도 1회만
    assert v1[0] == v1[2]
    assert _embedding_usage_rows() == 1

    v2 = embed_texts(["라마바", "가나다", "사아자"])
    assert fake.embedded == ["가나다", "라마바", "사아자"]  # 새 텍스트만 추가 호출
    assert v2[0] == v1[1] and v2[1] == v1[0]
    assert _embedding_usage_rows() == 2

    embed_texts(["가나다", "사아자"])  # 전부 캐시 적중
    assert len(fake.embedded) == 3
    assert _embedding_usage_rows() == 2

    # 예산 소진 상태라도 캐시 적중분만 필요하면 호출 없이 성공하고, 미적중이 있으면 차단
    _insert_live_cost(10_000)
    _set_llm(monthly_budget_krw=100)
    assert len(embed_texts(["가나다"])) == 1
    with pytest.raises(BudgetExceededError):
        embed_texts(["새로운 텍스트"])


def test_vision_ocr_cache_reuses_success_only(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    from app.ai.llm_client import vision_ocr_page

    _live()
    fake = _FakeEmbedGenai(8)
    monkeypatch.setattr(genai, "Client", fake)

    page_png = b"\x89PNG-fake-page-1"
    first = vision_ocr_page(page_png, 1)
    second = vision_ocr_page(page_png, 1)
    assert first == second and "배점" in first
    assert fake.ocr_calls == 1

    # 오류 결과는 캐시하지 않아 다음 시도에서 다시 판독한다
    fake.ocr_fail = True
    other_png = b"\x89PNG-fake-page-2"
    err = vision_ocr_page(other_png, 2)
    assert "오류" in err
    fake.ocr_fail = False
    ok = vision_ocr_page(other_png, 2)
    assert "배점" in ok
    assert fake.ocr_calls == 3

    # 예산 소진 시 캐시 미적중 페이지는 API 호출 없이 생략 안내
    _insert_live_cost(10_000)
    _set_llm(monthly_budget_krw=100)
    skipped = vision_ocr_page(b"\x89PNG-fake-page-3", 3)
    assert "예산 초과" in skipped
    assert fake.ocr_calls == 3
    assert vision_ocr_page(page_png, 1) == first  # 캐시 적중분은 계속 사용 가능


def test_cache_stats_and_clear_endpoints(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    from app.ai.llm_client import embed_texts, vision_ocr_page

    _live()
    fake = _FakeEmbedGenai(int(load_config()["llm"]["embedding_dim"]))
    monkeypatch.setattr(genai, "Client", fake)
    embed_texts(["캐시1", "캐시2"])
    vision_ocr_page(b"png", 1)

    client = _client()
    stats = client.get("/api/settings/llm-cache", headers=HEADERS).json()
    assert stats == {"embedding_entries": 2, "ocr_entries": 1}
    removed = client.delete("/api/settings/llm-cache", headers=HEADERS).json()
    assert removed == {"embedding_entries": 2, "ocr_entries": 1}
    assert client.get("/api/settings/llm-cache", headers=HEADERS).json() == {"embedding_entries": 0, "ocr_entries": 0}
