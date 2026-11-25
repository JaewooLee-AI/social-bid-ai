"""LLM 결과 재사용 캐시 (SQLite): 임베딩 벡터·Vision OCR 텍스트.

동일 입력에 대한 반복 API 호출(같은 문서 재업로드·재인덱싱·동일 검색 질의)을 0원으로 만든다.
실제 API가 반환한 정상 결과만 저장하며, 시뮬레이션·오류 결과는 저장하지 않는다.
"""

from __future__ import annotations

import hashlib
from array import array
from datetime import datetime, timezone

from app.db import get_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> list[float]:
    arr = array("f")
    arr.frombytes(blob)
    return [float(x) for x in arr]


def get_cached_embeddings(hashes: list[str], model: str, dim: int) -> dict[str, list[float]]:
    if not hashes:
        return {}
    out: dict[str, list[float]] = {}
    uniq = list(dict.fromkeys(hashes))
    with get_conn() as conn:
        for i in range(0, len(uniq), 500):
            part = uniq[i : i + 500]
            placeholders = ",".join("?" for _ in part)
            rows = conn.execute(
                f"""
                SELECT text_hash, vector FROM embedding_cache
                WHERE model = ? AND dim = ? AND text_hash IN ({placeholders})
                """,
                (model, dim, *part),
            ).fetchall()
            for r in rows:
                vec = _unpack(r["vector"])
                if len(vec) == dim:
                    out[r["text_hash"]] = vec
    return out


def put_cached_embeddings(items: dict[str, list[float]], model: str, dim: int) -> None:
    if not items:
        return
    now = _now_iso()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO embedding_cache (text_hash, model, dim, vector, created_at) VALUES (?, ?, ?, ?, ?)",
            [(h, model, dim, _pack(v), now) for h, v in items.items() if len(v) == dim],
        )


def get_cached_ocr(image_hash: str, model: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT text FROM vision_ocr_cache WHERE image_hash = ? AND model = ?", (image_hash, model)
        ).fetchone()
    return str(row["text"]) if row else None


def put_cached_ocr(image_hash: str, model: str, text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vision_ocr_cache (image_hash, model, text, created_at) VALUES (?, ?, ?, ?)",
            (image_hash, model, text, _now_iso()),
        )


def get_cache_stats() -> dict[str, int]:
    with get_conn() as conn:
        emb = conn.execute("SELECT COUNT(*) AS c FROM embedding_cache").fetchone()["c"]
        ocr = conn.execute("SELECT COUNT(*) AS c FROM vision_ocr_cache").fetchone()["c"]
    return {"embedding_entries": int(emb), "ocr_entries": int(ocr)}


def clear_caches() -> dict[str, int]:
    stats = get_cache_stats()
    with get_conn() as conn:
        conn.execute("DELETE FROM embedding_cache")
        conn.execute("DELETE FROM vision_ocr_cache")
    return stats
