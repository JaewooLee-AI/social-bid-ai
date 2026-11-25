"""광역 시·도 정규화 및 지역제한 대조.

예전에는 본사 소재지 앞 2글자(`comp_region[:2]`)를 공고 지역 문자열에 포함되는지만 봐서
경상남도↔경상북도, 충청남·북도, 전라남·북도가 같은 지역으로 통과했다.
"""

from __future__ import annotations

import re

# (정규 이름, 별칭들) — 긴 별칭부터 매칭해 "경상남도"가 "경상"·"남도"로 쪼개지지 않게 한다.
_PROVINCES: list[tuple[str, tuple[str, ...]]] = [
    ("서울", ("서울특별시", "서울시", "서울")),
    ("부산", ("부산광역시", "부산시", "부산")),
    ("대구", ("대구광역시", "대구시", "대구")),
    ("인천", ("인천광역시", "인천시", "인천")),
    ("광주", ("광주광역시", "광주")),
    ("대전", ("대전광역시", "대전시", "대전")),
    ("울산", ("울산광역시", "울산시", "울산")),
    ("세종", ("세종특별자치시", "세종시", "세종")),
    ("경기", ("경기도", "경기")),
    ("강원", ("강원특별자치도", "강원도", "강원")),
    ("충북", ("충청북도", "충북")),
    ("충남", ("충청남도", "충남")),
    ("전북", ("전북특별자치도", "전라북도", "전북")),
    ("전남", ("전라남도", "전남")),
    ("경북", ("경상북도", "경북")),
    ("경남", ("경상남도", "경남")),
    ("제주", ("제주특별자치도", "제주도", "제주")),
]

_ALIASES: list[tuple[str, str]] = sorted(
    ((alias, canon) for canon, aliases in _PROVINCES for alias in aliases),
    key=lambda x: -len(x[0]),
)
_ALIAS_RE = re.compile("|".join(re.escape(a) for a, _ in _ALIASES))
_ALIAS_TO_CANON = dict(_ALIASES)

NATIONWIDE_VALUES = {"", "전국", "제한없음", "제한 없음", "없음", "해당없음", "전국(제한없음)"}


def find_provinces(text: str) -> list[str]:
    """문자열에 등장하는 광역 시·도를 등장 순서대로(중복 제거) 정규 이름으로 반환한다."""
    s = str(text or "")
    found: list[str] = []
    for m in _ALIAS_RE.finditer(s):
        canon = _ALIAS_TO_CANON[m.group(0)]
        # "경기도 광주시"의 광주는 광주광역시가 아니다
        if canon == "광주" and m.group(0) == "광주" and "경기" in s[max(0, m.start() - 8) : m.start()]:
            continue
        if canon not in found:
            found.append(canon)
    return found


def company_province(region: str) -> str:
    """본사 소재지 문자열에서 대표 광역 시·도(첫 번째)를 반환. 알 수 없으면 빈 문자열."""
    provinces = find_provinces(region)
    return provinces[0] if provinces else ""


def is_nationwide(region_limit: str) -> bool:
    return str(region_limit or "").strip() in NATIONWIDE_VALUES


def region_matches(company_region: str, region_limit: str) -> bool | None:
    """
    True  = 참가 가능(전국 공고이거나 본사 시·도가 허용 지역에 포함)
    False = 허용 지역 밖
    None  = 판단 불가(본사 소재지나 공고 지역을 시·도로 해석하지 못함) → 사람 확인 필요
    """
    if is_nationwide(region_limit):
        return True
    allowed = find_provinces(region_limit)
    comp = company_province(company_region)
    if not allowed or not comp:
        return None
    return comp in allowed


_REGION_CONTEXT_RE = re.compile(
    r"[^\n]{0,60}(?:지역\s*제한|참가\s*가능\s*지역|소재한|소재하는|주된\s*영업소)[^\n]{0,80}"
)


def extract_region_limit_from_text(text: str) -> str:
    """RFP 본문의 참가자격 문장에서 지역 제한(시·도)을 추출한다. 찾지 못하면 빈 문자열."""
    for m in _REGION_CONTEXT_RE.finditer(str(text or "")):
        window = m.group(0)
        if any(k in window for k in ("제한없음", "제한 없음", "전국")):
            continue
        provinces = find_provinces(window)
        if provinces:
            return ", ".join(provinces)
    return ""
