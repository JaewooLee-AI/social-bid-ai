"""단위 경제성/BEP, SVI 14개 지표 등급 판정, 5종 레드플래그 자동 점검 및 원클릭 보완 검증."""

from __future__ import annotations

from app.ai.bizplan_gen import (
    apply_redflag_remediation,
    calculate_unit_economics,
    evaluate_svi,
    run_redflag_audit,
)
from app.schemas.bizplan import SocialBMCanvas, UnitEconomicsInput


def test_unit_economics_bep_calculation() -> None:
    res = calculate_unit_economics(
        UnitEconomicsInput(
            unit_price=120_000,
            target_customers_monthly=80,
            conversion_rate_pct=25.0,
            variable_cost_per_unit=45_000,
            fixed_cost_monthly=4_500_000,
        )
    )
    assert res.paying_customers_monthly == 20
    assert res.monthly_revenue == 2_400_000
    assert res.annual_revenue == 28_800_000
    assert res.contribution_margin_per_unit == 75_000
    assert res.bep_customers == 60
    assert res.bep_revenue == 7_200_000
    assert "손익분기점" in res.markdown_table


def test_svi_14_indicators_grades_and_120_threshold(isolated_data_dir) -> None:
    # 전체 만점(100점) -> 탁월 & 120% 우선구매 인정
    full_scores = {f"svi_{i:02d}": 10.0 for i in range(1, 15)}
    full_res = evaluate_svi(full_scores)
    assert len(full_res.indicators) == 14
    assert full_res.total_score == 100.0
    assert full_res.social_score == 60.0
    assert full_res.economic_score == 30.0
    assert full_res.innovation_score == 10.0
    assert full_res.grade_label == "탁월"
    assert full_res.priority_purchase_120 is True

    # 75점 경계 -> 우수 & 120% 인정
    scores_75 = {
        "svi_01": 8, "svi_02": 8, "svi_03": 8, "svi_04": 4, "svi_05": 4,
        "svi_06": 3, "svi_07": 4, "svi_08": 3, "svi_09": 3,
        "svi_10": 8, "svi_11": 8, "svi_12": 4, "svi_13": 4, "svi_14": 6,
    }
    res_75 = evaluate_svi(scores_75)
    assert res_75.total_score == 75.0
    assert res_75.grade_label == "우수"
    assert res_75.priority_purchase_120 is True

    # 40점 -> 취약 & 120% 미해당
    low_scores = {f"svi_{i:02d}": 2.0 for i in range(1, 15)}
    res_low = evaluate_svi(low_scores)
    assert res_low.total_score < 45.0
    assert res_low.grade_label == "취약"
    assert res_low.priority_purchase_120 is False


def test_redflag_5_types_detection_and_remediation(isolated_data_dir) -> None:
    canvas = SocialBMCanvas()
    flawed_sections = {
        "problem": "이 시장은 경쟁사가 없고 독점적입니다. 매출 50억 달성 예정.",
        "scaleup": "초기에는 정부 보조금과 지원금 인건비 지원으로 운영합니다.",
    }
    audit = run_redflag_audit(canvas, flawed_sections)
    codes = {f.code for f in audit.red_flags}
    assert "format_length" in codes
    assert "unsubstantiated_number" in codes
    assert "no_competitor" in codes
    assert "missing_evidence" in codes
    assert "subsidy_dependency" in codes

    # 원클릭 보완 순차 적용 시 각 레드플래그가 해소되는지 검증
    remediated = dict(flawed_sections)
    for flag in list(audit.red_flags):
        remediated = apply_redflag_remediation(flag.code, flag.section_key, canvas, remediated)

    after_audit = run_redflag_audit(canvas, remediated)
    assert len(after_audit.red_flags) < len(audit.red_flags)
    assert after_audit.overall_score > audit.overall_score
