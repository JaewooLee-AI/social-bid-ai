"""2026-11-25 재검토 '중간' 항목 회귀 테스트 (M1은 test_high_findings.py).

M2 광역 시·도 지역제한 대조(경상남도↔경상북도 등) + 업로드 RFP 본문 지역 추출
M3 사업계획서 내보내기 제목·DOCX 제목 단계·한글(eastAsia) 글꼴
M4 진흥원·기업마당 수집 데이터 견고성(ID·금액·마감일·경로·다운로드 크기)
M5 공공 API 일일 한도 강제
M6 손상 문서 업로드 400 / 없는 파일 404
M8 샘플 회사 프로필 표시
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import job_guard
from app.config import get_profile_path, load_config, load_profile, save_config
from app.server import create_app

TOKEN = "medium-findings-token"
H = {"X-PyWebView-Token": TOKEN}


@pytest.fixture(autouse=True)
def _reset_guard() -> None:
    job_guard.reset_all()
    yield
    job_guard.reset_all()


def _client() -> TestClient:
    return TestClient(create_app(session_token=TOKEN, sync_profile_rag=False))


# ------------------------------------------------------------------ M2


@pytest.mark.parametrize(
    ("company", "limit", "expected"),
    [
        ("경상남도 창원시", "경상북도", False),  # 예전: '경상' 2글자 비교로 통과
        ("충청북도 청주시", "충청남도", False),
        ("전라남도 목포시", "전북특별자치도", False),
        ("경상남도 창원시", "경남", True),
        ("전라북도 전주시", "전북특별자치도", True),
        ("경기도 광주시", "광주광역시", False),
        ("서울특별시 성동구", "서울, 경기", True),
        ("서울특별시", "전국", True),
        ("", "부산광역시", None),
    ],
)
def test_region_matches_by_province(company: str, limit: str, expected: bool | None) -> None:
    from app.collectors.region import region_matches

    assert region_matches(company, limit) is expected


def test_rule_filter_rejects_neighbouring_province(isolated_data_dir: Path) -> None:
    from app.collectors.rule_filter import apply_social_enterprise_rules

    profile = {**load_profile(), "region": "경상남도 창원시"}
    res = apply_social_enterprise_rules({"region_limit": "경상북도", "category": "용역"}, profile)
    assert res["rule_passed"] is False
    assert any("지역제한 불일치" in w for w in res["rule_warnings"])


def test_uploaded_rfp_region_extracted_from_text(isolated_data_dir: Path) -> None:
    from app.documents.rfp_analyzer import build_rfp_summary_and_gonogo
    from app.schemas.docs import ParsedDocument

    doc = ParsedDocument(
        file_path="x.pdf",
        filename="x.pdf",
        file_type="pdf",
        full_text="3. 입찰참가자격\n- 공고일 전일부터 주된 영업소의 소재지가 경상북도에 있는 업체\n",
    )
    profile = {**load_profile(), "region": "경상남도 창원시"}
    res = build_rfp_summary_and_gonogo(doc, [], profile)
    region_item = next(i for i in res.go_nogo if i.criterion == "참가 가능 지역 제한")
    assert region_item.required_value == "경북"  # 예전: 메타가 없어 항상 '전국'·통과
    assert region_item.status == "warn"


# ------------------------------------------------------------------ M3


def test_bizplan_export_uses_section_titles_not_keys(isolated_data_dir: Path, tmp_path: Path) -> None:
    from docx import Document

    client = _client()
    bp = client.post("/api/bizplan", json={"title": "내보내기 제목 테스트", "output_track": "psst"}, headers=H).json()
    client.put(
        f"/api/bizplan/{bp['id']}",
        json={"sections_content": {"team": "팀 본문", "problem": "#### 세부 항목\n문제 본문"}},
        headers=H,
    )
    md = client.post(
        "/api/bizplan/export", json={"bizplan_id": bp["id"], "format": "md", "output_path": str(tmp_path / "o.md")}, headers=H
    ).json()
    text = Path(md["output_path"]).read_text(encoding="utf-8")
    assert "## problem" not in text and "## 1. P — Problem" in text
    assert text.index("1. P — Problem") < text.index("4. T — Team")  # 트랙 순서

    dx = client.post(
        "/api/bizplan/export", json={"bizplan_id": bp["id"], "format": "docx", "output_path": str(tmp_path / "o.docx")}, headers=H
    ).json()
    d = Document(dx["output_path"])
    paras = [(p.style.name, p.text) for p in d.paragraphs]
    assert ("Heading 4", "세부 항목") in paras  # 예전: '#### 세부 항목' 문자 그대로
    assert not any(t.startswith("#") for _, t in paras)
    from docx.oxml.ns import qn

    rfonts = d.styles["Normal"].element.rPr.find(qn("w:rFonts"))
    assert rfonts.get(qn("w:eastAsia")) == "Malgun Gothic"


# ------------------------------------------------------------------ M4


def test_seis_ids_are_stable_and_amounts_tolerant(isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.collectors.seis as seis

    cfg = load_config()
    cfg["public_api"].update(seis_service_key="K", seis_endpoint_url="https://seis.example/api")
    save_config(cfg)

    class _Resp:
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self._items = items

        def json(self) -> dict[str, Any]:
            return {"data": self._items}

    day1 = [{"pblancNm": "판로지원 A", "supportAmt": "30,000,000", "reqstEndDe": "2026-12-01"}]
    day2 = [{"pblancNm": "다른 공고 B", "supportAmt": "5천만원"}, *day1]

    async def fake(client: Any, url: str, params: dict[str, Any], source: str, max_retries: int = 3) -> Any:
        return _Resp(current)

    monkeypatch.setattr(seis, "request_with_backoff", fake)
    current = day1
    a1, _ = asyncio.run(seis.fetch_seis_notices())
    current = day2
    a2, _ = asyncio.run(seis.fetch_seis_notices())
    assert a1[0]["estimated_price"] == 30_000_000  # 예전: int('30,000,000') ValueError → 수집 전체 실패
    assert a2[0]["estimated_price"] == 50_000_000
    # 예전: 목록 순번 SEIS-1이라 2일차의 B가 1일차 A를 덮어씀
    ids2 = {i["title"]: i["bid_no"] for i in a2}
    assert ids2["판로지원 A"] == a1[0]["bid_no"] and ids2["다른 공고 B"] != a1[0]["bid_no"]


def test_bizinfo_deadline_range_and_safe_paths() -> None:
    from app.collectors.base import normalize_deadline, safe_path_component

    assert normalize_deadline("20261101 ~ 20261130") == "2026-11-30"
    assert normalize_deadline("상시 접수") == "상시 접수"
    assert safe_path_component("../../etc") == "etc"
    assert safe_path_component("..") == "file"
    assert "/" not in safe_path_component("a/b:c?.hwpx")


def test_attachment_download_size_cap(tmp_path: Path) -> None:
    from app.collectors.base import download_to_file

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    async def run(limit: int) -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await download_to_file(client, "https://files.example/a.pdf", tmp_path / "a.pdf", max_bytes=limit)

    with pytest.raises(ValueError):
        asyncio.run(run(1000))
    assert not (tmp_path / "a.pdf").exists() and not (tmp_path / "a.pdf.part").exists()
    assert asyncio.run(run(10_000)) == 5000


# ------------------------------------------------------------------ M5


def test_public_api_daily_limit_is_enforced(isolated_data_dir: Path) -> None:
    from app.collectors.base import QuotaExceededError, increment_quota, request_with_backoff

    cfg = load_config()
    cfg["public_api"].setdefault("daily_limits", {})["seis"] = 3
    save_config(cfg)
    increment_quota("seis", 3)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await request_with_backoff(client, "https://seis.example/api", {}, source="seis")

    with pytest.raises(QuotaExceededError):
        asyncio.run(run())
    assert calls["n"] == 0  # 예전: 기록만 하고 그대로 호출


# ------------------------------------------------------------------ M6


@pytest.mark.parametrize("name", ["broken.hwpx", "broken.pdf"])
def test_corrupt_upload_returns_400_with_reason(isolated_data_dir: Path, name: str) -> None:
    client = _client()
    r = client.post("/api/docs/upload", files={"file": (name, b"not a real document", "application/octet-stream")}, headers=H)
    assert r.status_code == 400  # 예전: 500 "요청 실패"
    assert "열 수 없습니다" in r.json()["detail"]

    r = client.post("/api/rag/ingest", files={"file": (name, b"garbage", "application/octet-stream")}, headers=H)
    assert r.status_code == 400


def test_missing_rag_path_returns_404(isolated_data_dir: Path) -> None:
    r = _client().post("/api/rag/ingest-path", json={"file_path": "/nonexistent/x.pdf"}, headers=H)
    assert r.status_code == 404


# ------------------------------------------------------------------ M8


def test_sample_profile_is_flagged_until_user_saves(isolated_data_dir: Path, tmp_path: Path) -> None:
    client = _client()
    prof = client.get("/api/settings/profile", headers=H).json()
    assert prof["is_sample"] is True
    assert "예비" not in prof["social_type"] or not prof["is_certified_social"]  # 예전: 예비인데 인증 특례 적용
    assert client.get("/api/dashboard/summary", headers=H).json()["profile_is_sample"] is True

    bp = client.post("/api/bizplan", json={"title": "샘플 경고", "output_track": "psst"}, headers=H).json()
    client.put(f"/api/bizplan/{bp['id']}", json={"sections_content": {"problem": "본문"}}, headers=H)
    res = client.post(
        "/api/bizplan/export", json={"bizplan_id": bp["id"], "format": "md", "output_path": str(tmp_path / "s.md")}, headers=H
    ).json()
    assert any("샘플" in w for w in res["warnings"])

    prof["name"] = "실제 회사"
    assert client.put("/api/settings/profile", json=prof, headers=H).json()["is_sample"] is False
    assert client.get("/api/dashboard/summary", headers=H).json()["profile_is_sample"] is False


def test_legacy_profile_file_without_flag(isolated_data_dir: Path) -> None:
    path = get_profile_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("is_sample", None)
    data["name"] = "이미 수정한 우리 회사"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_profile()["is_sample"] is False  # 기존 사용자의 실제 프로필에 샘플 경고를 붙이지 않음
