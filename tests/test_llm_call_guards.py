"""LLM API 과다 호출 방지(P0) 회귀 테스트: 중복 실행 409, 잠금 해제, 스트림 취소 전파, 폴백 중복 방지, 보완 멱등성."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ai import job_guard
from app.ai.bizplan_gen import apply_redflag_remediation
from app.config import load_config, save_config
from app.schemas.bizplan import SocialBMCanvas
from app.server import create_app

TOKEN = "guard-test-token"
HEADERS = {"X-PyWebView-Token": TOKEN}


@pytest.fixture(autouse=True)
def _reset_guard() -> None:
    job_guard.reset_all()
    yield
    job_guard.reset_all()


def _client() -> TestClient:
    return TestClient(create_app(session_token=TOKEN, sync_profile_rag=False))


def test_job_guard_token_and_ttl() -> None:
    t1 = job_guard.try_acquire("k")
    assert t1 is not None
    assert job_guard.try_acquire("k") is None  # 중복 실행 거절

    job_guard.release("k", "stale-token")  # 다른 토큰으로는 해제 불가
    assert job_guard.is_running("k")

    job_guard.release("k", t1)
    assert not job_guard.is_running("k")
    job_guard.release("k", t1)  # 중복 해제 안전

    t2 = job_guard.try_acquire("k")
    assert t2 is not None
    # TTL 만료 시 잠김 영구화 방지
    assert job_guard.try_acquire("k", ttl_sec=0.0) is not None


def test_bizplan_generate_and_remediate_reject_concurrent(isolated_data_dir: Path) -> None:
    client = _client()
    bp = client.post("/api/bizplan", json={"title": "가드 테스트", "output_track": "psst"}, headers=HEADERS).json()
    key = f"bizplan:{bp['id']}"

    held = job_guard.try_acquire(key)
    assert held is not None
    r_gen = client.post("/api/bizplan/generate", json={"bizplan_id": bp["id"]}, headers=HEADERS)
    assert r_gen.status_code == 409
    assert "진행 중" in r_gen.json()["detail"]

    r_rem = client.post(
        "/api/bizplan/remediate",
        json={"bizplan_id": bp["id"], "flag_code": "subsidy_dependency", "section_key": "scaleup"},
        headers=HEADERS,
    )
    assert r_rem.status_code == 409
    job_guard.release(key, held)

    # 잠금 해제 후에는 정상 실행되고, 완료 시 잠금이 자동 해제된다
    r_ok = client.post("/api/bizplan/generate", json={"bizplan_id": bp["id"]}, headers=HEADERS)
    assert r_ok.status_code == 200
    assert "event: done" in r_ok.text
    assert not job_guard.is_running(key)

    r_rem_ok = client.post(
        "/api/bizplan/remediate",
        json={"bizplan_id": bp["id"], "flag_code": "subsidy_dependency", "section_key": "scaleup"},
        headers=HEADERS,
    )
    assert r_rem_ok.status_code == 200
    assert not job_guard.is_running(key)


def test_proposal_generate_and_reindex_reject_concurrent(isolated_data_dir: Path) -> None:
    client = _client()
    prop = client.post("/api/proposal", json={"title": "가드 제안서", "scenario": "B"}, headers=HEADERS).json()
    key = f"proposal:{prop['id']}"

    held = job_guard.try_acquire(key)
    r = client.post("/api/proposal/generate", json={"proposal_id": prop["id"]}, headers=HEADERS)
    assert r.status_code == 409
    job_guard.release(key, held)

    r_ok = client.post(
        "/api/proposal/generate",
        json={"proposal_id": prop["id"], "section_ids": [prop["outline"][0]["id"]]},
        headers=HEADERS,
    )
    assert r_ok.status_code == 200
    assert not job_guard.is_running(key)

    held_rag = job_guard.try_acquire("rag:reindex")
    assert client.post("/api/rag/reindex", json={}, headers=HEADERS).status_code == 409
    job_guard.release("rag:reindex", held_rag)
    assert client.post("/api/rag/reindex", json={}, headers=HEADERS).status_code == 200
    assert not job_guard.is_running("rag:reindex")


def _enable_live_mode() -> None:
    cfg = load_config()
    cfg["llm"]["api_key"] = "test-key"
    cfg["llm"]["simulation_mode"] = False
    cfg["llm"]["provider"] = "gemini"
    save_config(cfg)


class _FakeGenai:
    """google.genai.Client 대체: 느린 스트림을 흉내 내고 소비/종료 상태를 기록한다."""

    def __init__(self, state: dict[str, Any], fail_after: int | None = None) -> None:
        self.state = state
        self.fail_after = fail_after

    def __call__(self, api_key: str) -> Any:  # genai.Client(api_key=...)
        return SimpleNamespace(models=self)

    def generate_content_stream(self, model: str, contents: Any, config: Any) -> Any:
        state, fail_after = self.state, self.fail_after

        def gen() -> Any:
            try:
                for i in range(50):
                    if fail_after is not None and i == fail_after:
                        raise RuntimeError("upstream 503")
                    state["yielded"] += 1
                    yield SimpleNamespace(text=f"c{i} ", usage_metadata=None)
                    time.sleep(0.02)
            finally:
                state["closed"] = True

        return gen()


def test_stream_cancel_stops_gemini_producer(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    from app.ai.llm_client import stream_text

    _enable_live_mode()
    state: dict[str, Any] = {"yielded": 0, "closed": False}
    monkeypatch.setattr(genai, "Client", _FakeGenai(state))

    async def run() -> None:
        gen = stream_text("sys", "user", feature="test_cancel", simulation_text="SIM")
        first = await gen.__anext__()
        assert first.startswith("c0")
        await gen.aclose()  # 사용자 중지 / 연결 끊김
        deadline = time.monotonic() + 3.0
        while not state["closed"] and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

    asyncio.run(run())
    assert state["closed"] is True
    assert state["yielded"] < 50  # 끝까지 소비하지 않음


def test_stream_error_after_partial_does_not_append_simulation(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from google import genai

    from app.ai.llm_client import stream_text

    _enable_live_mode()
    state: dict[str, Any] = {"yielded": 0, "closed": False}
    monkeypatch.setattr(genai, "Client", _FakeGenai(state, fail_after=2))

    async def run() -> str:
        parts = [ch async for ch in stream_text("sys", "user", feature="test_err", simulation_text="SIMULATION_FULL")]
        return "".join(parts)

    out = asyncio.run(run())
    assert out.startswith("c0 c1 ")
    assert "SIMULATION_FULL" not in out
    assert "생성이 중단" in out


def test_remediation_appends_are_idempotent(isolated_data_dir: Path) -> None:
    canvas = SocialBMCanvas()
    sections = {"scaleup": "기존 본문", "solution": "기존 솔루션"}
    for code, key, marker in (
        ("subsidy_dependency", "scaleup", "[자동 보완] 보조금 비의존"),
        ("unsubstantiated_number", "scaleup", "[자동 보완] 단위 경제성"),
        ("no_competitor", "solution", "[자동 보완] 간접 경쟁자"),
        ("missing_evidence", "solution", "[자동 보완] 실행 증거"),
    ):
        once = apply_redflag_remediation(code, key, canvas, sections)
        twice = apply_redflag_remediation(code, key, canvas, once)
        assert once[key].count(marker) == 1
        assert twice[key] == once[key]
        sections = twice
