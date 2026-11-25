"""멀티 LLM 클라이언트 (Gemini / OpenAI 호환) + 토큰·원화 비용 추적 + 시뮬레이션 폴백."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx

from app.ai.llm_budget import BudgetExceededError, ensure_budget_available
from app.ai.llm_cache import (
    bytes_hash,
    get_cached_embeddings,
    get_cached_ocr,
    put_cached_embeddings,
    put_cached_ocr,
    text_hash,
)
from app.ai.model_catalog import get_model_capabilities, get_model_pricing
from app.config import effective_embedding_space, is_simulation_config, load_config
from app.db import get_conn
from app.schemas.settings import (
    RecalculateUsageResponse,
    TokenUsageByFeature,
    TokenUsageByModel,
    TokenUsageLogItem,
    TokenUsageReport,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    """한국어/영문 혼합 텍스트의 보수적 토큰 수를 추정한다."""
    if not text:
        return 0
    return max(1, int(len(text) / 2.2))


def record_token_usage(
    provider: str,
    model: str,
    feature: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
) -> dict[str, Any]:
    cfg = load_config()
    llm_cfg = cfg["llm"]
    usd_to_krw = float(llm_cfg.get("usd_to_krw", 1380.0))
    rates = get_model_pricing(model, cfg)
    is_emb = "embedding" in (model or "").lower()

    if rates is None:
        in_rate = 0.0
        out_rate = 0.0
        pricing_missing = 1
    else:
        in_rate, out_rate = rates
        pricing_missing = 1 if (in_rate == 0.0 and out_rate == 0.0 and not is_emb) else 0

    billable_output = int(output_tokens) + int(thinking_tokens)
    cost_usd = (int(input_tokens) / 1_000_000.0) * in_rate + (billable_output / 1_000_000.0) * out_rate
    cost_krw = cost_usd * usd_to_krw

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO token_usage (
                provider, model, feature, input_tokens, output_tokens, thinking_tokens,
                cost_usd, cost_krw, pricing_missing, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                model,
                feature,
                int(input_tokens),
                int(output_tokens),
                int(thinking_tokens),
                cost_usd,
                cost_krw,
                pricing_missing,
                _now_iso(),
            ),
        )
    return {
        "cost_usd": cost_usd,
        "cost_krw": cost_krw,
        "pricing_missing": bool(pricing_missing),
        "thinking_tokens": int(thinking_tokens),
    }


def _live_failure_fallback(simulation_text: str | None, exc: BaseException) -> str:
    """Live 호출 실패 시 대체 초안. 실제 AI 초안으로 오인해 제출하지 않도록 본문 맨 앞에 경고를 붙인다."""
    notice = (
        "> ⚠️ **Live AI 호출에 실패해 자동 템플릿 초안으로 대체되었습니다.** "
        f"제출 전 반드시 내용을 검토하거나 다시 생성하세요. (오류: {str(exc)[:200]})\n\n"
    )
    return notice + (simulation_text or "")


def is_simulation_active(cfg: dict[str, Any] | None = None) -> bool:
    return is_simulation_config(cfg or load_config())


class EmbeddingUnavailableError(RuntimeError):
    """Live 임베딩 API 호출 실패. 로컬 해시 벡터로 몰래 대체하면 인덱스가 오염되므로 호출자에게 알린다."""


def deterministic_embedding(text: str, dim: int = 768) -> list[float]:
    """시뮬레이션/오프라인 모드에서 동작하는 한국어 2-gram + 해시 기반 정규화 임베딩."""
    vec = [0.0] * dim
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        vec[0] = 1.0
        return vec

    tokens: list[str] = cleaned.split(" ")
    for word in list(tokens):
        if len(word) >= 2:
            for i in range(len(word) - 1):
                tokens.append(word[i : i + 2])

    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        val = int.from_bytes(digest[:4], "little")
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        idx = val % dim
        vec[idx] += sign

    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-9:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def _extract_gemini_usage(resp: Any, prompt_text: str, out_text: str) -> tuple[int, int, int, bool]:
    meta = getattr(resp, "usage_metadata", None)
    if meta is not None:
        prompt_tok = getattr(meta, "prompt_token_count", None)
        cand_tok = getattr(meta, "candidates_token_count", None)
        thought_tok = getattr(meta, "thoughts_token_count", None) or 0
        from_api_meta = (prompt_tok is not None) or (cand_tok is not None)
        in_tok = int(prompt_tok if prompt_tok is not None else estimate_tokens(prompt_text))
        out_tok = int(cand_tok if cand_tok is not None else estimate_tokens(out_text))
        return in_tok, out_tok, int(thought_tok), from_api_meta
    return estimate_tokens(prompt_text), estimate_tokens(out_text), 0, False


def _build_gemini_config(
    model: str,
    system_prompt: str = "",
    *,
    tier: str = "strong",
    json_mode: bool = False,
    temperature: float | None = None,
) -> Any:
    """모델 세대별 파라미터 제약(temperature 제거·thinking_level 전환)에 맞춘 GenerateContentConfig를 생성한다."""
    from google.genai import types

    caps = get_model_capabilities(model)
    kwargs: dict[str, Any] = {}
    if system_prompt:
        kwargs["system_instruction"] = system_prompt
    if json_mode:
        kwargs["response_mime_type"] = "application/json"
    if caps["supports_temperature"] and temperature is not None:
        kwargs["temperature"] = temperature
    if caps["supports_thinking_level"]:
        allowed = set(caps.get("allowed_thinking_levels") or [])
        if json_mode:
            level = "MINIMAL" if "minimal" in allowed else "LOW"
        elif tier == "fast":
            level = "LOW"
        else:
            level = "MEDIUM"
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level)
    return types.GenerateContentConfig(**kwargs)


async def stream_text(
    system_prompt: str,
    user_prompt: str,
    feature: str,
    tier: str = "strong",
    simulation_text: str | None = None,
) -> AsyncIterator[str]:
    """LLM 스트리밍 생성기. 실 API 키 미설정/시뮬레이션 모드 시 컨텍스트 기반 고품질 초안을 스트리밍한다."""
    cfg = load_config()
    llm = cfg["llm"]
    provider = str(llm.get("provider", "gemini"))
    default_model = "gemini-3.8-flash" if tier == "strong" else "gemini-3.5-flash-lite"
    model = str(llm.get("strong_model" if tier == "strong" else "fast_model", default_model))
    api_key = str(llm.get("api_key") or "").strip()

    if is_simulation_active(cfg):
        text = simulation_text or (
            "### 초안 생성 결과 (시뮬레이션 모드)\n\n"
            "설정에서 Gemini API 키를 등록하고 시뮬레이션 모드를 해제하면 실시간 LLM이 호출됩니다.\n\n"
            f"{user_prompt[:400]}"
        )
        chunks = re.findall(r".{1,18}(?:\s+|$)", text, flags=re.DOTALL)
        for ch in chunks:
            if ch:
                yield ch
                await asyncio.sleep(0.015)
        record_token_usage(
            "simulation",
            model,
            feature,
            estimate_tokens(system_prompt + user_prompt),
            estimate_tokens(text),
            0,
        )
        return

    # 월 예산 초과 시 Live 호출을 시작하지 않는다(BudgetExceededError는 SSE 라우트에서 error 이벤트로 변환).
    ensure_budget_available(cfg)

    if provider == "gemini":
        from google import genai

        # 비동기 루프에서 블로킹을 피하면서 순차 청크 전송
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        # 소비자(SSE 응답)가 중지·연결 끊김으로 사라지면 set되어 Gemini 스트림 수신을 즉시 중단한다.
        cancel_event = threading.Event()

        def _emit(item: str | None) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                # 이벤트 루프가 이미 종료된 경우(앱 종료 중) 무시
                pass

        def _producer() -> None:
            stream: Any = None
            last_chunk = None
            full_out_parts: list[str] = []
            try:
                client = genai.Client(api_key=api_key)
                gen_cfg = _build_gemini_config(
                    model,
                    system_prompt,
                    tier=tier,
                    temperature=0.35,
                )
                stream = client.models.generate_content_stream(
                    model=model,
                    contents=user_prompt,
                    config=gen_cfg,
                )
                for chunk in stream:
                    last_chunk = chunk
                    if cancel_event.is_set():
                        break
                    txt = getattr(chunk, "text", None)
                    if txt:
                        full_out_parts.append(txt)
                        _emit(txt)
                if cancel_event.is_set():
                    logger.info("Gemini stream cancelled by client (feature=%s)", feature)
            except Exception as exc:
                if not cancel_event.is_set():
                    logger.exception("Gemini stream error: %s", exc)
                    if full_out_parts:
                        # 이미 일부 본문이 전송된 경우 시뮬레이션 전문을 덧붙이지 않는다(본문 중복 방지).
                        _emit(f"\n\n[API 오류로 생성이 중단되었습니다: {exc}]")
                    else:
                        _emit(_live_failure_fallback(simulation_text, exc))
            finally:
                close_fn = getattr(stream, "close", None)
                if callable(close_fn):
                    try:
                        # 제너레이터를 닫아 HTTP 스트리밍 연결을 끊어 추가 토큰 생성을 멈춘다.
                        close_fn()
                    except Exception:
                        pass
                if stream is not None:
                    # 중단된 경우에도 이미 소비된 토큰은 사용량에 기록한다.
                    try:
                        in_t, out_t, thought_t, _ = _extract_gemini_usage(
                            last_chunk, system_prompt + user_prompt, "".join(full_out_parts)
                        )
                        record_token_usage("gemini", model, feature, in_t, out_t, thought_t)
                    except Exception as rec_exc:
                        logger.warning("token usage record failed: %s", rec_exc)
                _emit(None)

        task = asyncio.create_task(asyncio.to_thread(_producer))
        finished = False
        try:
            while True:
                item = await queue.get()
                if item is None:
                    finished = True
                    break
                yield item
        finally:
            if not finished:
                # CancelledError(연결 끊김) 또는 aclose()(중지)로 빠져나온 경우: 생산자 스레드에 중단 신호
                cancel_event.set()
        await task
        return

    # OpenAI 호환 엔드포인트
    base_url = str(llm.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    full_text = ""
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.35,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            full_text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            record_token_usage(
                "openai_compat",
                model,
                feature,
                int(usage.get("prompt_tokens") or estimate_tokens(system_prompt + user_prompt)),
                int(usage.get("completion_tokens") or estimate_tokens(full_text)),
            )
    except Exception as exc:
        logger.warning("OpenAI-compat call failed: %s", exc)
        full_text = _live_failure_fallback(simulation_text, exc)

    for i in range(0, len(full_text), 20):
        yield full_text[i : i + 20]
        await asyncio.sleep(0.01)


async def generate_json(
    system_prompt: str,
    user_prompt: str,
    feature: str,
    tier: str = "fast",
    simulation_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """구조화된 JSON 응답을 반환한다."""
    cfg = load_config()
    llm = cfg["llm"]
    api_key = str(llm.get("api_key") or "").strip()
    default_model = "gemini-3.8-flash" if tier == "strong" else "gemini-3.5-flash-lite"
    model = str(llm.get("strong_model" if tier == "strong" else "fast_model", default_model))

    if is_simulation_active(cfg):
        payload = simulation_payload or {}
        record_token_usage(
            "simulation",
            model,
            feature,
            estimate_tokens(system_prompt + user_prompt),
            estimate_tokens(json.dumps(payload, ensure_ascii=False)),
            0,
        )
        return payload

    try:
        ensure_budget_available(cfg)
    except BudgetExceededError as exc:
        # JSON 평가(공고 채점 등)는 휴리스틱 폴백이 있으므로 흐름을 끊지 않고 폴백한다.
        logger.warning("Budget exceeded, JSON generation uses fallback (%s): %s", feature, exc)
        return simulation_payload or {}

    if llm.get("provider", "gemini") == "gemini":
        from google import genai

        def _call() -> dict[str, Any]:
            client = genai.Client(api_key=api_key)
            gen_cfg = _build_gemini_config(
                model,
                system_prompt,
                tier=tier,
                json_mode=True,
                temperature=0.2,
            )
            resp = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=gen_cfg,
            )
            txt = getattr(resp, "text", None) or "{}"
            in_t, out_t, thought_t, _ = _extract_gemini_usage(resp, system_prompt + user_prompt, txt)
            record_token_usage("gemini", model, feature, in_t, out_t, thought_t)
            return json.loads(txt)

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            logger.warning("Gemini JSON generation failed, using fallback: %s", exc)
            return simulation_payload or {}

    # OpenAI 호환 provider는 JSON 모드를 아직 지원하지 않는다. 조용히 넘어가지 않도록 경고를 남긴다.
    logger.warning(
        "generate_json: provider '%s'는 JSON 생성을 지원하지 않아 휴리스틱 결과를 사용합니다 (feature=%s).",
        llm.get("provider"), feature,
    )
    return simulation_payload or {}


def embed_texts(texts: list[str], model: str | None = None, dim: int | None = None) -> list[list[float]]:
    """현재 임베딩 공간(effective_embedding_space)의 벡터를 반환한다.

    - 시뮬레이션·비 Gemini 모드: 로컬 해시 임베딩(과금 없음).
    - Live Gemini: Gemini Embedding API(Matryoshka 차원 축소). (텍스트 해시, 모델, 차원) 기준 캐시로 동일 텍스트는 재과금하지 않는다.
      API 실패 시 EmbeddingUnavailableError를 던진다. 로컬 해시 벡터로 몰래 대체하면 서로 다른 벡터 공간이
      한 인덱스에 섞여 의미 검색이 조용히 망가지기 때문이다.
    - 월 예산 초과 시 캐시에 없는 텍스트가 있으면 BudgetExceededError를 던진다.
    """
    if not texts:
        return []
    cfg = load_config()
    llm = cfg["llm"]
    space_model, space_dim = effective_embedding_space(cfg)
    target_dim = int(dim or space_dim)
    from app.config import LOCAL_HASH_EMBEDDING_SPACE

    if space_model == LOCAL_HASH_EMBEDDING_SPACE:
        return [deterministic_embedding(t, target_dim) for t in texts]

    target_model = model or space_model
    api_key = str(llm.get("api_key") or "").strip()

    # 실제로 임베딩되는 범위(최대 3,200자)를 기준으로 해시
    clipped = [t[:3200] for t in texts]
    hashes = [text_hash(t) for t in clipped]
    cached = get_cached_embeddings(hashes, target_model, target_dim)

    # 캐시에 없는 고유 텍스트만 API로 요청
    miss_hashes: list[str] = []
    miss_texts: list[str] = []
    for h, t in zip(hashes, clipped):
        if h not in cached and h not in miss_hashes:
            miss_hashes.append(h)
            miss_texts.append(t)

    if miss_texts:
        ensure_budget_available(cfg)
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            # 최대 2,048토큰 안전 배치 처리
            for i in range(0, len(miss_texts), 16):
                batch = miss_texts[i : i + 16]
                batch_hashes = miss_hashes[i : i + 16]
                resp = client.models.embed_content(
                    model=target_model,
                    contents=batch,
                    config=types.EmbedContentConfig(output_dimensionality=target_dim),
                )
                batch_vecs = [[float(x) for x in emb.values] for emb in resp.embeddings]
                if len(batch_vecs) != len(batch) or any(len(v) != target_dim for v in batch_vecs):
                    raise ValueError(
                        f"임베딩 응답 형식 불일치(요청 {len(batch)}건/응답 {len(batch_vecs)}건, 기대 차원 {target_dim})"
                    )
                total_in = sum(estimate_tokens(t) for t in batch)
                record_token_usage("gemini", target_model, "embedding", total_in, 0)
                batch_fresh = dict(zip(batch_hashes, batch_vecs))
                # 배치마다 즉시 저장: 중간 실패 시에도 이미 과금된 결과는 재사용
                put_cached_embeddings(batch_fresh, target_model, target_dim)
                cached.update(batch_fresh)
        except BudgetExceededError:
            raise
        except Exception as exc:
            logger.warning("Gemini embed_content failed (%d texts): %s", len(miss_texts), exc)
            raise EmbeddingUnavailableError(f"임베딩 API 호출 실패({target_model}): {exc}") from exc

    return [cached[h] for h in hashes]


def vision_ocr_page(image_bytes: bytes, page_num: int) -> str:
    """스캔 PDF 페이지를 멀티모달 Vision LLM으로 마크다운 텍스트/표로 변환한다."""
    cfg = load_config()
    llm = cfg["llm"]
    api_key = str(llm.get("api_key") or "").strip()
    model = str(llm.get("fast_model", "gemini-3.5-flash-lite"))

    if is_simulation_active(cfg) or llm.get("provider", "gemini") != "gemini":
        return f"[시뮬레이션 OCR - 페이지 {page_num}] 스캔 이미지가 감지되었습니다. Gemini API 키를 연결하면 표와 본문을 정밀 판독합니다."

    # 동일 페이지 이미지(같은 파일 재업로드)는 캐시된 판독 결과를 재사용한다.
    img_hash = bytes_hash(image_bytes)
    cached_text = get_cached_ocr(img_hash, model)
    if cached_text is not None:
        return cached_text

    try:
        ensure_budget_available(cfg)
    except BudgetExceededError as exc:
        logger.warning("Vision OCR skipped on page %d: %s", page_num, exc)
        return f"[월 LLM 예산 초과로 Vision OCR 생략 - 페이지 {page_num}]"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = (
            f"이 이미지는 한국 공공기관 RFP/문서의 {page_num}페이지입니다. "
            "모든 텍스트와 표를 누락 없이 한국어 마크다운으로 변환하세요. 표는 반드시 마크다운 표(| ... |)로 작성하세요."
        )
        part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
        gen_cfg = _build_gemini_config(model, "", tier="fast")
        resp = client.models.generate_content(model=model, contents=[prompt, part], config=gen_cfg)
        txt = getattr(resp, "text", None) or ""
        in_t, out_t, thought_t, from_api_meta = _extract_gemini_usage(resp, prompt, txt)
        billed_in = in_t if from_api_meta else (in_t + 258)
        record_token_usage("gemini", model, "vision_ocr", billed_in, out_t, thought_t)
        if txt.strip():
            put_cached_ocr(img_hash, model, txt)
        return txt
    except Exception as exc:
        logger.warning("Vision OCR failed on page %d: %s", page_num, exc)
        return f"[Vision OCR 오류 - 페이지 {page_num}: {exc}]"


async def test_gemini_connection() -> dict[str, Any]:
    """설정 화면 [API 통신 테스트]용 Gemini 연결 검증."""
    cfg = load_config()
    llm_cfg = cfg["llm"]
    api_key = str(llm_cfg.get("api_key") or "").strip()
    model = str(llm_cfg.get("fast_model", "gemini-3.5-flash-lite"))
    strong_model = str(llm_cfg.get("strong_model", ""))
    sim_mode = bool(llm_cfg.get("simulation_mode", True))
    tested_at = _now_iso()

    if not api_key:
        return {
            "ok": False,
            "message": "비정상 — Gemini API 키가 비어 있습니다.",
            "details": {
                "model": model,
                "strong_model": strong_model,
                "simulation_mode": sim_mode,
                "tested_at": tested_at,
            },
        }

    start = time.perf_counter()
    try:
        from google import genai

        def _ping() -> str:
            client = genai.Client(api_key=api_key)
            gen_cfg = _build_gemini_config(model, "", tier="fast")
            resp = client.models.generate_content(
                model=model,
                contents="응답 확인: OK만 출력하세요.",
                config=gen_cfg,
            )
            return (getattr(resp, "text", "") or "").strip()

        reply = await asyncio.wait_for(asyncio.to_thread(_ping), timeout=20.0)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": True,
            "message": f"정상 연결됨 ({model}, {elapsed_ms}ms) — 응답: {reply[:40]}",
            "details": {
                "model": model,
                "strong_model": strong_model,
                "elapsed_ms": elapsed_ms,
                "simulation_mode": sim_mode,
                "tested_at": tested_at,
            },
        }
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": False,
            "message": f"비정상 (응답 시간 초과 20초 · {model}): 네트워크 또는 API 서버 응답이 지연되고 있습니다.",
            "details": {
                "model": model,
                "strong_model": strong_model,
                "elapsed_ms": elapsed_ms,
                "simulation_mode": sim_mode,
                "tested_at": tested_at,
            },
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        err_text = str(exc)
        low_err = err_text.lower()
        hint = ""
        if any(k in low_err for k in ("404", "not found", "not_found", "deprecated", "not supported")):
            hint = " (모델이 종료되었거나 지원되지 않을 수 있습니다. [공식 사이트 모델·단가 최신화]를 실행해 대체 모델을 확인하세요.)"
        return {
            "ok": False,
            "message": f"비정상 (연결 실패 · {model}): {err_text}{hint}",
            "details": {
                "model": model,
                "strong_model": strong_model,
                "elapsed_ms": elapsed_ms,
                "simulation_mode": sim_mode,
                "tested_at": tested_at,
            },
        }


def get_token_usage_report(scope: str = "month") -> TokenUsageReport:
    """월간 또는 전체 기간 토큰 사용량과 모델·기능별 비용 집계를 반환한다."""
    cfg = load_config()
    usd_to_krw = float(cfg.get("llm", {}).get("usd_to_krw", 1380.0))
    use_all = scope == "all"
    month_prefix = datetime.now().strftime("%Y-%m")

    where_clause = "" if use_all else "WHERE created_at LIKE ?"
    params: tuple[Any, ...] = () if use_all else (f"{month_prefix}%",)

    with get_conn() as conn:
        summary_row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_calls,
                COALESCE(SUM(CASE WHEN provider != 'simulation' THEN 1 ELSE 0 END), 0) AS live_calls,
                COALESCE(SUM(CASE WHEN provider = 'simulation' THEN 1 ELSE 0 END), 0) AS simulation_calls,
                COALESCE(SUM(CASE WHEN pricing_missing = 1 THEN 1 ELSE 0 END), 0) AS unpriced_calls,
                COALESCE(SUM(input_tokens), 0) AS in_toks,
                COALESCE(SUM(output_tokens), 0) AS out_toks,
                COALESCE(SUM(thinking_tokens), 0) AS thought_toks,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                COALESCE(SUM(cost_krw), 0.0) AS cost_krw,
                COALESCE(SUM(CASE WHEN provider != 'simulation' THEN cost_krw ELSE 0.0 END), 0.0) AS live_krw,
                COALESCE(SUM(CASE WHEN provider = 'simulation' THEN cost_krw ELSE 0.0 END), 0.0) AS sim_krw
            FROM token_usage
            {where_clause}
            """,
            params,
        ).fetchone()

        model_rows = conn.execute(
            f"""
            SELECT
                model,
                provider,
                COUNT(*) AS calls,
                COALESCE(SUM(input_tokens), 0) AS in_toks,
                COALESCE(SUM(output_tokens), 0) AS out_toks,
                COALESCE(SUM(thinking_tokens), 0) AS thought_toks,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                COALESCE(SUM(cost_krw), 0.0) AS cost_krw,
                MAX(pricing_missing) AS any_missing
            FROM token_usage
            {where_clause}
            GROUP BY model, provider
            ORDER BY cost_krw DESC, (in_toks + out_toks + thought_toks) DESC
            """,
            params,
        ).fetchall()

        feature_rows = conn.execute(
            f"""
            SELECT
                feature,
                COUNT(*) AS calls,
                COALESCE(SUM(input_tokens + output_tokens + thinking_tokens), 0) AS total_toks,
                COALESCE(SUM(cost_krw), 0.0) AS cost_krw
            FROM token_usage
            {where_clause}
            GROUP BY feature
            ORDER BY cost_krw DESC, total_toks DESC
            LIMIT 12
            """,
            params,
        ).fetchall()

        log_rows = conn.execute(
            f"""
            SELECT id, provider, model, feature, input_tokens, output_tokens, thinking_tokens,
                   cost_usd, cost_krw, pricing_missing, created_at
            FROM token_usage
            {where_clause}
            ORDER BY id DESC
            LIMIT 10
            """,
            params,
        ).fetchall()

    by_model: list[TokenUsageByModel] = []
    for r in model_rows:
        mid = str(r["model"])
        rates = get_model_pricing(mid, cfg)
        is_emb = "embedding" in mid.lower()
        has_valid_rate = rates is not None and (rates[0] > 0.0 or rates[1] > 0.0 or is_emb)
        in_tok = int(r["in_toks"])
        out_tok = int(r["out_toks"])
        thought_tok = int(r["thought_toks"])
        by_model.append(
            TokenUsageByModel(
                model=mid,
                provider=str(r["provider"]),
                calls=int(r["calls"]),
                input_tokens=in_tok,
                output_tokens=out_tok,
                thinking_tokens=thought_tok,
                total_tokens=in_tok + out_tok + thought_tok,
                cost_usd=round(float(r["cost_usd"]), 6),
                cost_krw=round(float(r["cost_krw"]), 2),
                has_pricing=bool(has_valid_rate and not bool(r["any_missing"])),
                input_rate=rates[0] if rates is not None else None,
                output_rate=rates[1] if rates is not None else None,
            )
        )

    by_feature = [
        TokenUsageByFeature(
            feature=str(r["feature"]),
            calls=int(r["calls"]),
            total_tokens=int(r["total_toks"]),
            cost_krw=round(float(r["cost_krw"]), 2),
        )
        for r in feature_rows
    ]

    recent_logs = [
        TokenUsageLogItem(
            id=int(r["id"]),
            provider=str(r["provider"]),
            model=str(r["model"]),
            feature=str(r["feature"]),
            input_tokens=int(r["input_tokens"]),
            output_tokens=int(r["output_tokens"]),
            thinking_tokens=int(r["thinking_tokens"]),
            cost_usd=round(float(r["cost_usd"]), 6),
            cost_krw=round(float(r["cost_krw"]), 2),
            pricing_missing=bool(r["pricing_missing"]),
            created_at=str(r["created_at"]),
        )
        for r in log_rows
    ]

    in_sum = int(summary_row["in_toks"])
    out_sum = int(summary_row["out_toks"])
    thought_sum = int(summary_row["thought_toks"])
    return TokenUsageReport(
        scope="all" if use_all else "month",
        usd_to_krw=usd_to_krw,
        total_calls=int(summary_row["total_calls"]),
        live_calls=int(summary_row["live_calls"]),
        simulation_calls=int(summary_row["simulation_calls"]),
        unpriced_calls=int(summary_row["unpriced_calls"]),
        input_tokens=in_sum,
        output_tokens=out_sum,
        thinking_tokens=thought_sum,
        total_tokens=in_sum + out_sum + thought_sum,
        cost_usd=round(float(summary_row["cost_usd"]), 6),
        cost_krw=round(float(summary_row["cost_krw"]), 2),
        live_cost_krw=round(float(summary_row["live_krw"]), 2),
        simulation_cost_krw=round(float(summary_row["sim_krw"]), 2),
        by_model=by_model,
        by_feature=by_feature,
        recent_logs=recent_logs,
    )


