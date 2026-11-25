"""외부 설정(%APPDATA%/SocialBidAI), 마스터 프로필, 프롬프트 YAML 및 SVI 기준 관리."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app import APP_NAME


def get_resource_dir() -> Path:
    """번들 정적 리소스(defaults, frontend/ui_dist) 루트 경로를 반환한다."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def get_data_dir() -> Path:
    """고객사 가변 데이터(%APPDATA%/SocialBidAI) 디렉터리를 반환하고 생성한다."""
    override = os.environ.get("SOCIALBID_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        base = Path(xdg) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_downloads_dir() -> Path:
    d = get_data_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_exports_dir() -> Path:
    d = get_data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_prompts_dir() -> Path:
    d = get_data_dir() / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path() -> Path:
    return get_data_dir() / "config.json"


def get_profile_path() -> Path:
    return get_data_dir() / "company_profile.json"


def get_svi_rubric_path() -> Path:
    return get_data_dir() / "svi_rubric.json"


def get_db_path() -> Path:
    return get_data_dir() / "app.db"


def get_model_catalog_path() -> Path:
    return get_data_dir() / "model_catalog.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "gemini",
        "api_key": "",
        "base_url": "",
        "fast_model": "gemini-3.5-flash-lite",
        "strong_model": "gemini-3.8-flash",
        "embedding_model": "gemini-embedding-001",
        "embedding_dim": 768,
        "simulation_mode": True,
        "usd_to_krw": 1380.0,
        # 월간 Live API 비용 상한(원). 0이면 무제한. 초과 시 신규 LLM 호출을 차단한다.
        "monthly_budget_krw": 0.0,
        "pricing_per_1m": {
            "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
            "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
            "gemini-4-hydrogen": {"input": 1.50, "output": 7.50},
            "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
            "gemini-4-neon": {"input": 3.00, "output": 15.00},
            "gemini-4-carbon": {"input": 2.00, "output": 10.00},
            "gemini-4-argon": {"input": 2.00, "output": 10.00},
            "gemini-embedding-001": {"input": 0.15, "output": 0.0},
            "gemini-embedding-2": {"input": 0.20, "output": 0.0},
            "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
            "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
        },
    },
    "public_api": {
        "g2b_service_key": "",
        "g2b_base_url": "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02",
        "seis_service_key": "",
        "seis_endpoint_url": "https://apis.data.go.kr/B552465/integratedSupportNotice/getNoticeList",
        "seis_extra_params": {},
        "bizinfo_api_key": "",
        "bizinfo_endpoint_url": "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do",
        "bizinfo_hashtags": "사회적기업,ESG,돌봄,친환경,교육",
        "daily_limits": {"g2b": 1000, "seis": 500, "bizinfo": 1000},
        "g2b_max_pages": 10,
    },
    "remote_prompts_manifest_url": "",
}


DEFAULT_PROFILE: dict[str, Any] = {
    # 시연용 가상 회사. 사용자가 프로필을 한 번이라도 저장하면 is_sample=False가 되고,
    # 그 전까지는 화면·내보내기에 "샘플 회사 정보" 경고를 표시한다.
    "is_sample": True,
    "name": "(주)함께자람 사회적협동조합",
    "ceo": "김나눔",
    "biz_reg_no": "123-82-00456",
    # 인증 사회적기업(certifications에도 인증 보유). 예전엔 '예비사회적기업'인데 is_certified_social=True로 모순
    "social_type": "인증 사회적기업(일자리 제공형)",
    "is_certified_social": True,
    "vulnerable_employment_ratio": 38.5,
    "is_women_owned": False,
    "is_disabled_owned": False,
    "industry_codes": ["1469", "1468", "1162", "5312"],
    "region": "서울특별시",
    "include_nationwide": True,
    "min_bid_amount": 10_000_000,
    "max_bid_amount": 300_000_000,
    "certifications": [
        "사회적기업 인증(고용노동부)",
        "직접생산확인증명서(소프트웨어유지보수/전산업무)",
        "여성·장애인친화 우수사업장",
    ],
    "direct_production_items": [
        "전산업무(소프트웨어개발)",
        "인쇄물·홍보물 제작",
        "교육훈련 위탁서비스",
    ],
    "svi_current_score": 78.0,
    "svi_target_score": 88.0,
    "summary": (
        "취약계층(경력보유여성·고령자) 38.5%를 정규직으로 고용하여 공공기관·지자체 대상 "
        "디지털 돌봄·지역사회 교육 운영 및 친환경 로컬 패키징·홍보물 제작을 수행하는 사회적기업입니다."
    ),
    "past_projects": [
        {
            "year": "2025",
            "client": "서울특별시 성동구청",
            "title": "어르신 디지털 역량강화 및 돌봄 매니저 파견 용역",
            "amount": 48_000_000,
            "outcome": "만족도 96.4점, 취약계층 일자리 6명 신규 창출",
        },
        {
            "year": "2024",
            "client": "한국사회적기업진흥원",
            "title": "사회적경제 판로지원 ESG 키트 패키지 제작 납품",
            "amount": 64_000_000,
            "outcome": "기한 내 100% 납품, 재계약 체결",
        },
    ],
}


