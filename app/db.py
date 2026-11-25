"""SQLite + FTS5 + sqlite-vec 하이브리드 데이터베이스 관리."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from app.config import LOCAL_HASH_EMBEDDING_SPACE, effective_embedding_space, get_db_path, load_config

logger = logging.getLogger(__name__)

_VEC_SUPPORTED: bool | None = None


def _try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    global _VEC_SUPPORTED
    try:
        import sqlite_vec  # type: ignore

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_SUPPORTED = True
        return True
    except Exception as exc:
        logger.warning("sqlite-vec 로드 실패 (FTS5 폴백 활성화): %s", exc)
        _VEC_SUPPORTED = False
        return False


def is_vec_available() -> bool:
    if _VEC_SUPPORTED is None:
        with get_conn() as _:
            pass
    return bool(_VEC_SUPPORTED)


# SQLite 3.51.0 ~ 3.51.2의 unix VFS에는 "broken POSIX lock 감지" 로직의 락 순서 역전 버그가 있다.
# 마지막 WAL 연결을 닫는 스레드(sqlite3WalClose -> unixLock: inode 락 -> 전역 VFS 락)와
# 같은 파일을 여는 스레드(findReusableFd: 전역 VFS 락 -> inode 락)가 겹치면 영구 교착되어
# 이후 모든 DB 접근이 멈춘다(앱 전체 무응답). 3.51.2에서 수정, 3.51.3에서 전역 락 사용 자체가 제거됨.
# 참고: https://sqlite.org/changes.html (3.51.2 "Fix an obscure deadlock in the new broken-posix-lock detection logic")
# 영향 버전에서는 연결 열기·닫기만 프로세스 전역 락으로 직렬화한다(쿼리 실행은 병렬 유지).
_SQLITE_OPEN_CLOSE_DEADLOCK_BUG = (3, 51, 0) <= sqlite3.sqlite_version_info < (3, 51, 3)
_OPEN_CLOSE_LOCK = threading.Lock()

if _SQLITE_OPEN_CLOSE_DEADLOCK_BUG:
    logger.warning(
        "SQLite %s는 연결 열기/닫기 교착 버그가 있는 버전입니다(3.51.0~3.51.2). "
        "연결 열기·닫기를 직렬화하는 우회책을 적용합니다. 가능하면 SQLite 3.51.3 이상으로 업그레이드하세요.",
        sqlite3.sqlite_version,
    )


@contextmanager
def _open_close_guard() -> Iterator[None]:
    if _SQLITE_OPEN_CLOSE_DEADLOCK_BUG:
        with _OPEN_CLOSE_LOCK:
            yield
    else:
        yield


def _open_connection(db_path: str) -> sqlite3.Connection:
    # journal_mode 전환(최초 WAL 설정)도 파일 락을 잡으므로 열기 구간에 포함한다.
    with _open_close_guard():
        conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            _try_load_sqlite_vec(conn)
        except Exception:
            conn.close()
            raise
    return conn


def _close_connection(conn: sqlite3.Connection) -> None:
    with _open_close_guard():
        conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """SQLite 연결 컨텍스트 매니저 (WAL + Foreign Key + sqlite-vec 로드)."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open_connection(str(db_path))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _close_connection(conn)


CORE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    bid_no TEXT NOT NULL,
    bid_ord TEXT NOT NULL DEFAULT '00',
    title TEXT NOT NULL,
    agency TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '용역',
    contract_method TEXT NOT NULL DEFAULT '',
    estimated_price INTEGER NOT NULL DEFAULT 0,
    deadline TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    region_limit TEXT NOT NULL DEFAULT '전국',
    license_limit TEXT NOT NULL DEFAULT '',
    is_sme_competing INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    privilege_tier TEXT NOT NULL DEFAULT 'none',
    privilege_label TEXT NOT NULL DEFAULT '',
    rule_passed INTEGER NOT NULL DEFAULT 1,
    rule_warnings_json TEXT NOT NULL DEFAULT '[]',
    ai_score REAL,
    ai_summary TEXT NOT NULL DEFAULT '',
    ai_risks_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    exclusion_reason TEXT NOT NULL DEFAULT '',
    exclusion_note TEXT NOT NULL DEFAULT '',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    rfp_analysis_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, bid_no, bid_ord)
);