def recalculate_token_usage_costs() -> RecalculateUsageResponse:
    """현재 단가표와 환율을 기준으로 기존 token_usage 기록의 비용을 재계산한다."""
    cfg = load_config()
    usd_to_krw = float(cfg.get("llm", {}).get("usd_to_krw", 1380.0))
    updated_rows = 0
    still_unpriced = 0
    total_krw = 0.0

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, model, input_tokens, output_tokens, thinking_tokens, cost_usd, cost_krw, pricing_missing FROM token_usage"
        ).fetchall()
        for r in rows:
            mid = str(r["model"])
            rates = get_model_pricing(mid, cfg)
            is_emb = "embedding" in mid.lower()
            if rates is None or (rates[0] == 0.0 and rates[1] == 0.0 and not is_emb):
                new_usd = 0.0
                new_krw = 0.0
                new_missing = 1
                still_unpriced += 1
            else:
                in_r, out_r = rates
                billable_out = int(r["output_tokens"]) + int(r["thinking_tokens"])
                new_usd = (int(r["input_tokens"]) / 1_000_000.0) * in_r + (billable_out / 1_000_000.0) * out_r
                new_krw = new_usd * usd_to_krw
                new_missing = 0

            total_krw += new_krw
            if (
                abs(float(r["cost_usd"]) - new_usd) > 1e-9
                or abs(float(r["cost_krw"]) - new_krw) > 1e-6
                or int(r["pricing_missing"]) != new_missing
            ):
                conn.execute(
                    "UPDATE token_usage SET cost_usd = ?, cost_krw = ?, pricing_missing = ? WHERE id = ?",
                    (new_usd, new_krw, new_missing, int(r["id"])),
                )
                updated_rows += 1

    msg = f"총 {updated_rows}건의 사용량 비용을 현재 단가·환율 기준으로 재계산했습니다."
    if still_unpriced > 0:
        msg += f" (단가 미등록 {still_unpriced}건 유지)"
    return RecalculateUsageResponse(
        ok=True,
        updated_rows=updated_rows,
        still_unpriced_rows=still_unpriced,
        total_cost_krw=round(total_krw, 2),
        message=msg,
    )
