"""FastAPI 애플리케이션 팩토리 및 정적 React(ui_dist) 서빙."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.ai.llm_budget import BudgetExceededError
from app.ai.llm_client import EmbeddingUnavailableError, is_simulation_active
from app.ai.rag_store import sync_profile_to_rag
from app.api.deps import get_session_token, set_session_token
from app.api.routes_bids import router as bids_router
from app.api.routes_bizplan import router as bizplan_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_docs import router as docs_router
from app.api.routes_proposal import router as proposal_router
from app.api.routes_settings import router as settings_router
from app.config import get_data_dir, get_resource_dir, seed_defaults
from app.db import init_db, is_vec_available
from app.documents.errors import DocumentParseError
from app.schemas.common import HealthResponse


def create_app(session_token: str | None = None, sync_profile_rag: bool = True) -> FastAPI:
    if session_token:
        set_session_token(session_token)

    seed_defaults()
    init_db()
    if sync_profile_rag:
        try:
            sync_profile_to_rag()
        except Exception:
            pass

    app = FastAPI(
        title="SocialBidAI Local API",
        version=__version__,
        docs_url="/api/docs-swagger",
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(BudgetExceededError)
    async def _budget_exceeded_handler(_request: Any, exc: BudgetExceededError) -> JSONResponse:
        # 402 Payment Required: 월 LLM 예산 초과로 신규 Live 호출 차단
        return JSONResponse(status_code=402, content={"detail": str(exc)})

    @app.exception_handler(DocumentParseError)
    async def _document_parse_handler(_request: Any, exc: DocumentParseError) -> JSONResponse:
        # 400: 사용자가 올린 파일 문제(손상·암호·보안 거부). 예전엔 500 "요청 실패"만 보였음
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def _file_not_found_handler(_request: Any, exc: FileNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc) or "파일을 찾을 수 없습니다."})

    @app.exception_handler(EmbeddingUnavailableError)
    async def _embedding_unavailable_handler(_request: Any, exc: EmbeddingUnavailableError) -> JSONResponse:
        # 502 Bad Gateway: 외부 임베딩 API 실패. 인덱스 오염 방지를 위해 해당 작업은 저장하지 않고 중단됨
        return JSONResponse(
            status_code=502,
            content={"detail": f"{exc} — 인덱스 오염을 막기 위해 이번 작업은 저장하지 않았습니다. 잠시 후 다시 시도하세요."},
        )

    # 개발 모드(Vite localhost:5173 -> FastAPI localhost:8000) CORS 허용
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            version=__version__,
            simulation_mode=is_simulation_active(),
            vec_available=is_vec_available(),
            data_dir=str(get_data_dir()),
        )

    @app.get("/api/dev-bootstrap", tags=["system"])
    def dev_bootstrap() -> dict[str, str]:
        """개발 서버 모드(--dev-server / SOCIALBID_DEV_MODE=1) 전용 토큰 부트스트랩."""
        if os.environ.get("SOCIALBID_DEV_MODE") != "1":
            raise HTTPException(status_code=403, detail="개발 모드에서만 허용됩니다.")
        return {"token": get_session_token()}

    app.include_router(dashboard_router)
    app.include_router(bids_router)
    app.include_router(docs_router)
    app.include_router(proposal_router)
    app.include_router(bizplan_router)
    app.include_router(settings_router)

    # 빌드된 React 정적 자산(frontend/ui_dist) 마운트
    ui_dist = get_resource_dir() / "frontend" / "ui_dist"
    assets_dir = ui_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        if ui_dist.exists():
            candidate = (ui_dist / full_path).resolve()
            if full_path and candidate.is_file() and ui_dist.resolve() in candidate.parents:
                return FileResponse(str(candidate))
            index_file = ui_dist / "index.html"
            if index_file.exists():
                return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="frontend/ui_dist 빌드가 없습니다. npm run build를 실행하세요.")

    return app