CREATE TABLE IF NOT EXISTS exclusion_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER,
    bid_title TEXT NOT NULL,
    agency TEXT NOT NULL DEFAULT '',
    reason_tag TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    last_synced_at TEXT NOT NULL,
    last_status TEXT NOT NULL DEFAULT 'ok',
    last_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS api_quota_log (
    date_str TEXT NOT NULL,
    source TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0,
    daily_limit INTEGER NOT NULL DEFAULT 1000,
    PRIMARY KEY (date_str, source)
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER,
    title TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT 'B',
    source_file_path TEXT NOT NULL DEFAULT '',
    source_file_type TEXT NOT NULL DEFAULT '',
    conversion_warnings_json TEXT NOT NULL DEFAULT '[]',
    rfp_summary_json TEXT NOT NULL DEFAULT '{}',
    scoring_table_json TEXT NOT NULL DEFAULT '[]',
    outline_json TEXT NOT NULL DEFAULT '[]',
    sections_content_json TEXT NOT NULL DEFAULT '{}',
    citations_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bizplans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    output_track TEXT NOT NULL DEFAULT 'psst',
    template_hwpx_path TEXT NOT NULL DEFAULT '',
    canvas_json TEXT NOT NULL DEFAULT '{}',
    unit_economics_json TEXT NOT NULL DEFAULT '{}',
    svi_assessment_json TEXT NOT NULL DEFAULT '{}',
    sections_content_json TEXT NOT NULL DEFAULT '{}',
    audit_report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL DEFAULT 'reference',
    file_path TEXT NOT NULL DEFAULT '',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_est INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (doc_id) REFERENCES rag_documents(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    content,
    search_tokens
);

CREATE TABLE IF NOT EXISTS rag_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 동일 텍스트 재임베딩 방지 캐시 (실제 API 결과만 저장)
CREATE TABLE IF NOT EXISTS embedding_cache (
    text_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (text_hash, model, dim)
);

-- 동일 스캔 페이지 이미지 재OCR 방지 캐시 (성공 결과만 저장)
CREATE TABLE IF NOT EXISTS vision_ocr_cache (
    image_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (image_hash, model)
);

CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    feature TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    thinking_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    cost_krw REAL NOT NULL DEFAULT 0.0,
    pricing_missing INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


def _migrate_legacy_embedding_label(conn: sqlite3.Connection, indexed_model: str) -> None:
    """구버전은 시뮬레이션(로컬 해시) 벡터도 'gemini-embedding-001' 등 설정 모델명으로 기록했다.

    Live Gemini 임베딩 호출 기록이 한 번도 없다면 저장된 벡터는 전부 로컬 해시 벡터이므로
    실제 공간 이름으로 정정한다. Live 기록이 있으면 실제 Gemini 벡터일 수 있어 건드리지 않는다.
    """
    if indexed_model == LOCAL_HASH_EMBEDDING_SPACE:
        return
    live_embed = conn.execute(
        "SELECT 1 FROM token_usage WHERE feature = 'embedding' AND provider != 'simulation' LIMIT 1"
    ).fetchone()
    if live_embed is None:
        conn.execute(
            "INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('embedding_model', ?)", (LOCAL_HASH_EMBEDDING_SPACE,)
        )
        logger.info("RAG 인덱스 임베딩 공간 라벨 정정: %s -> %s", indexed_model, LOCAL_HASH_EMBEDDING_SPACE)


def init_db() -> dict[str, bool]:
    """모든 테이블 및 벡터/FTS 가상 테이블을 초기화한다."""
    cfg = load_config()
    dim = int(cfg["llm"].get("embedding_dim", 768))
    emb_model, _ = effective_embedding_space(cfg)

    with get_conn() as conn:
        conn.executescript(CORE_SCHEMA_SQL)

        # 기존 DB 스키마 마이그레이션: token_usage 신규 컬럼 추가
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(token_usage)").fetchall()}
        if "thinking_tokens" not in cols:
            conn.execute("ALTER TABLE token_usage ADD COLUMN thinking_tokens INTEGER NOT NULL DEFAULT 0")
        if "pricing_missing" not in cols:
            conn.execute("ALTER TABLE token_usage ADD COLUMN pricing_missing INTEGER NOT NULL DEFAULT 0")
        vec_ok = bool(_VEC_SUPPORTED)
        if vec_ok:
            try:
                conn.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_vec USING vec0(
                        chunk_id INTEGER PRIMARY KEY,
                        embedding float[{dim}]
                    );
                    """
                )
            except Exception as exc:
                logger.warning("rag_chunks_vec 생성 실패: %s", exc)
                vec_ok = False

        # 최초 rag_meta 기록 (존재하지 않을 때만)
        row = conn.execute("SELECT value FROM rag_meta WHERE key = 'embedding_model'").fetchone()
        if row is None:
            conn.execute("INSERT INTO rag_meta(key, value) VALUES ('embedding_model', ?)", (emb_model,))
            conn.execute("INSERT OR REPLACE INTO rag_meta(key, value) VALUES ('embedding_dim', ?)", (str(dim),))
        else:
            _migrate_legacy_embedding_label(conn, str(row["value"]))

    return {"initialized": True, "vec_available": vec_ok}
