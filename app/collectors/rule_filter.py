"""사회적기업 수의계약 특례(5천만 1인/1억 2인), 직접생산확인 의무, 복수 업종·지역 1차 규칙 필터."""

from __future__ import annotations

from typing import Any

from app.config import load_profile


def apply_social_enterprise_rules(
    bid: dict[str, Any],
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    공고 메타데이터에 사회적기업 특례(국가계약법 시행령 제26조·지방계약법 시행령 제25조/30조) 및
    중소기업자간 경쟁제품 직접생산확인 의무(1,000만 원+)와 프로필 조건을 대조한다.
    """
    profile = profile or load_profile()
    est_price = int(bid.get("estimated_price") or 0)
    category = str(bid.get("category") or "용역")
    region_limit = str(bid.get("region_limit") or "전국")
    license_limit = str(bid.get("license_limit") or "")
    is_sme = bool(bid.get("is_sme_competing"))

    is_se_eligible = bool(profile.get("is_certified_social"))
    vuln_ratio = float(profile.get("vulnerable_employment_ratio") or 0.0)
    is_women_or_disabled = bool(profile.get("is_women_owned") or profile.get("is_disabled_owned"))
    meets_local_threshold = (is_se_eligible and vuln_ratio >= 30.0) or is_women_or_disabled

    privilege_tier = "none"
    privilege_label = ""
    warnings: list[str] = []
    rule_passed = True

    # 1. 수의계약 금액 특례 판별 (물품·용역 기준, 부가세 제외 추정가격)
    if category in ("용역", "물품", "외자/기타") and est_price > 0:
        if est_price <= 50_000_000 and is_se_eligible:
            privilege_tier = "private_1_quote"
            privilege_label = "수의계약 1인 견적 가능 (5천만 이하)"
            if not meets_local_threshold:
                warnings.append("지자체(지방계약법) 공고 시 취약계층 고용비율 30% 이상 요건 확인 필요")
        elif est_price <= 100_000_000 and is_se_eligible:
            privilege_tier = "private_2_quotes"
            privilege_label = "수의계약 2인 이상 견적 가능 (1억 이하)"
            if not meets_local_threshold:
                warnings.append("지자체(지방계약법) 공고 시 취약계층 고용비율 30% 이상 요건 확인 필요")
        elif is_se_eligible:
            privilege_tier = "general_priority"
            privilege_label = "사회적기업 우선구매·가점 검토"
    elif bid.get("source") in ("seis", "bizinfo") and is_se_eligible:
        privilege_tier = "general_priority"
        privilege_label = "사회적기업 지원사업 트랙"

    # 2. 중소기업자간 경쟁제품 직접생산확인 의무 (1,000만 원 이상)
    dp_items = [x.strip() for x in (profile.get("direct_production_items") or []) if x.strip()]
    if is_sme and est_price >= 10_000_000:
        title_str = str(bid.get("title") or "")
        matched_dp = any(any(tok in title_str for tok in item.replace("(", " ").replace(")", " ").split() if len(tok) >= 2) for item in dp_items)
        if not dp_items or not matched_dp:
            warnings.append("중소기업자간 경쟁제품(1천만 원+): 직접생산확인증명서 보유 품목 일치 여부 확인 필요")

    # 3. 지역제한 필터
    comp_region = str(profile.get("region") or "")
    include_nationwide = bool(profile.get("include_nationwide", True))
    if region_limit and region_limit not in ("전국", "제한없음"):
        short_region = comp_region[:2] if comp_region else ""
        if short_region and short_region not in region_limit:
            rule_passed = False
            warnings.append(f"지역제한 불일치 (공고: {region_limit} / 본사: {comp_region})")
    elif region_limit in ("전국", "제한없음") and not include_nationwide:
        rule_passed = False
        warnings.append("프로필 설정에서 전국 공고 포함 옵션이 비활성화되어 있습니다.")

    # 4. 복수 업종코드 대조 (공고에 면허/업종코드가 명시된 경우)
    industry_codes = [str(c).strip() for c in (profile.get("industry_codes") or []) if str(c).strip()]
    if license_limit and industry_codes:
        # 숫자 4자리 업종코드가 license_limit에 있을 때만 엄격 대조
        import re

        codes_in_bid = re.findall(r"\b\d{4}\b", license_limit)
        if codes_in_bid and not any(c in industry_codes for c in codes_in_bid):
            rule_passed = False
            warnings.append(f"업종코드 미보유 (요구: {','.join(codes_in_bid)})")

    # 5. 추정가격 하한~상한 필터 (금액이 있는 입찰만)
    min_amt = int(profile.get("min_bid_amount") or 0)
    max_amt = int(profile.get("max_bid_amount") or 0)
    if est_price > 0:
        if min_amt > 0 and est_price < min_amt:
            rule_passed = False
            warnings.append(f"추정가격({est_price:,}원)이 설정 하한({min_amt:,}원) 미만")
        if max_amt > 0 and est_price > max_amt:
            rule_passed = False
            warnings.append(f"추정가격({est_price:,}원)이 설정 상한({max_amt:,}원) 초과")

    enriched = dict(bid)
    enriched["privilege_tier"] = privilege_tier
    enriched["privilege_label"] = privilege_label
    enriched["rule_passed"] = rule_passed
    enriched["rule_warnings"] = warnings
    return enriched
