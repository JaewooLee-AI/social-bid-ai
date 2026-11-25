"""마스터 프로필, API 키·단가 설정, 프롬프트 YAML 원격 동기화 및 로컬 하이브리드 RAG 관리 API."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.ai.job_guard import acquire_or_409, release
from app.ai.llm_budget import BudgetExceededError, ensure_budget_available, get_budget_status
from app.ai.llm_cache import clear_caches, get_cache_stats
from app.ai.llm_client import (
    EmbeddingUnavailableError,
    get_token_usage_report,
    recalculate_token_usage_costs,
    test_gemini_connection,
)
from app.ai.model_catalog import load_model_catalog, sync_model_catalog_from_official
from app.ai.rag_store import (
    delete_rag_document,
    get_rag_status,
    hybrid_search,
    ingest_file,
    reindex_all,
    sync_profile_to_rag,
)
from app.api.deps import verify_csrf_token
from app.collectors.bizinfo import test_bizinfo_connection
from app.collectors.g2b import test_g2b_connection
from app.collectors.seis import test_seis_connection
from app.config import (
    activate_profile_preset,
    delete_profile_preset,
    get_data_dir,
    get_downloads_dir,
    list_profile_presets,
    list_prompts,
    load_config,
    load_profile,
    load_svi_rubric,
    save_config,
    save_profile,
    save_profile_preset,
    save_prompt,
)
from app.schemas.common import ApiStatusResponse
from app.schemas.rag import RagDocumentInfo, RagIngestPathRequest, RagSearchHit, RagSearchRequest, RagStatus
from app.schemas.settings import (
    ApiTestRequest,
    AppConfig,
    CompanyProfile,
    ModelCatalogResponse,
    ModelSyncResponse,
    ProfilePresetCreateRequest,
    ProfilePresetInfo,
    PromptItem,
    PromptUpdateRequest,
    RecalculateUsageResponse,
    RemotePromptSyncRequest,
    TokenUsageReport,
)

router = APIRouter(prefix="/api", tags=["settings-rag"], dependencies=[Depends(verify_csrf_token)])

logger = logging.getLogger(__name__)


def _sync_profile_rag_safely(saved: dict[str, Any]) -> None:
    """프로필 저장 자체는 성공시키고, 예산 초과로 RAG 재임베딩만 실패하면 경고 로그만 남긴다."""
    try:
        sync_profile_to_rag(saved)
    except (BudgetExceededError, EmbeddingUnavailableError) as exc:
        logger.warning("마스터 프로필 RAG 동기화 생략(예산 초과 또는 임베딩 API 실패): %s", exc)


@router.get("/settings", response_model=AppConfig)
def get_settings() -> AppConfig:
    return AppConfig(**load_config())


@router.put("/settings", response_model=AppConfig)
def update_settings(cfg: AppConfig) -> AppConfig:
    saved = save_config(cfg.model_dump())
    return AppConfig(**saved)


@router.get("/settings/models", response_model=ModelCatalogResponse)
def get_settings_models() -> ModelCatalogResponse:
    return load_model_catalog()


@router.post("/settings/models/sync", response_model=ModelSyncResponse)
async def sync_settings_models() -> ModelSyncResponse:
    return await sync_model_catalog_from_official()


@router.get("/settings/token-usage", response_model=TokenUsageReport)
def get_settings_token_usage(scope: str = "month") -> TokenUsageReport:
    return get_token_usage_report(scope=scope)


@router.post("/settings/token-usage/recalculate", response_model=RecalculateUsageResponse)
def recalculate_settings_token_usage() -> RecalculateUsageResponse:
    return recalculate_token_usage_costs()


@router.get("/settings/profile", response_model=CompanyProfile)
def get_company_profile() -> CompanyProfile:
    return CompanyProfile(**load_profile())


@router.put("/settings/profile", response_model=CompanyProfile)
def update_company_profile(profile: CompanyProfile) -> CompanyProfile:
    saved = save_profile(profile.model_dump())
    # 마스터 프로필 변경 시 로컬 RAG 자동 동기화
    _sync_profile_rag_safely(saved)
    return CompanyProfile(**saved)


@router.get("/settings/profile-presets", response_model=list[ProfilePresetInfo])
def get_profile_presets() -> list[ProfilePresetInfo]:
    return [ProfilePresetInfo(**p) for p in list_profile_presets()]


@router.post("/settings/profile-presets", response_model=ProfilePresetInfo)
def create_profile_preset(req: ProfilePresetCreateRequest) -> ProfilePresetInfo:
    prof_dict = req.profile.model_dump() if req.profile is not None else None
    saved = save_profile_preset(req.preset_name, prof_dict)
    return ProfilePresetInfo(**saved)


@router.post("/settings/profile-presets/{slug}/activate", response_model=CompanyProfile)
def switch_profile_preset(slug: str) -> CompanyProfile:
    try:
        saved = activate_profile_preset(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _sync_profile_rag_safely(saved)
    return CompanyProfile(**saved)


@router.delete("/settings/profile-presets/{slug}")
def remove_profile_preset(slug: str) -> dict[str, bool]:
    ok = delete_profile_preset(slug)
    if not ok:
        raise HTTPException(status_code=404, detail="프로필 프리셋을 찾을 수 없습니다.")
    return {"ok": True}


@router.post("/settings/test-api", response_model=ApiStatusResponse)
async def test_api_connection(req: ApiTestRequest) -> ApiStatusResponse:
    if req.target == "gemini":
        res = await test_gemini_connection()
    elif req.target == "g2b":
        res = await test_g2b_connection()
    elif req.target == "seis":
        res = await test_seis_connection()
    elif req.target == "bizinfo":
        res = await test_bizinfo_connection()
    else:
        raise HTTPException(status_code=400, detail=f"알 수 없는 테스트 대상입니다: {req.target}")
    return ApiStatusResponse(ok=bool(res.get("ok")), message=str(res.get("message", "")), details=res.get("details", {}))


@router.get("/settings/prompts", response_model=list[PromptItem])
def get_all_prompts() -> list[PromptItem]:
    return [PromptItem(**p) for p in list_prompts()]


@router.put("/settings/prompts/{name}", response_model=PromptItem)
def update_prompt_item(name: str, req: PromptUpdateRequest) -> PromptItem:
    saved = save_prompt(name, req.system_prompt, req.description, req.version)
    return PromptItem(**saved)


@router.post("/settings/prompts/sync-remote", response_model=ApiStatusResponse)
async def sync_remote_prompts(req: RemotePromptSyncRequest) -> ApiStatusResponse:
    """고객사별 프롬프트 외부 원격 매니페스트(JSON)를 받아 %APPDATA%/prompts/를 업데이트한다."""
    cfg = load_config()
    url = (req.manifest_url or cfg.get("remote_prompts_manifest_url") or "").strip()
    if not url:
        return ApiStatusResponse(ok=False, message="원격 프롬프트 매니페스트 URL이 비어 있습니다.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            manifest = resp.json()
        prompts = manifest.get("prompts") if isinstance(manifest, dict) else manifest
        updated_count = 0
        for item in prompts or []:
            if isinstance(item, dict) and item.get("name") and item.get("system_prompt"):
                save_prompt(
                    str(item["name"]),
                    str(item["system_prompt"]),
                    str(item.get("description", "")),
                    str(item.get("version", "remote")),
                )
                updated_count += 1
        return ApiStatusResponse(
            ok=True,
            message=f"원격 프롬프트 {updated_count}개를 성공적으로 동기화했습니다.",
            details={"updated_count": updated_count},
        )
    except Exception as exc:
        return ApiStatusResponse(ok=False, message=f"원격 프롬프트 동기화 실패: {exc}")


@router.get("/settings/svi-rubric")
def get_svi_rubric() -> dict[str, Any]:
    return load_svi_rubric()


@router.get("/settings/data-path")
def get_runtime_data_path() -> dict[str, str]:
    return {"data_dir": str(get_data_dir())}


# --- 로컬 하이브리드 RAG 엔드포인트 ---


@router.get("/rag/status", response_model=RagStatus)
def rag_status() -> RagStatus:
    return get_rag_status()


@router.post("/rag/ingest", response_model=RagDocumentInfo)
async def rag_ingest_upload(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    doc_type: str = Form(default="reference"),
) -> RagDocumentInfo:
    fname = Path(file.filename or "doc.txt").name

    def _save_and_ingest() -> RagDocumentInfo:
        # 파일 저장·파싱·청크 임베딩(네트워크)은 동기 작업이므로 이벤트 루프 밖에서 실행
        dest_dir = get_downloads_dir() / "rag_sources"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fname
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return ingest_file(dest, title=title or fname, doc_type=doc_type)

    return await run_in_threadpool(_save_and_ingest)


@router.post("/rag/ingest-path", response_model=RagDocumentInfo)
def rag_ingest_by_path(req: RagIngestPathRequest) -> RagDocumentInfo:
    return ingest_file(req.file_path, title=req.title, doc_type=req.doc_type)


@router.post("/rag/search", response_model=list[RagSearchHit])
def rag_search(req: RagSearchRequest) -> list[RagSearchHit]:
    return hybrid_search(req.query, top_k=req.top_k)


@router.get("/settings/budget")
def get_llm_budget(kind: str = "", calls: int = 0, tier: str = "strong") -> dict[str, Any]:
    """이번 달 Live 사용액·예산과 (선택) 작업 대략 비용 추정. kind: section | remediate | embedding"""
    return get_budget_status(kind=kind, calls=max(0, min(calls, 100_000)), tier=tier)


@router.get("/settings/llm-cache")
def llm_cache_stats() -> dict[str, int]:
    return get_cache_stats()


@router.delete("/settings/llm-cache")
def llm_cache_clear() -> dict[str, int]:
    """임베딩·Vision OCR 캐시를 비운다(다음 호출부터 다시 과금됨). 삭제 전 항목 수를 반환."""
    return clear_caches()


@router.post("/rag/reindex", response_model=RagStatus)
def rag_reindex() -> RagStatus:
    ensure_budget_available()  # 예산 초과 시 402로 즉시 거절
    # 전체 청크 재임베딩은 비용이 크므로 동시 중복 실행을 차단한다.
    job_token = acquire_or_409("rag:reindex", "RAG 전체 재인덱싱")
    try:
        return reindex_all()
    finally:
        release("rag:reindex", job_token)


@router.delete("/rag/docs/{doc_id}")
def rag_delete_doc(doc_id: int) -> dict[str, bool]:
    ok = delete_rag_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="RAG 문서를 찾을 수 없습니다.")
    return {"ok": True}