def atomic_write_text(path: Path, text: str) -> None:
    """임시 파일에 기록한 뒤 원자적으로 교체하여 설정 손상을 방지한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def seed_defaults() -> None:
    """최초 실행 시 번들된 기본 설정·SVI 루브릭·프롬프트 YAML을 데이터 폴더에 복사한다."""
    res_dir = get_resource_dir() / "defaults"

    if not get_config_path().exists():
        atomic_write_json(get_config_path(), DEFAULT_CONFIG)

    if not get_profile_path().exists():
        atomic_write_json(get_profile_path(), DEFAULT_PROFILE)

    rubric_src = res_dir / "svi_rubric.json"
    rubric_dst = get_svi_rubric_path()
    if rubric_src.exists() and not rubric_dst.exists():
        shutil.copy2(rubric_src, rubric_dst)

    prompts_src = res_dir / "prompts"
    prompts_dst = get_prompts_dir()
    if prompts_src.exists():
        for yaml_file in prompts_src.glob("*.yaml"):
            target = prompts_dst / yaml_file.name
            if not target.exists():
                shutil.copy2(yaml_file, target)

    get_downloads_dir()
    get_exports_dir()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# 시뮬레이션·비 Gemini 모드에서 쓰는 로컬 해시 임베딩(deterministic_embedding)의 벡터 공간 식별자.
# 실제 Gemini 임베딩과 수치적으로 비교 불가능하므로 rag_meta에 이 이름으로 구분 기록한다.
LOCAL_HASH_EMBEDDING_SPACE = "local-hash-2gram-v1"


def is_simulation_config(cfg: dict[str, Any]) -> bool:
    llm = cfg.get("llm", {})
    return bool(llm.get("simulation_mode", True)) or not bool((llm.get("api_key") or "").strip())


def effective_embedding_space(cfg: dict[str, Any] | None = None) -> tuple[str, int]:
    """현재 설정에서 실제로 생성될 임베딩 벡터 공간(모델 식별자, 차원)을 반환한다.

    설정의 embedding_model 이름이 같더라도 시뮬레이션(로컬 해시)과 Live Gemini 벡터는 서로 다른 공간이므로
    RAG 인덱스 호환성 판단(needs_reindex·벡터 검색 사용 여부)은 반드시 이 값으로 해야 한다.
    """
    cfg = cfg or load_config()
    llm = cfg.get("llm", {})
    dim = int(llm.get("embedding_dim", 768))
    if is_simulation_config(cfg) or llm.get("provider", "gemini") != "gemini":
        return LOCAL_HASH_EMBEDDING_SPACE, dim
    return str(llm.get("embedding_model", "gemini-embedding-001")), dim


def load_config() -> dict[str, Any]:
    seed_defaults()
    raw = json.loads(get_config_path().read_text(encoding="utf-8"))
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    seed_defaults()
    merged = _deep_merge(DEFAULT_CONFIG, cfg)
    atomic_write_json(get_config_path(), merged)
    return merged


def load_profile() -> dict[str, Any]:
    seed_defaults()
    raw = json.loads(get_profile_path().read_text(encoding="utf-8"))
    if "is_sample" not in raw:
        # 구버전 프로필 파일: 샘플 회사의 이름·사업자번호를 그대로 쓰는 경우에만 샘플로 간주
        raw["is_sample"] = (
            raw.get("name") == DEFAULT_PROFILE["name"] and raw.get("biz_reg_no") == DEFAULT_PROFILE["biz_reg_no"]
        )
    return _deep_merge(DEFAULT_PROFILE, raw)


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    seed_defaults()
    merged = _deep_merge(DEFAULT_PROFILE, profile)
    atomic_write_json(get_profile_path(), merged)
    return merged


def load_svi_rubric() -> dict[str, Any]:
    seed_defaults()
    path = get_svi_rubric_path()
    if not path.exists():
        path = get_resource_dir() / "defaults" / "svi_rubric.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_prompt(name: str) -> dict[str, Any]:
    """프롬프트 YAML을 데이터 디렉터리에서 우선 로드한다."""
    seed_defaults()
    fname = name if name.endswith(".yaml") else f"{name}.yaml"
    path = get_prompts_dir() / fname
    if not path.exists():
        fallback = get_resource_dir() / "defaults" / "prompts" / fname
        if fallback.exists():
            path = fallback
        else:
            return {"name": name, "version": "1.0", "description": "", "system_prompt": ""}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data


def list_prompts() -> list[dict[str, Any]]:
    seed_defaults()
    items = []
    for p in sorted(get_prompts_dir().glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        items.append(
            {
                "name": p.stem,
                "version": str(data.get("version", "1.0")),
                "description": str(data.get("description", "")),
                "system_prompt": str(data.get("system_prompt", "")),
            }
        )
    return items


def save_prompt(name: str, system_prompt: str, description: str = "", version: str = "2026.1") -> dict[str, Any]:
    seed_defaults()
    clean_name = Path(name).stem
    existing = load_prompt(clean_name)
    payload = {
        "name": clean_name,
        "version": version or existing.get("version", "2026.1"),
        "description": description or existing.get("description", ""),
        "system_prompt": system_prompt,
    }
    target = get_prompts_dir() / f"{clean_name}.yaml"
    atomic_write_text(target, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return payload


def get_profile_presets_dir() -> Path:
    d = get_data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify_preset(name: str) -> str:
    import hashlib
    import re

    ascii_part = re.sub(r"[^a-zA-Z0-9_-]", "", name.strip().replace(" ", "_")).strip("_")
    digest = hashlib.blake2b(name.strip().encode("utf-8"), digest_size=4).hexdigest()
    return f"{ascii_part}_{digest}" if ascii_part else f"client_{digest}"


def list_profile_presets() -> list[dict[str, Any]]:
    seed_defaults()
    presets_dir = get_profile_presets_dir()
    items: list[dict[str, Any]] = []
    for p in sorted(presets_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            prof = data.get("profile") or {}
            items.append(
                {
                    "slug": p.stem,
                    "preset_name": str(data.get("preset_name") or prof.get("name") or p.stem),
                    "company_name": str(prof.get("name") or ""),
                    "social_type": str(prof.get("social_type") or ""),
                    "region": str(prof.get("region") or ""),
                    "svi_current_score": float(prof.get("svi_current_score") or 0.0),
                    "updated_at": str(data.get("updated_at") or ""),
                }
            )
        except Exception:
            continue
    return items


def save_profile_preset(preset_name: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone

    seed_defaults()
    prof = _deep_merge(DEFAULT_PROFILE, profile or load_profile())
    clean_name = (preset_name or prof.get("name") or "고객사").strip()
    slug = _slugify_preset(clean_name)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {"slug": slug, "preset_name": clean_name, "updated_at": now, "profile": prof}
    atomic_write_json(get_profile_presets_dir() / f"{slug}.json", payload)
    return {
        "slug": slug,
        "preset_name": clean_name,
        "company_name": str(prof.get("name") or ""),
        "social_type": str(prof.get("social_type") or ""),
        "region": str(prof.get("region") or ""),
        "svi_current_score": float(prof.get("svi_current_score") or 0.0),
        "updated_at": now,
    }


def activate_profile_preset(slug: str) -> dict[str, Any]:
    target = get_profile_presets_dir() / f"{Path(slug).stem}.json"
    if not target.exists():
        raise FileNotFoundError(f"프로필 프리셋을 찾을 수 없습니다: {slug}")
    data = json.loads(target.read_text(encoding="utf-8"))
    prof = data.get("profile") or {}
    return save_profile(prof)


def delete_profile_preset(slug: str) -> bool:
    target = get_profile_presets_dir() / f"{Path(slug).stem}.json"
    if not target.exists():
        return False
    target.unlink()
    return True
