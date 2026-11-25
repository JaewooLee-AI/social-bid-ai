"""P2 회귀 테스트: async 라우트 안의 느린 동기 작업(RAG 검색·임베딩·문서 분석)이 이벤트 루프를 막지 않는지.

TestClient를 컨텍스트로 열어 하나의 이벤트 루프를 공유시키고, 느린 작업이 진행되는 동안
/api/health 응답 시간을 측정한다. 루프가 막히면 health도 느린 작업이 끝날 때까지 기다린다.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.ai import job_guard
from app.server import create_app

TOKEN = "loop-test-token"
HEADERS = {"X-PyWebView-Token": TOKEN}
SLOW_SEC = 1.5


@pytest.fixture(autouse=True)
def _reset_guard() -> None:
    job_guard.reset_all()
    yield
    job_guard.reset_all()


def _health_latency_during(client: TestClient, slow_call: Callable[[], Any], started: threading.Event) -> float:
    result: dict[str, Any] = {}

    def run() -> None:
        result["resp"] = slow_call()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert started.wait(5), "느린 작업이 시작되지 않음"
    t0 = time.perf_counter()
    r = client.get("/api/health")
    latency = time.perf_counter() - t0
    assert r.status_code == 200
    t.join(15)
    assert result["resp"].status_code == 200, result["resp"].text
    return latency


def test_bizplan_stream_rag_search_does_not_block_loop(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ai.bizplan_gen as bizplan_gen

    started = threading.Event()
    real = bizplan_gen.hybrid_search

    def slow_search(*args: Any, **kwargs: Any) -> Any:
        started.set()
        time.sleep(SLOW_SEC)  # 네트워크 임베딩 호출 지연 모사
        return real(*args, **kwargs)

    monkeypatch.setattr(bizplan_gen, "hybrid_search", slow_search)

    with TestClient(create_app(session_token=TOKEN, sync_profile_rag=False)) as client:
        bp = client.post("/api/bizplan", json={"title": "루프 테스트"}, headers=HEADERS).json()
        latency = _health_latency_during(
            client,
            lambda: client.post(
                "/api/bizplan/generate", json={"bizplan_id": bp["id"], "section_keys": ["problem"]}, headers=HEADERS
            ),
            started,
        )
    assert latency < SLOW_SEC / 2, f"이벤트 루프가 {latency:.2f}s 동안 막힘"


def test_document_upload_analysis_does_not_block_loop(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes_docs as routes_docs

    started = threading.Event()
    real = routes_docs._analyze_local_file

    def slow_analyze(path: Path) -> Any:
        started.set()
        time.sleep(SLOW_SEC)  # 스캔 PDF Vision OCR 지연 모사
        return real(path)

    monkeypatch.setattr(routes_docs, "_analyze_local_file", slow_analyze)

    with TestClient(create_app(session_token=TOKEN, sync_profile_rag=False)) as client:
        sample = client.get("/api/docs/sample-template?kind=rfp", headers=HEADERS).json()
        data = Path(sample["document"]["file_path"]).read_bytes()
        latency = _health_latency_during(
            client,
            lambda: client.post(
                "/api/docs/upload",
                files={"file": ("loop_test.hwpx", data, "application/octet-stream")},
                headers=HEADERS,
            ),
            started,
        )
    assert latency < SLOW_SEC / 2, f"이벤트 루프가 {latency:.2f}s 동안 막힘"


def test_rag_ingest_upload_does_not_block_loop(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes_settings as routes_settings

    started = threading.Event()
    real = routes_settings.ingest_file

    def slow_ingest(*args: Any, **kwargs: Any) -> Any:
        started.set()
        time.sleep(SLOW_SEC)  # 대량 청크 임베딩 지연 모사
        return real(*args, **kwargs)

    monkeypatch.setattr(routes_settings, "ingest_file", slow_ingest)

    with TestClient(create_app(session_token=TOKEN, sync_profile_rag=False)) as client:
        latency = _health_latency_during(
            client,
            lambda: client.post(
                "/api/rag/ingest",
                files={"file": ("loop.md", "성동구청 디지털 돌봄 실적 문서".encode(), "text/markdown")},
                headers=HEADERS,
            ),
            started,
        )
    assert latency < SLOW_SEC / 2, f"이벤트 루프가 {latency:.2f}s 동안 막힘"
