"""SQLite 하이브리드 RAG: FTS5(BM25) + sqlite-vec(768차원) + RRF(Reciprocal Rank Fusion)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ai.llm_client import embed_texts, estimate_tokens
from app.config import effective_embedding_space, load_profile
from app.db import get_conn, is_vec_available
from app.documents.hwpx_engine import parse_hwpx_or_hwp
from app.documents.pdf_engine import parse_pdf
from app.schemas.rag import RagDocumentInfo, RagSearchHit, RagStatus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_korean_search_tokens(text: str) -> str:
    """FTS5 unicode61 토크나이저에서 한국어 복합명사(예: 직접생산확인) 검색 누락을 막기 위한 2-gram 병행 토큰화."""
    cleaned = re.sub(r"[^\w가-힣\s]", " ", text or "")
    words = [w for w in cleaned.split() if w]
    bag: list[str] = list(words)
    for w in words:
        if len(w) >= 2 and re.search(r"[가-힣]", w):
            for i in range(len(w) - 1):
                bag.append(w[i : i + 2])
    return " ".join(bag)


def chunk_text(text: str, max_chars: int = 1000, overlap: int = 150) -> list[str]:
    """2,048토큰 제한을 초과하지 않도록 안전 길이(약 1,000자)로 청킹한다."""
    clean = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + max_chars)
        if end < len(clean):
            nl = clean.rfind("\n", start + max_chars // 2, end)
            if nl != -1:
                end = nl
        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(clean):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _serialize_vec(vec: list[float]) -> bytes:
    import sqlite_vec  # type: ignore

    return sqlite_vec.serialize_float32(vec)


def _get_rag_meta(conn: Any) -> tuple[str, int]:
    """인덱스에 저장된 벡터의 임베딩 공간(모델 식별자, 차원). 기록이 없으면 현재 공간으로 간주."""
    rows = conn.execute("SELECT key, value FROM rag_meta").fetchall()
    meta = {r["key"]: r["value"] for r in rows}
    cur_model, cur_dim = effective_embedding_space()
    model = meta.get("embedding_model") or cur_model
    dim = int(meta.get("embedding_dim") or cur_dim)
    return model, dim


def _set_rag_meta(conn: Any, model: str, dim: int) -> None:
    conn.execute("INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('embedding_model', ?)", (model,))
    conn.execute("INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('embedding_dim', ?)", (str(dim),))


def get_rag_status() -> RagStatus:
    cur_model, cur_dim = effective_embedding_space()
    vec_ok = is_vec_available()

    with get_conn() as conn:
        idx_model, idx_dim = _get_rag_meta(conn)
        doc_rows = conn.execute(
            "SELECT id, title, doc_type, file_path, chunk_count, created_at FROM rag_documents ORDER BY id DESC"
        ).fetchall()
        chunk_count = conn.execute("SELECT COUNT(*) AS cnt FROM rag_chunks").fetchone()["cnt"]

    needs_reindex = (len(doc_rows) > 0) and (cur_model != idx_model or cur_dim != idx_dim)
    docs = [
        RagDocumentInfo(
            id=r["id"],
            title=r["title"],
            doc_type=r["doc_type"],
            file_path=r["file_path"],
            chunk_count=r["chunk_count"],
            created_at=r["created_at"],
        )
        for r in doc_rows
    ]
    return RagStatus(
        vec_available=vec_ok,
        current_model=cur_model,
        current_dim=cur_dim,
        indexed_model=idx_model,
        indexed_dim=idx_dim,
        needs_reindex=needs_reindex,
        doc_count=len(docs),
        chunk_count=int(chunk_count),
        documents=docs,
    )


def ingest_text_document(
    title: str,
    text: str,
    doc_type: str = "reference",
    file_path: str = "",
    replace_same_title: bool = False,
) -> RagDocumentInfo:
    """텍스트 문서를 청킹하여 SQLite FTS5 + sqlite-vec에 동시 인덱싱한다.

    인덱스의 기존 벡터가 현재 임베딩 공간과 다르면(재인덱싱 필요 상태) 벡터를 섞지 않도록
    이 문서는 FTS 전용으로 저장하고 임베딩 API도 호출하지 않는다. [전체 재인덱싱] 시 벡터가 채워진다.
    """
    cur_model, cur_dim = effective_embedding_space()
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("인덱싱할 텍스트 내용이 비어 있습니다.")

    # 1) 벡터 호환성 판단(짧은 읽기) — 교체 대상 문서를 제외하고 남는 문서가 없으면 새 공간으로 시작한다.
    with get_conn() as conn:
        idx_model, idx_dim = _get_rag_meta(conn)
        if replace_same_title:
            other_docs = conn.execute(
                "SELECT COUNT(*) AS c FROM rag_documents WHERE NOT (title = ? AND doc_type = ?)", (title, doc_type)
            ).fetchone()["c"]
        else:
            other_docs = conn.execute("SELECT COUNT(*) AS c FROM rag_documents").fetchone()["c"]
    fresh_index = int(other_docs) == 0
    space_matches = fresh_index or (idx_model == cur_model and idx_dim == cur_dim)
    store_vectors = is_vec_available() and space_matches

    # 2) 임베딩은 DB 연결 밖에서 수행(네트워크 대기 중 연결 점유 방지). 실패 시 예외로 중단 → 아무것도 저장 안 됨.
    vectors: list[list[float]] | None = embed_texts(chunks, dim=cur_dim) if store_vectors else None
    now = _now_iso()

    with get_conn() as conn:
        if replace_same_title:
            existing = conn.execute(
                "SELECT id FROM rag_documents WHERE title = ? AND doc_type = ?", (title, doc_type)
            ).fetchall()
            for ex in existing:
                _delete_doc_in_conn(conn, ex["id"])

        cur = conn.execute(
            """
            INSERT INTO rag_documents (title, doc_type, file_path, chunk_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, doc_type, file_path, len(chunks), now),
        )
        doc_id = int(cur.lastrowid)

        for idx, chunk in enumerate(chunks):
            tok_est = estimate_tokens(chunk)
            c_cur = conn.execute(
                """
                INSERT INTO rag_chunks (doc_id, chunk_index, content, token_est, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, idx, chunk, tok_est, json.dumps({"title": title, "doc_type": doc_type}, ensure_ascii=False)),
            )
            chunk_id = int(c_cur.lastrowid)
            search_tokens = build_korean_search_tokens(chunk + " " + title)
            conn.execute(
                "INSERT INTO rag_chunks_fts (chunk_id, content, search_tokens) VALUES (?, ?, ?)",
                (chunk_id, chunk, search_tokens),
            )
            if vectors is not None:
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO rag_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                        (chunk_id, _serialize_vec(vectors[idx])),
                    )
                except Exception as exc:
                    logger.warning("vec0 insert skipped: %s", exc)

        if fresh_index:
            # 비어 있던 인덱스는 현재 임베딩 공간으로 시작
            _set_rag_meta(conn, cur_model, cur_dim)
        elif not space_matches:
            logger.warning(
                "RAG 인덱스 임베딩 공간(%s/%dD)이 현재(%s/%dD)와 달라 '%s'는 키워드(FTS) 전용으로 저장했습니다. 전체 재인덱싱이 필요합니다.",
                idx_model, idx_dim, cur_model, cur_dim, title,
            )

    return RagDocumentInfo(
        id=doc_id,
        title=title,
        doc_type=doc_type,
        file_path=file_path,
        chunk_count=len(chunks),
        created_at=now,
    )


def ingest_file(
    file_path: str | Path,
    title: str = "",
    doc_type: str = "reference",
    replace_same_title: bool = True,
) -> RagDocumentInfo:
    """로컬 .hwpx / .hwp / .pdf / .txt / .md 파일을 파싱해 RAG에 인덱싱한다."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    ext = path.suffix.lower()
    if ext in {".hwpx", ".hwp"}:
        parsed = parse_hwpx_or_hwp(path)
        text = parsed.full_text
    elif ext == ".pdf":
        parsed = parse_pdf(path)
        text = parsed.full_text
    elif ext in {".txt", ".md", ".json", ".yaml", ".yml", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"RAG 인덱싱 미지원 포맷입니다: {ext}")

    return ingest_text_document(
        title=title or path.name,
        text=text,
        doc_type=doc_type,
        file_path=str(path),
        replace_same_title=replace_same_title,
    )


def _delete_doc_in_conn(conn: Any, doc_id: int) -> None:
    chunk_rows = conn.execute("SELECT id FROM rag_chunks WHERE doc_id = ?", (doc_id,)).fetchall()
    for cr in chunk_rows:
        cid = cr["id"]
        conn.execute("DELETE FROM rag_chunks_fts WHERE chunk_id = ?", (cid,))
        if is_vec_available():
            try:
                conn.execute("DELETE FROM rag_chunks_vec WHERE chunk_id = ?", (cid,))
            except Exception:
                pass
    conn.execute("DELETE FROM rag_documents WHERE id = ?", (doc_id,))


def delete_rag_document(doc_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM rag_documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return False
        _delete_doc_in_conn(conn, doc_id)
    return True


def sync_profile_to_rag(profile: dict[str, Any] | None = None) -> RagDocumentInfo:
    """우리 회사 마스터 프로필을 로컬 RAG에 자동 반영해 제안서·사업계획서가 실제 실적을 인용하게 한다."""
    p = profile or load_profile()
    lines = [
        f"기업명: {p.get('name', '')} / 대표자: {p.get('ceo', '')} / 사업자번호: {p.get('biz_reg_no', '')}",
        f"사회적기업 유형: {p.get('social_type', '')} (인증여부: {p.get('is_certified_social')}, 취약계층 고용비율: {p.get('vulnerable_employment_ratio')}%)",
        f"소재지: {p.get('region', '')} / 업종코드: {', '.join(p.get('industry_codes') or [])}",
        f"보유 인증: {', '.join(p.get('certifications') or [])}",
        f"직접생산확인 품목: {', '.join(p.get('direct_production_items') or [])}",
        f"SVI 현황: 현재 {p.get('svi_current_score')}점 / 목표 {p.get('svi_target_score')}점",
        f"기업 핵심 역량 요약: {p.get('summary', '')}",
        "과거 주요 수행 실적:",
    ]
    for proj in p.get("past_projects") or []:
        lines.append(
            f"- [{proj.get('year', '')}] {proj.get('client', '')} | {proj.get('title', '')} | "
            f"계약금액 {int(proj.get('amount') or 0):,}원 | 성과: {proj.get('outcome', '')}"
        )
    return ingest_text_document(
        title="우리 회사 마스터 프로필",
        text="\n".join(lines),
        doc_type="master_profile",
        file_path="",
        replace_same_title=True,
    )


def reindex_all() -> RagStatus:
    """임베딩 공간(모델·차원·시뮬레이션↔Live) 변경 시 기존 모든 청크의 벡터 인덱스를 재구축한다.

    임베딩을 모두 먼저 계산한 뒤에만 기존 벡터 테이블을 교체하므로, API 실패·예산 초과 시 기존 인덱스는 그대로 남는다.
    """
    cur_model, cur_dim = effective_embedding_space()

    with get_conn() as conn:
        rows = [(int(r["id"]), str(r["content"])) for r in conn.execute("SELECT id, content FROM rag_chunks ORDER BY id ASC")]

    vectors = embed_texts([c for _, c in rows], dim=cur_dim) if rows else []

    with get_conn() as conn:
        if is_vec_available():
            try:
                conn.execute("DROP TABLE IF EXISTS rag_chunks_vec")
                conn.execute(
                    f"CREATE VIRTUAL TABLE rag_chunks_vec USING vec0(chunk_id INTEGER PRIMARY KEY, embedding float[{cur_dim}])"
                )
                for (chunk_id, _), vec in zip(rows, vectors):
                    conn.execute(
                        "INSERT INTO rag_chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                        (chunk_id, _serialize_vec(vec)),
                    )
            except Exception as exc:
                logger.warning("Reindex vec0 error: %s", exc)
                raise
        _set_rag_meta(conn, cur_model, cur_dim)

    return get_rag_status()


def hybrid_search(query: str, top_k: int = 5) -> list[RagSearchHit]:
    """FTS5(BM25) + sqlite-vec 코사인/L2 유사도를 RRF(Reciprocal Rank Fusion)로 결합 검색한다."""
    q = (query or "").strip()
    if not q:
        return []

    cur_model, cur_dim = effective_embedding_space()
    q_tokens = build_korean_search_tokens(q).split()
    # FTS5 OR 질의 생성 (특수문자 제거)
    safe_terms = [re.sub(r"[^\w가-힣]", "", t) for t in q_tokens if len(t) >= 2]
    fts_expr = " OR ".join(dict.fromkeys(safe_terms[:16]))

    fts_ranks: dict[int, int] = {}
    vec_ranks: dict[int, int] = {}
    k_limit = max(top_k * 3, 15)

    # 벡터 검색은 인덱스와 현재 임베딩 공간이 같을 때만 사용한다(다르면 FTS 전용).
    # 질의 임베딩은 DB 연결 밖에서 계산해 네트워크 대기 중 연결을 점유하지 않는다.
    q_vec: list[float] | None = None
    if is_vec_available():
        with get_conn() as conn:
            idx_model, idx_dim = _get_rag_meta(conn)
        if cur_model == idx_model and cur_dim == idx_dim:
            try:
                q_vec = embed_texts([q], dim=cur_dim)[0]
            except Exception as exc:
                # 임베딩 실패·예산 초과 시에도 키워드 검색 결과는 제공
                logger.warning("query embedding unavailable, FTS-only search: %s", exc)

    with get_conn() as conn:
        # 1. FTS5 BM25 검색
        if fts_expr:
            try:
                fts_rows = conn.execute(
                    """
                    SELECT chunk_id, bm25(rag_chunks_fts) AS score
                    FROM rag_chunks_fts
                    WHERE rag_chunks_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (fts_expr, k_limit),
                ).fetchall()
                for rank_idx, r in enumerate(fts_rows, start=1):
                    fts_ranks[int(r["chunk_id"])] = rank_idx
            except Exception as exc:
                logger.warning("FTS5 query warning: %s", exc)

        # 2. sqlite-vec 벡터 검색 (임베딩 공간 일치 시)
        if q_vec is not None:
            try:
                vec_rows = conn.execute(
                    """
                    SELECT chunk_id, distance
                    FROM rag_chunks_vec
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance ASC
                    """,
                    (_serialize_vec(q_vec), k_limit),
                ).fetchall()
                for rank_idx, r in enumerate(vec_rows, start=1):
                    vec_ranks[int(r["chunk_id"])] = rank_idx
            except Exception as exc:
                logger.warning("sqlite-vec search warning: %s", exc)

        # 3. RRF(Reciprocal Rank Fusion, k=60)
        all_ids = set(fts_ranks.keys()) | set(vec_ranks.keys())
        if not all_ids:
            # 데이터는 있으나 키워드가 빗나간 경우 최신 청크 일부를 폴백 조회
            fallback_rows = conn.execute("SELECT id FROM rag_chunks ORDER BY id DESC LIMIT ?", (top_k,)).fetchall()
            all_ids = {int(r["id"]) for r in fallback_rows}

        rrf_k = 60.0
        scored: list[tuple[float, int]] = []
        for cid in all_ids:
            s = 0.0
            if cid in fts_ranks:
                s += 1.0 / (rrf_k + fts_ranks[cid])
            if cid in vec_ranks:
                s += 1.0 / (rrf_k + vec_ranks[cid])
            if s == 0.0:
                s = 0.001
            scored.append((s, cid))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_ids = [cid for _, cid in scored[:top_k]]
        if not top_ids:
            return []

        placeholders = ",".join("?" for _ in top_ids)
        rows = conn.execute(
            f"""
            SELECT c.id, c.doc_id, c.content, c.metadata_json, d.title AS doc_title, d.doc_type
            FROM rag_chunks c
            JOIN rag_documents d ON d.id = c.doc_id
            WHERE c.id IN ({placeholders})
            """,
            top_ids,
        ).fetchall()
        row_map = {int(r["id"]): r for r in rows}

    hits: list[RagSearchHit] = []
    for score, cid in scored[:top_k]:
        r = row_map.get(cid)
        if not r:
            continue
        meta = {}
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except Exception:
            pass
        hits.append(
            RagSearchHit(
                chunk_id=cid,
                doc_id=int(r["doc_id"]),
                doc_title=r["doc_title"],
                doc_type=r["doc_type"],
                content=r["content"],
                rrf_score=round(score, 5),
                fts_rank=fts_ranks.get(cid),
                vec_rank=vec_ranks.get(cid),
                metadata=meta,
            )
        )
    return hits
