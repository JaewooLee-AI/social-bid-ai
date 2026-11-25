"""P2 회귀 테스트: RAG 임베딩 공간 오염 방지와 Live 실패 대체 초안 경고.

- 시뮬레이션(로컬 해시) 벡터와 Live Gemini 벡터가 한 인덱스에 섞이지 않아야 한다.
- Live 임베딩 실패 시 로컬 해시 벡터로 몰래 대체하지 않고 작업을 중단해야 한다(기존 인덱스 보존).
- 구버전 DB의 잘못된 공간 라벨은 Live 임베딩 기록이 없을 때만 정정한다.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import LOCAL_HASH_EMBEDDING_SPACE, get_db_path, load_config, save_config
from app.server import create_app

TOKEN = "space-test-token"
HEADERS = {"X-PyWebView-Token": TOKEN}
DOC = "성동구청 어르신 디지털 돌봄 시범사업 만족도 96.4점 수행 실적 보고서"


def _set_live(on: bool) -> None:
    cfg = load_config()
    cfg["llm"].update(api_key="test-key" if on else "", simulation_mode=not on, provider="gemini")
    save_config(cfg)


class _FakeGenai:
    def __init__(self, dim: int, fail: bool = False) -> None:
        self.dim = dim
        self.fail = fail
        self.embed_calls = 0

    def __call__(self, api_key: str) -> Any:
        return SimpleNamespace(models=self)

    def embed_content(self, model: str, contents: list[str], config: Any) -> Any:
        self.embed_calls += 1
        if self.fail:
            raise RuntimeError("embedding 503")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1] * self.dim) for _ in contents])

    def generate_content_stream(self, model: str, contents: Any, config: Any) -> Any:
        raise RuntimeError("generation 500")


def _fake(monkeypatch: pytest.MonkeyPatch, fail: bool = False) -> _FakeGenai:
    from google import genai

    fake = _FakeGenai(int(load_config()["llm"]["embedding_dim"]), fail=fail)
    monkeypatch.setattr(genai, "Client", fake)
    return fake


def test_sim_to_live_switch_flags_reindex_and_never_mixes(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.rag_store import get_rag_status, hybrid_search, ingest_text_document, reindex_all

    ingest_text_document("실적", DOC)
    s = get_rag_status()
    assert s.indexed_model == LOCAL_HASH_EMBEDDING_SPACE and not s.needs_reindex

    _set_live(True)
    fake = _fake(monkeypatch)
    s = get_rag_status()
    assert s.current_model == "gemini-embedding-001"
    assert s.needs_reindex is True  # 수정 전에는 False였음(해시 벡터를 Gemini 벡터로 오인)

    # 공간 불일치 중: 검색은 FTS 전용(질의 임베딩 API 호출 없음), 새 문서도 벡터 없이 저장(API 호출 없음)
    hits = hybrid_search("디지털 돌봄 만족도", top_k=3)
    assert hits and all(h.vec_rank is None for h in hits)
    ingest_text_document("추가 문서", "취약계층 고용률 38.5% 달성 증빙 자료")
    assert fake.embed_calls == 0
    assert get_rag_status().needs_reindex is True

    # 재인덱싱 후에는 Live 공간으로 통일되고 벡터 검색 사용
    s = reindex_all()
    assert s.indexed_model == "gemini-embedding-001" and not s.needs_reindex
    assert fake.embed_calls >= 1
    hits = hybrid_search("디지털 돌봄 만족도", top_k=3)
    assert any(h.vec_rank is not None for h in hits)


def test_live_embedding_failure_does_not_store_or_corrupt(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.llm_client import EmbeddingUnavailableError
    from app.ai.rag_store import get_rag_status, hybrid_search, ingest_text_document, reindex_all

    _set_live(True)
    _fake(monkeypatch)
    ingest_text_document("실적", DOC)  # 정상 Live 인덱스
    assert get_rag_status().indexed_model == "gemini-embedding-001"

    fake = _fake(monkeypatch, fail=True)
    with pytest.raises(EmbeddingUnavailableError):
        ingest_text_document("실패 문서", "임베딩 API 장애 중 업로드한 새로운 문서 본문")
    status = get_rag_status()
    assert status.doc_count == 1  # 해시 벡터로 몰래 저장되지 않음

    # 재인덱싱 실패 시 기존 벡터 인덱스 보존
    count_before = _vec_count()
    with pytest.raises(EmbeddingUnavailableError):
        # 캐시에 없는 청크가 있어야 API를 호출하므로 캐시를 비운다
        from app.ai.llm_cache import clear_caches

        clear_caches()
        reindex_all()
    assert _vec_count() == count_before
    assert get_rag_status().indexed_model == "gemini-embedding-001"

    # 검색은 임베딩 실패에도 키워드 결과 제공
    assert hybrid_search("디지털 돌봄", top_k=3)
    assert fake.embed_calls >= 2


def _vec_count() -> int:
    from app.db import get_conn, is_vec_available

    if not is_vec_available():
        return -1
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) AS c FROM rag_chunks_vec").fetchone()["c"])


def test_ingest_api_returns_502_on_embedding_failure(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _set_live(True)
    _fake(monkeypatch, fail=True)
    doc = tmp_path / "fail.md"
    doc.write_text("장애 중 업로드 문서 본문입니다.", encoding="utf-8")
    client = TestClient(create_app(session_token=TOKEN, sync_profile_rag=False))
    r = client.post("/api/rag/ingest-path", json={"file_path": str(doc)}, headers=HEADERS)
    assert r.status_code == 502
    assert "저장하지 않았습니다" in r.json()["detail"]

    # 프로필 저장은 임베딩 실패와 무관하게 성공
    prof = client.get("/api/settings/profile", headers=HEADERS).json()
    prof["summary"] = "임베딩 장애 중 수정"
    assert client.put("/api/settings/profile", json=prof, headers=HEADERS).status_code == 200


def _write_legacy_meta(model: str, live_embedding_usage: bool) -> None:
    con = sqlite3.connect(get_db_path())
    con.execute("INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('embedding_model', ?)", (model,))
    if live_embedding_usage:
        con.execute(
            "INSERT INTO token_usage (provider, model, feature, input_tokens, output_tokens, thinking_tokens, "
            "cost_usd, cost_krw, pricing_missing, created_at) VALUES ('gemini', ?, 'embedding', 10, 0, 0, 0, 0, 0, ?)",
            (model, datetime.now().astimezone().isoformat()),
        )
    con.commit()
    con.close()


def _indexed_label() -> str:
    con = sqlite3.connect(get_db_path())
    v = con.execute("SELECT value FROM rag_meta WHERE key = 'embedding_model'").fetchone()[0]
    con.close()
    return str(v)


def test_legacy_label_migration_only_without_live_embeddings(isolated_data_dir: Path) -> None:
    from app.db import init_db

    _write_legacy_meta("gemini-embedding-001", live_embedding_usage=False)
    init_db()
    assert _indexed_label() == LOCAL_HASH_EMBEDDING_SPACE  # 실제로는 해시 벡터였으므로 정정

    _write_legacy_meta("gemini-embedding-001", live_embedding_usage=True)
    init_db()
    assert _indexed_label() == "gemini-embedding-001"  # 실제 Gemini 벡터일 수 있어 보존


def test_live_failure_fallback_is_visibly_marked(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ai.llm_client import stream_text

    _set_live(True)
    _fake(monkeypatch)

    async def run() -> str:
        return "".join([c async for c in stream_text("sys", "user", feature="t", simulation_text="### 템플릿 초안")])

    out = asyncio.run(run())
    assert out.startswith("> ⚠️ **Live AI 호출에 실패해 자동 템플릿 초안으로 대체되었습니다.**")
    assert "### 템플릿 초안" in out
    assert "generation 500" in out
