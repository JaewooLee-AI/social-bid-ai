"""SQLite 하이브리드 RAG (FTS5 + sqlite-vec RRF 융합 검색 및 임베딩 모델 변경 재인덱싱 경고) 검증."""

from __future__ import annotations

from app.ai.rag_store import (
    chunk_text,
    get_rag_status,
    hybrid_search,
    ingest_file,
    ingest_text_document,
    reindex_all,
    sync_profile_to_rag,
)
from app.config import load_config, save_config


def test_korean_chunking_hybrid_rrf_and_reindex(isolated_data_dir) -> None:
    # 2,048 토큰 초과 방지 청킹 검증
    long_text = ("사회적기업 직접생산확인증명서 및 SVI 우수등급 우선구매 실적 120% 인정 가이드. " * 120).strip()
    chunks = chunk_text(long_text, max_chars=900, overlap=100)
    assert len(chunks) >= 3
    assert all(len(c) <= 950 for c in chunks)

    sync_profile_to_rag()
    ingest_text_document(
        title="2025 성동구 디지털 돌봄 완료보고서",
        text=(
            "성동구청과 함께 진행한 어르신 디지털 돌봄 매니저 사업에서 만족도 96.4점을 달성했고 "
            "경력보유여성 6명을 정규직으로 채용했습니다. 직접생산확인증명서를 보유하고 있습니다."
        ),
        doc_type="past_winning",
    )

    hits = hybrid_search("성동구 어르신 디지털 돌봄 직접생산확인", top_k=3)
    assert len(hits) >= 1
    assert any("성동구" in h.content for h in hits)
    assert hits[0].rrf_score > 0

    # 시뮬레이션 모드는 모델명과 무관하게 로컬 해시 공간이므로 모델명 변경만으로는 재인덱싱 불필요
    cfg = load_config()
    cfg["llm"]["embedding_model"] = "gemini-embedding-2"
    save_config(cfg)
    assert get_rag_status().needs_reindex is False

    # 임베딩 차원 변경은 벡터 공간이 바뀌므로 needs_reindex=True 감지 검증
    cfg = load_config()
    cfg["llm"]["embedding_dim"] = 512
    save_config(cfg)

    status_before = get_rag_status()
    assert status_before.needs_reindex is True

    status_after = reindex_all()
    assert status_after.needs_reindex is False
    assert status_after.indexed_dim == 512
    assert status_after.indexed_model == "local-hash-2gram-v1"
    assert hybrid_search("성동구 어르신 디지털 돌봄", top_k=3)


def test_ingest_file_replaces_same_title_without_duplicates(isolated_data_dir, tmp_path) -> None:
    sample = tmp_path / "01_test_doc.md"
    sample.write_text(
        "성동구청 어르신 디지털 돌봄 매니저 용역 완료보고서 초안입니다. 직접생산확인증명서를 보유하고 있습니다.",
        encoding="utf-8",
    )

    first = ingest_file(sample)
    second = ingest_file(sample)
    status = get_rag_status()
    assert status.doc_count == 1
    assert len(status.documents) == 1
    assert status.documents[0].title == "01_test_doc.md"
    assert second.id != first.id or status.doc_count == 1

    sample.write_text(
        "성동구청 어르신 디지털 돌봄 매니저 용역 완료보고서 개정판입니다. 취약계층 고용률 38.5%를 달성했습니다.",
        encoding="utf-8",
    )
    ingest_file(sample)
    updated_status = get_rag_status()
    assert updated_status.doc_count == 1
    assert updated_status.documents[0].title == "01_test_doc.md"
    hits = hybrid_search("개정판 취약계층 고용률", top_k=3)
    assert len(hits) >= 1
    assert any("개정판" in h.content for h in hits)
    assert not any("초안입니다" in h.content for h in hits)
