"""FastAPI 전체 엔드포인트 E2E 테스트 (CSRF 토큰 보호, 공고 HITL, 제안서 SSE/HWPX, 사업계획서, 설정/RAG)."""

from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

from app.server import create_app


def test_full_e2e_workflow(isolated_data_dir: Path) -> None:
    token = "test-csrf-token-123"
    app = create_app(session_token=token)
    client = TestClient(app)
    headers = {"X-PyWebView-Token": token}

    # 1. Health & CSRF Guard
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["ok"] is True

    r_forbidden = client.get("/api/dashboard/summary")
    assert r_forbidden.status_code == 403

    r_dash = client.get("/api/dashboard/summary", headers=headers)
    assert r_dash.status_code == 200

    # 2. 공고 샘플 로드 -> HITL 제외 피드백 -> HITL 승인(첨부 다운로드 + 제안서 자동 생성)
    r_seed = client.post("/api/bids/seed-demo", json={}, headers=headers)
    assert r_seed.status_code == 200
    assert r_seed.json()["total_saved"] == 12

    bids = client.get("/api/bids", headers=headers).json()
    assert len(bids) == 12
    first_id = bids[0]["id"]
    second_id = bids[1]["id"]

    # 제외 처리 및 피드백 기록
    r_excl = client.post(
        f"/api/bids/{second_id}/decision",
        json={"status": "excluded", "exclusion_reason": "납기 촉박", "exclusion_note": "연말 일정 중복"},
        headers=headers,
    )
    assert r_excl.status_code == 200
    assert r_excl.json()["bid"]["status"] == "excluded"

    # 승인 처리 -> 온디맨드 첨부 생성 및 제안서 워크스페이스 생성
    r_appr = client.post(
        f"/api/bids/{first_id}/decision",
        json={"status": "approved"},
        headers=headers,
    )
    assert r_appr.status_code == 200
    appr_data = r_appr.json()
    assert appr_data["bid"]["status"] == "approved"
    assert len(appr_data["downloaded_files"]) >= 1
    assert Path(appr_data["downloaded_files"][0]).exists()
    prop_id = appr_data["created_proposal_id"]
    assert prop_id is not None

    # 3. 제안서 SSE 스트리밍 생성 + HWPX / DOCX / MD 내보내기
    prop = client.get(f"/api/proposal/{prop_id}", headers=headers).json()
    first_sec_id = prop["outline"][0]["id"]
    with client.stream(
        "POST",
        "/api/proposal/generate",
        json={"proposal_id": prop_id, "section_ids": [first_sec_id], "model_tier": "fast"},
        headers=headers,
    ) as stream_resp:
        assert stream_resp.status_code == 200
        body = "".join(stream_resp.iter_text())
        assert "event: delta" in body
        assert "event: done" in body

    for fmt in ("hwpx", "docx", "md"):
        r_exp = client.post(
            f"/api/proposal/export-{fmt}",
            json={"proposal_id": prop_id, "format": fmt},
            headers=headers,
        )
        assert r_exp.status_code == 200
        assert Path(r_exp.json()["output_path"]).exists()

    # 4. 소셜 사업계획서 생성 -> SSE 초안 -> 레드플래그 점검 -> HWPX 내보내기
    r_bp = client.post(
        "/api/bizplan",
        json={"title": "2026 예비사회적기업 테스트 계획서", "output_track": "psst"},
        headers=headers,
    )
    assert r_bp.status_code == 200
    bp_id = r_bp.json()["id"]

    with client.stream(
        "POST",
        "/api/bizplan/generate",
        json={"bizplan_id": bp_id, "output_track": "psst", "section_keys": ["problem", "scaleup"]},
        headers=headers,
    ) as bp_stream:
        assert bp_stream.status_code == 200
        bp_body = "".join(bp_stream.iter_text())
        assert "event: audit" in bp_body

    r_bp_exp = client.post(
        "/api/bizplan/export",
        json={"bizplan_id": bp_id, "format": "hwpx"},
        headers=headers,
    )
    assert r_bp_exp.status_code == 200
    assert Path(r_bp_exp.json()["output_path"]).exists()

    # 4-b. 사업계획서 본문 직접 편집(PUT) 및 지정 HWPX 슬롯 자동 매핑 내보내기
    from app.documents.hwpx_engine import create_sample_hwpx_template, parse_hwpx_or_hwp

    tpl_path = create_sample_hwpx_template(isolated_data_dir / "custom_biz_form.hwpx", kind="form")
    r_bp_upd = client.put(
        f"/api/bizplan/{bp_id}",
        json={
            "template_hwpx_path": str(tpl_path),
            "sections_content": {
                "problem": "독거어르신 디지털 소외 및 돌봄 공백 Pain Point 해소를 목표로 지자체 복지관 현장 조사를 완료했습니다. " * 3,
                "solution": "취약계층 매니저 파견 솔루션 및 실적 96.4점 달성. 경쟁사·대체재 비교 결과 일반 영리업체 대비 우선구매 특례 경쟁력이 높습니다. " * 3,
                "scaleup": "객단가 12만원 × 20명 = 월 240만원, BEP 60건 (B2G 우선구매 5천만 수의계약 및 B2B ESG 납품 매출) " * 3,
                "team": "SVI 78점 우수등급(120% 우선구매 인정), 이사회 회의록 및 수요기관 구매의향서 보유. " * 3,
            },
        },
        headers=headers,
    )
    assert r_bp_upd.status_code == 200
    assert r_bp_upd.json()["audit_report"]["overall_score"] >= 80

    patched_biz_out = isolated_data_dir / "exports" / "mapped_bizplan.hwpx"
    r_bp_patch = client.post(
        "/api/bizplan/export",
        json={"bizplan_id": bp_id, "format": "hwpx", "output_path": str(patched_biz_out)},
        headers=headers,
    )
    assert r_bp_patch.status_code == 200
    reopened_bp = parse_hwpx_or_hwp(patched_biz_out)
    assert "독거어르신 디지털 소외" in reopened_bp.full_text
    assert "월 240만원" in reopened_bp.full_text

    # 4-c. 고객사 프로필 프리셋 저장 · 목록 조회 · 전환 활성화
    cur_prof = client.get("/api/settings/profile", headers=headers).json()
    cur_prof["name"] = "(주)그린케어 사회적기업"
    cur_prof["region"] = "경기도"
    r_preset_save = client.post(
        "/api/settings/profile-presets",
        json={"preset_name": "그린케어_경기도", "profile": cur_prof},
        headers=headers,
    )
    assert r_preset_save.status_code == 200
    slug = r_preset_save.json()["slug"]

    r_preset_list = client.get("/api/settings/profile-presets", headers=headers)
    assert r_preset_list.status_code == 200
    assert any(p["slug"] == slug for p in r_preset_list.json())

    r_preset_act = client.post(f"/api/settings/profile-presets/{slug}/activate", json={}, headers=headers)
    assert r_preset_act.status_code == 200
    assert r_preset_act.json()["name"] == "(주)그린케어 사회적기업"

    # 5. 프론트엔드 정적 빌드(ui_dist) 서빙 확인
    ui_index = Path("frontend/ui_dist/index.html")
    if ui_index.exists():
        r_root = client.get("/")
        assert r_root.status_code == 200
        assert "SocialBidAI" in r_root.text
