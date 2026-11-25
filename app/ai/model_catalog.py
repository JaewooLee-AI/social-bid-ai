"""공식 Gemini 모델 카탈로그, 단가/지원종료 사이트 동기화 및 모델별 API 호환성 관리."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from app.config import get_model_catalog_path, load_config, save_config
from app.schemas.settings import (
    ActiveModelWarning,
    ModelCatalogEntry,
    ModelCatalogResponse,
    ModelSyncResponse,
)

logger = logging.getLogger(__name__)

OFFICIAL_PRICING_PAGE_URL = "https://aistudio.google.com/docs/pricing"
OFFICIAL_DEPRECATIONS_PAGE_URL = "https://aistudio.google.com/docs/deprecations"

PRICING_DOC_URLS: tuple[str, ...] = (
    "https://aistudio.google.com/docs/pricing.md",
    "https://ai.google.dev/gemini-api/docs/pricing.md.txt",
)

DEPRECATIONS_DOC_URLS: tuple[str, ...] = (
    "https://aistudio.google.com/docs/deprecations.md",
    "https://ai.google.dev/gemini-api/docs/deprecations.md.txt",
)

GEMINI_MODELS_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_EXCLUDED_MODEL_RE = re.compile(
    r"(?:^|-)(?:imagen|veo|lyria|tts|native-audio|live|robotics|computer-use|cyber|aqa|bison|gemma|transcribe|translate|image|omni|decision|nano-banana|customtools)(?:-|$)",
    re.IGNORECASE,
)
_MODEL_ID_RE = re.compile(r"\bgemini-(?:[0-9]+(?:\.[0-9]+)?|embedding)-[a-z0-9._-]*[a-z0-9]\b", re.IGNORECASE)
_VERSION_RE = re.compile(r"gemini-(\d+)(?:\.(\d+))?", re.IGNORECASE)
_USD_PRICE_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)")
_DISPLAY_MODEL_RE = re.compile(
    r"\bgemini\s+(?:(\d+(?:\.\d+)?)\s+(flash[-\s]?lite|flash|pro|neon|hydrogen|carbon|argon)(?:\s+(preview))?|embedding\s+([0-9]+))\b",
    re.IGNORECASE,
)

_SKIP_ROW_KEYWORDS = (
    "cache",
    "caching",
    "cached",
    "batch",
    "flex",
    "priority",
    "storage",
    "grounding",
    "audio",
    "video",
    "image",
    "tuning",
    "prompts >",
    "> 200k",
    ">200k",
    "over 200k",
    "live api",
    "캐시",
    "배치",
    "스토리지",
)


class SiteStructureChangedError(Exception):
    """공식 사이트 HTML/Markdown 구조 변경으로 모델·단가 파싱에 실패했을 때 발생한다."""

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        self.reason = reason
        self.error_code = "SITE_STRUCTURE_CHANGED"
        super().__init__(f"{reason} (대상 URL: {url})")


class _HtmlToTextParser(HTMLParser):
    """표/제목/단락 구조를 유지하면서 HTML을 평문 마크다운 유사 텍스트로 변환한다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._lines: list[str] = []
        self._buf: list[str] = []
        self._row_cells: list[str] = []
        self._in_cell = False
        self._skip_depth = 0

    def _flush_buf(self, prefix: str = "") -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf.clear()
        if text:
            self._lines.append(f"{prefix}{text}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t in ("h1", "h2", "h3", "h4"):
            self._flush_buf()
        elif t == "tr":
            self._flush_buf()
            self._row_cells = []
        elif t in ("td", "th"):
            self._buf.clear()
            self._in_cell = True
        elif t == "br":
            if not self._in_cell:
                self._flush_buf()
            else:
                self._buf.append(" ")
        elif t in ("p", "li", "section", "article"):
            if not self._in_cell:
                self._flush_buf()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg"):
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t in ("h1", "h2", "h3", "h4"):
            self._flush_buf(prefix="## ")
        elif t in ("td", "th"):
            cell_txt = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._buf.clear()
            self._row_cells.append(cell_txt)
            self._in_cell = False
        elif t == "tr":
            if any(self._row_cells):
                self._lines.append("| " + " | ".join(self._row_cells) + " |")
            self._row_cells = []
        elif t in ("p", "li"):
            if not self._in_cell:
                self._flush_buf()

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._buf.append(data)

    def get_text(self) -> str:
        self._flush_buf()
        return "\n".join(self._lines)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_doc_text(raw_text: str) -> str:
    lower = raw_text.lower()
    if "<html" in lower or "<table" in lower or "<body" in lower or "<tr" in lower:
        parser = _HtmlToTextParser()
        parser.feed(raw_text)
        parser.close()
        return parser.get_text()
    return raw_text


def _is_excluded_model(model_id: str) -> bool:
    return bool(_EXCLUDED_MODEL_RE.search(model_id.lower()))


def _parse_version_tuple(model_id: str) -> tuple[int, int]:
    m = _VERSION_RE.search(model_id.lower())
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


def _format_display_name(model_id: str) -> str:
    parts = model_id.split("-")
    formatted: list[str] = []
    for p in parts:
        if p.lower() in ("preview", "exp", "latest"):
            formatted.append(f"({p.capitalize()})")
        elif p.isdigit() or re.match(r"^\d+\.\d+$", p):
            formatted.append(p)
        else:
            formatted.append(p.capitalize())
    return " ".join(formatted)


def get_model_capabilities(model_id: str) -> dict[str, Any]:
    """모델 ID 규칙을 기반으로 temperature 지원 여부 및 thinking_level 제약사항을 판별한다."""
    mid = (model_id or "").strip().lower()
    is_gemini = mid.startswith("gemini-")
    if not is_gemini:
        return {
            "supports_temperature": True,
            "supports_thinking_level": False,
            "allowed_thinking_levels": [],
            "default_thinking_level": None,
        }

    if "embedding" in mid:
        return {
            "supports_temperature": False,
            "supports_thinking_level": False,
            "allowed_thinking_levels": [],
            "default_thinking_level": None,
        }

    major, _ = _parse_version_tuple(mid)
    if major >= 3:
        allowed = ["minimal", "low", "medium", "high"] if "flash-lite" in mid else ["low", "medium", "high"]
        return {
            "supports_temperature": False,
            "supports_thinking_level": True,
            "allowed_thinking_levels": allowed,
            "default_thinking_level": "low" if "flash-lite" in mid else "medium",
        }

    return {
        "supports_temperature": True,
        "supports_thinking_level": False,
        "allowed_thinking_levels": [],
        "default_thinking_level": None,
    }


def classify_model(
    model_id: str,
    *,
    display_name: str = "",
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
    price_note: str = "",
    deprecated: bool = False,
    replacement_model: str = "",
    verified_live: bool | None = None,
    source: str = "builtin",
) -> ModelCatalogEntry:
    """알려진 모델 및 미래 신규 모델에 대해 일관된 역할·배지·API 제약을 부여한다."""
    mid = model_id.strip().lower()
    caps = get_model_capabilities(mid)

    if "embedding" in mid:
        category = "embedding"
        recommended_tier = "embedding"
        tier_badge = "임베딩"
    elif deprecated:
        category = "chat"
        recommended_tier = "legacy"
        tier_badge = "지원 종료 예정"
    elif "flash-lite" in mid:
        category = "chat"
        recommended_tier = "fast"
        tier_badge = "고속·경량 추천"
    elif "flash" in mid or "hydrogen" in mid:
        category = "chat"
        recommended_tier = "strong"
        tier_badge = "고품질 기본 추천"
    elif any(tok in mid for tok in ("pro", "neon", "carbon", "argon")):
        category = "chat"
        recommended_tier = "frontier"
        tier_badge = "최상위 추론 (Pro 이상)"
    else:
        category = "chat"
        recommended_tier = "strong"
        tier_badge = "일반 모델"

    return ModelCatalogEntry(
        model_id=mid,
        display_name=display_name or _format_display_name(mid),
        category=category,
        recommended_tier=recommended_tier,
        tier_badge=tier_badge,
        input_price_per_1m=input_price_per_1m,
        output_price_per_1m=output_price_per_1m,
        price_note=price_note,
        supports_temperature=bool(caps["supports_temperature"]),
        supports_thinking_level=bool(caps["supports_thinking_level"]),
        allowed_thinking_levels=list(caps["allowed_thinking_levels"]),
        deprecated=deprecated,
        replacement_model=replacement_model,
        verified_live=verified_live,
        source=source,
    )


BUILTIN_MODEL_CATALOG: list[ModelCatalogEntry] = [
    classify_model(
        "gemini-3.5-flash-lite",
        display_name="Gemini 3.5 Flash-Lite",
        input_price_per_1m=0.30,
        output_price_per_1m=2.50,
        price_note="공고 배치 채점·스캔 OCR 등 고속 경량 작업 최적 (minimal 사고 지원)",
    ),
    classify_model(
        "gemini-3.8-flash",
        display_name="Gemini 3.8 Flash",
        input_price_per_1m=0.75,
        output_price_per_1m=3.75,
        price_note="~2026.12.31 프로모션 ($0.75/$3.75), 2027.01.01~ $1.50/$7.50",
    ),
    classify_model(
        "gemini-4-hydrogen",
        display_name="Gemini 4 Hydrogen",
        input_price_per_1m=1.50,
        output_price_per_1m=7.50,
        price_note="Gemini 4 경량 워크호스 모델",
    ),
    classify_model(
        "gemini-3.1-pro-preview",
        display_name="Gemini 3.1 Pro (Preview)",
        input_price_per_1m=2.00,
        output_price_per_1m=12.00,
        price_note="≤200K 기준 ($2/$12), 200K 초과 시 $4/$18",
    ),
    classify_model(
        "gemini-4-neon",
        display_name="Gemini 4 Neon",
        input_price_per_1m=3.00,
        output_price_per_1m=15.00,
        price_note="고액 경쟁입찰·최종본 다듬기용 최상위 추천",
    ),
    classify_model(
        "gemini-4-carbon",
        display_name="Gemini 4 Carbon",
        input_price_per_1m=2.00,
        output_price_per_1m=10.00,
        price_note="~2026.11.30 프로모션 ($2/$10), 12/1~ $4/$20",
    ),
    classify_model(
        "gemini-4-argon",
        display_name="Gemini 4 Argon",
        input_price_per_1m=2.00,
        output_price_per_1m=10.00,
        price_note="~2026.11.30 프로모션 ($2/$10), 12/1~ $4/$20",
    ),
    classify_model(
        "gemini-embedding-001",
        display_name="Gemini Embedding 001",
        input_price_per_1m=0.15,
        output_price_per_1m=0.0,
        price_note="텍스트 전용 2,048토큰 (Matryoshka 768D 기본 권장)",
    ),
    classify_model(
        "gemini-embedding-2",
        display_name="Gemini Embedding 2",
        input_price_per_1m=0.20,
        output_price_per_1m=0.0,
        price_note="멀티모달 8,192토큰 지원 (변경 시 RAG 재인덱싱 필요)",
    ),
    classify_model(
        "gemini-2.5-flash",
        display_name="Gemini 2.5 Flash (Legacy)",
        input_price_per_1m=0.15,
        output_price_per_1m=0.60,
        price_note="지원 종료 예정 · gemini-3.5-flash-lite로 자동 라우팅",
        deprecated=True,
        replacement_model="gemini-3.5-flash-lite",
    ),
    classify_model(
        "gemini-2.5-pro",
        display_name="Gemini 2.5 Pro (Legacy)",
        input_price_per_1m=1.25,
        output_price_per_1m=10.00,
        price_note="지원 종료 예정 · gemini-3.8-flash로 자동 라우팅",
        deprecated=True,
        replacement_model="gemini-3.8-flash",
    ),
]


def _builtin_map() -> dict[str, ModelCatalogEntry]:
    return {entry.model_id: entry for entry in BUILTIN_MODEL_CATALOG}


def _extract_explicit_model_id(line: str) -> str | None:
    m = _MODEL_ID_RE.search(line)
    if m:
        candidate = m.group(0).lower().rstrip(".")
        if not _is_excluded_model(candidate):
            return candidate
    return None


def _extract_display_model_id(line: str) -> str | None:
    dm = _DISPLAY_MODEL_RE.search(line)
    if not dm:
        return None
    if dm.group(4):
        candidate = f"gemini-embedding-{dm.group(4)}"
    else:
        tier = re.sub(r"\s+", "-", dm.group(2).lower())
        suffix = f"-{dm.group(3).lower()}" if dm.group(3) else ""
        candidate = f"gemini-{dm.group(1)}-{tier}{suffix}"
    return None if _is_excluded_model(candidate) else candidate


def _extract_model_id_from_line(line: str) -> str | None:
    return _extract_explicit_model_id(line) or _extract_display_model_id(line)


def _should_skip_price_row(lower_line: str) -> bool:
    return any(kw in lower_line for kw in _SKIP_ROW_KEYWORDS)


def _extract_first_price(line: str) -> float | None:
    m = _USD_PRICE_RE.search(line)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    if 0.0 <= val <= 500.0:
        return val
    return None


_MARKDOC_TABLE_RE = re.compile(r"\{%\s*table\s*%\}(.*?)\{%\s*/table\s*%\}", re.DOTALL | re.IGNORECASE)
_MARKDOC_TAB_STD_RE = re.compile(
    r"\{%\s*tab\b[^%]*title=[\"']Standard[\"'][^%]*%\}(.*?)\{%\s*/tab\s*%\}",
    re.DOTALL | re.IGNORECASE,
)
_BACKTICK_MODEL_RE = re.compile(r"`(gemini-(?:[0-9]+(?:\.[0-9]+)?|embedding)-[a-z0-9._-]*[a-z0-9])`", re.IGNORECASE)
_DATE_THROUGH_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s+through\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE)
_DATE_STARTING_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s+starting\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE)


def _parse_markdoc_tables(block: str) -> list[list[list[str]]]:
    """Google AI Studio Markdoc `{% table %} ... {% /table %}` 블록을 행/셀 2차원 리스트로 파싱한다."""
    tables: list[list[list[str]]] = []
    for tbl_match in _MARKDOC_TABLE_RE.finditer(block):
        body = tbl_match.group(1)
        rows: list[list[str]] = []
        raw_row_blocks = re.split(r"(?m)^\s*---\s*$", body)
        for rb in raw_row_blocks:
            cells: list[str] = []
            for raw_line in rb.splitlines():
                line = raw_line.rstrip()
                m_cell = re.match(r"^\s*\*\s+(.*)$", line)
                if m_cell:
                    cells.append(m_cell.group(1).strip())
                elif cells and line.strip():
                    cells[-1] = f"{cells[-1]}\n{line.strip()}"
            if any(c.strip() for c in cells):
                rows.append([c.strip() for c in cells])
        if rows:
            tables.append(rows)
    return tables


def _parse_english_date(date_str: str) -> date | None:
    clean = re.sub(r"\s+", " ", date_str.strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _extract_effective_price(paid_cell: str, today: date | None = None) -> tuple[float | None, str]:
    """프로모션 날짜 구간(through/starting)과 컨텍스트 구간(<= 200k)을 반영해 현재 적용 단가를 반환한다."""
    ref_day = today or datetime.now(timezone.utc).astimezone().date()
    cell = (paid_cell or "").strip()
    if not cell:
        return None, ""

    m_through = _DATE_THROUGH_RE.search(cell)
    m_starting = _DATE_STARTING_RE.search(cell)
    if m_through or m_starting:
        through_val = float(m_through.group(1)) if m_through else None
        through_dt = _parse_english_date(m_through.group(2)) if m_through else None
        starting_val = float(m_starting.group(1)) if m_starting else None
        starting_dt = _parse_english_date(m_starting.group(2)) if m_starting else None

        if through_val is not None and through_dt is not None and ref_day <= through_dt:
            if starting_val is not None and starting_dt is not None:
                note = f"~{through_dt.strftime('%Y.%m.%d')} 프로모션 (${through_val:g}), {starting_dt.strftime('%Y.%m.%d')}~ ${starting_val:g}"
            else:
                note = f"~{through_dt.strftime('%Y.%m.%d')} 프로모션"
            return through_val, note
        if starting_val is not None and starting_dt is not None and ref_day >= starting_dt:
            return starting_val, f"{starting_dt.strftime('%Y.%m.%d')}~ 정규 단가 적용"

    lines = [ln.strip() for ln in cell.splitlines() if ln.strip()]
    le_lines = [ln for ln in lines if "<=" in ln or "≤" in ln]
    if le_lines:
        p = _extract_first_price(le_lines[0])
        if p is not None:
            return p, "≤200K 기준"

    base_lines = [ln for ln in lines if not any(k in ln.lower() for k in ("> 200k", ">200k", "over 200k"))]
    for ln in base_lines or lines:
        p = _extract_first_price(ln)
        if p is not None:
            return p, ""

    p = _extract_first_price(cell)
    return p, ""


_EXCLUDED_HEADING_KW = (
    "imagen",
    "veo",
    "lyria",
    "tts",
    "native audio",
    "native-audio",
    "live",
    "robotics",
    "computer use",
    "computer-use",
    "cyber",
    "nano banana",
    "nano-banana",
    "transcribe",
    "translate",
    "image",
    "omni",
    "decision",
    "genie",
    "gemma",
    "customtools",
)


def _extract_section_model_id(section: str) -> str | None:
    """Markdoc 모델 섹션 상단(tabs/table 이전)에서 정식 모델 ID를 추출한다. 특화(TTS/Image/Live) 섹션은 즉시 제외한다."""
    head_cut = len(section)
    for marker in ("{% tabs", "{% table"):
        idx = section.find(marker)
        if idx != -1 and idx < head_cut:
            head_cut = idx
    preamble = section[:head_cut]
    first_line = preamble.splitlines()[0] if preamble.splitlines() else ""
    first_low = first_line.lower()

    if any(kw in first_low for kw in _EXCLUDED_HEADING_KW):
        return None

    backtick_matches = [m.group(1).lower().rstrip(".") for m in _BACKTICK_MODEL_RE.finditer(preamble)]
    if backtick_matches:
        non_excluded = [cand for cand in backtick_matches if not _is_excluded_model(cand)]
        return non_excluded[0] if non_excluded else None

    id_attr = re.search(r'\{%\s*id=["\']([^"\']+)["\']\s*%\}', first_line)
    if id_attr:
        attr_val = id_attr.group(1).strip().lower()
        if attr_val.startswith("gemini-"):
            return None if _is_excluded_model(attr_val) else attr_val

    return _extract_model_id_from_line(first_line)


def _parse_markdoc_pricing_sections(raw_text: str, today: date | None = None) -> list[dict[str, Any]]:
    sections = re.split(r"(?m)^(?=##\s+)", raw_text)
    parsed_by_id: dict[str, dict[str, Any]] = {}

    for sec in sections:
        if not sec.lstrip().startswith("## "):
            continue
        mid = _extract_section_model_id(sec)
        if not mid or _is_excluded_model(mid) or mid in parsed_by_id:
            continue

        std_match = _MARKDOC_TAB_STD_RE.search(sec)
        scope = std_match.group(1) if std_match else sec
        tables = _parse_markdoc_tables(scope)
        if not tables:
            continue

        tbl = tables[0]
        if len(tbl) < 2:
            continue

        headers = [h.lower() for h in tbl[0]]
        paid_col = -1
        for idx, h in enumerate(headers):
            if "paid" in h or "usd" in h or "1m" in h:
                paid_col = idx
                break

        in_price: float | None = None
        out_price: float | None = None
        notes: list[str] = []

        for row in tbl[1:]:
            if len(row) < 2:
                continue
            feature_cell = row[0].strip()
            feat_low = feature_cell.lower()
            if _should_skip_price_row(feat_low):
                continue

            p_idx = paid_col if 0 <= paid_col < len(row) else (len(row) - 1)
            paid_cell = row[p_idx].strip()

            is_in = bool(re.search(r"\binput\b|입력", feat_low))
            is_out = bool(re.search(r"\boutput\b|출력", feat_low))

            if is_in and not is_out and in_price is None:
                val, note = _extract_effective_price(paid_cell, today=today)
                if val is not None:
                    in_price = val
                    if note and note not in notes:
                        notes.append(note)
            elif is_out and not is_in and out_price is None:
                val, note = _extract_effective_price(paid_cell, today=today)
                if val is not None:
                    out_price = val
                    if note and note not in notes:
                        notes.append(note)

        if "embedding" in mid:
            if in_price is not None and in_price >= 0.0:
                parsed_by_id[mid] = {
                    "model_id": mid,
                    "input_price": float(in_price),
                    "output_price": 0.0,
                    "price_note": " · ".join(notes) if notes else "공식 사이트 동기화",
                }
        elif in_price is not None and out_price is not None and in_price > 0.0 and out_price > 0.0:
            note_text = "공식 사이트 동기화"
            if notes:
                note_text = f"{notes[0]} (공식 사이트 동기화)" if "프로모션" in notes[0] or "기준" in notes[0] else notes[0]
            parsed_by_id[mid] = {
                "model_id": mid,
                "input_price": float(in_price),
                "output_price": float(out_price),
                "price_note": note_text,
            }

    return list(parsed_by_id.values())


def parse_pricing_document(
    raw_text: str,
    source_url: str = "",
    today: date | None = None,
) -> list[dict[str, Any]]:
    """
    Google AI Studio 공식 단가 문서(Markdoc, Markdown 또는 HTML)에서 모델별 1M 토큰 단가를 파싱한다.
    문서 구조가 변경되어 유효한 모델/단가를 추출할 수 없으면 SiteStructureChangedError를 발생시킨다.
    """
    text = _normalize_doc_text(raw_text or "")
    stripped = text.strip()
    lower = stripped.lower()

    if len(stripped) < 200:
        raise SiteStructureChangedError(source_url, "공식 단가 문서 응답이 너무 짧아 구조를 판독할 수 없습니다.")

    if any(tok in lower for tok in ("servicelogin", "accounts.google.com", "verify you are human", "captcha", "access denied")):
        if not (_MODEL_ID_RE.search(stripped) and _USD_PRICE_RE.search(stripped)):
            raise SiteStructureChangedError(source_url, "공식 사이트가 로그인 또는 보안 확인 페이지를 반환했습니다.")

    has_model = bool(_MODEL_ID_RE.search(stripped) or _DISPLAY_MODEL_RE.search(stripped))
    has_price = bool(_USD_PRICE_RE.search(stripped))
    has_token_marker = any(tok in lower for tok in ("1m", "1,000,000", "million", "input", "output", "입력", "출력"))
    if not (has_model and has_price and has_token_marker):
        raise SiteStructureChangedError(
            source_url,
            "공식 단가 사이트 구조가 변경되어 모델 코드 또는 1M 토큰 단가($) 표를 찾지 못했습니다.",
        )

    if "{% table" in lower:
        markdoc_results = _parse_markdoc_pricing_sections(stripped, today=today)
        chat_count = sum(1 for item in markdoc_results if "embedding" not in item["model_id"])
        if len(markdoc_results) >= 2 and chat_count >= 1:
            return markdoc_results
        raise SiteStructureChangedError(
            source_url,
            "공식 단가 사이트 Markdoc 표 형식이 변경되어 충분한 모델·토큰 단가를 추출할 수 없습니다.",
        )

    parsed_by_id: dict[str, dict[str, Any]] = {}
    current_model: str | None = None
    pending_display_model: str | None = None
    heading_pending_model_lines = 0

    def _ensure_bucket(mid: str) -> dict[str, Any]:
        if mid not in parsed_by_id:
            parsed_by_id[mid] = {
                "model_id": mid,
                "input_price": None,
                "output_price": None,
                "price_note": "",
            }
        return parsed_by_id[mid]

    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()

        # 제목(Markdown #~####) 판별
        if line.startswith("#"):
            explicit_id = _extract_explicit_model_id(line)
            if explicit_id:
                current_model = explicit_id
                pending_display_model = None
                heading_pending_model_lines = 0
                _ensure_bucket(current_model)
            else:
                current_model = None
                pending_display_model = _extract_display_model_id(line)
                heading_pending_model_lines = 6 if ("gemini" in low or pending_display_model) else 0
            continue

        # 제목 직후 명시적 모델 코드 라인(예: Model code: `gemini-4.5-flash`) 우선 바인딩
        if heading_pending_model_lines > 0 and current_model is None:
            explicit_id = _extract_explicit_model_id(line)
            if explicit_id:
                current_model = explicit_id
                pending_display_model = None
                heading_pending_model_lines = 0
                _ensure_bucket(current_model)
                continue
            if pending_display_model and (re.search(r"\b(input|output)\b|입력|출력", low) or _USD_PRICE_RE.search(line)):
                current_model = pending_display_model
                pending_display_model = None
                heading_pending_model_lines = 0
                _ensure_bucket(current_model)
            else:
                heading_pending_model_lines -= 1
                if heading_pending_model_lines == 0 and pending_display_model:
                    current_model = pending_display_model
                    pending_display_model = None
                    _ensure_bucket(current_model)
                if current_model is None:
                    continue

        # 단일 행 비교표 형식(| gemini-3.8-flash | $0.75 | $3.75 |) 지원
        if "|" in line and not _should_skip_price_row(low):
            row_id = _extract_model_id_from_line(line)
            row_prices = [float(p) for p in _USD_PRICE_RE.findall(line)]
            valid_row_prices = [p for p in row_prices if 0.0 <= p <= 500.0]
            if row_id and len(valid_row_prices) >= 2 and not re.search(r"\b(input|output)\b", low):
                bucket = _ensure_bucket(row_id)
                if bucket["input_price"] is None:
                    bucket["input_price"] = valid_row_prices[0]
                if bucket["output_price"] is None:
                    bucket["output_price"] = valid_row_prices[1]
                continue
            if row_id and "embedding" in row_id and len(valid_row_prices) >= 1 and not re.search(r"\b(input|output)\b", low):
                bucket = _ensure_bucket(row_id)
                if bucket["input_price"] is None:
                    bucket["input_price"] = valid_row_prices[0]
                    bucket["output_price"] = 0.0
                continue

        if current_model is None:
            continue

        if _should_skip_price_row(low):
            continue

        bucket = _ensure_bucket(current_model)
        is_input_row = bool(re.search(r"\binput\b|입력", low))
        is_output_row = bool(re.search(r"\boutput\b|출력", low))

        if is_input_row and not is_output_row:
            price = _extract_first_price(line)
            if price is not None and bucket["input_price"] is None:
                bucket["input_price"] = price
        elif is_output_row and not is_input_row:
            price = _extract_first_price(line)
            if price is not None and bucket["output_price"] is None:
                bucket["output_price"] = price
        elif is_input_row and is_output_row:
            prices = [float(p) for p in _USD_PRICE_RE.findall(line) if 0.0 <= float(p) <= 500.0]
            if len(prices) >= 2:
                if bucket["input_price"] is None:
                    bucket["input_price"] = prices[0]
                if bucket["output_price"] is None:
                    bucket["output_price"] = prices[1]

    valid_results: list[dict[str, Any]] = []
    chat_valid_count = 0
    for mid, info in parsed_by_id.items():
        if _is_excluded_model(mid):
            continue
        in_p = info["input_price"]
        out_p = info["output_price"]
        if "embedding" in mid:
            if in_p is not None and in_p >= 0.0:
                valid_results.append(
                    {
                        "model_id": mid,
                        "input_price": float(in_p),
                        "output_price": float(out_p or 0.0),
                        "price_note": "공식 사이트 동기화",
                    }
                )
            continue

        if in_p is not None and out_p is not None and in_p > 0.0 and out_p > 0.0:
            chat_valid_count += 1
            valid_results.append(
                {
                    "model_id": mid,
                    "input_price": float(in_p),
                    "output_price": float(out_p),
                    "price_note": "공식 사이트 동기화",
                }
            )

    if len(valid_results) < 2 or chat_valid_count < 1:
        raise SiteStructureChangedError(
            source_url,
            "공식 단가 사이트 형식이 변경되어 충분한 모델·토큰 단가(최소 2개 모델, 채팅 모델 입·출력 단가 포함)를 추출할 수 없습니다.",
        )

    return valid_results


def parse_deprecations_document(raw_text: str, today: date | None = None) -> tuple[dict[str, str], bool]:
    """공식 지원 종료(Deprecations) 문서에서 {구형모델: 대체권장모델} 매핑을 추출한다."""
    text = _normalize_doc_text(raw_text or "")
    stripped = text.strip()
    lower = stripped.lower()
    if len(stripped) < 100 or not any(k in lower for k in ("deprecat", "shutdown", "discontinu", "rout", "replacement", "지원 종료")):
        return {}, False

    ref_day = today or datetime.now(timezone.utc).astimezone().date()
    imminent_cutoff = ref_day + timedelta(days=60)
    mapping: dict[str, str] = {}

    if "{% table" in lower:
        sections = re.split(r"(?m)^(?=#{2,3}\s+)", stripped)
        for sec in sections:
            first_line = sec.splitlines()[0].lower() if sec.splitlines() else ""
            in_deprecated_heading = "deprecated" in first_line
            for tbl in _parse_markdoc_tables(sec):
                if len(tbl) < 2:
                    continue
                for row in tbl[1:]:
                    if not row:
                        continue
                    old_id = _extract_explicit_model_id(row[0])
                    if not old_id:
                        continue
                    shutdown_raw = row[2].strip() if len(row) > 2 else ""
                    shutdown_dt = _parse_english_date(shutdown_raw)
                    repl_cell = row[3].strip() if len(row) > 3 else ""
                    repl_low = repl_cell.lower()

                    new_id = ""
                    for cand_m in _MODEL_ID_RE.finditer(repl_cell):
                        cand = cand_m.group(0).lower().rstrip(".")
                        if cand != old_id and not _is_excluded_model(cand):
                            new_id = cand
                            break

                    has_imminent_shutdown = shutdown_dt is not None and shutdown_dt <= imminent_cutoff
                    is_marked_dep = (
                        in_deprecated_heading
                        or has_imminent_shutdown
                        or any(k in repl_low for k in ("no longer available", "automatically route", "deprecated"))
                    )
                    if is_marked_dep:
                        mapping[old_id] = new_id
        return mapping, True

    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        found = [m.lower().rstrip(".") for m in _MODEL_ID_RE.findall(line)]
        deduped: list[str] = []
        for mid in found:
            if not _is_excluded_model(mid) and mid not in deduped:
                deduped.append(mid)
        if len(deduped) >= 2:
            old_id = deduped[0]
            new_id = deduped[-1]
            if old_id != new_id:
                mapping[old_id] = new_id

    return mapping, True


async def fetch_live_gemini_models(
    api_key: str,
    client: httpx.AsyncClient,
) -> tuple[list[dict[str, Any]], str | None]:
    """Gemini Developer API /v1beta/models 엔드포인트로 현재 활성 모델 목록을 조회한다."""
    key = (api_key or "").strip()
    if not key:
        return [], None

    try:
        resp = await client.get(
            GEMINI_MODELS_API_URL,
            params={"key": key, "pageSize": "100"},
            timeout=15.0,
        )
    except httpx.RequestError as exc:
        return [], f"Gemini Models API 네트워크 오류: {exc}"

    if resp.status_code in (400, 401, 403):
        return [], f"Gemini API 키 인증/권한 오류 (HTTP {resp.status_code})"
    if resp.status_code != 200:
        return [], f"Gemini Models API 응답 오류 (HTTP {resp.status_code})"

    try:
        payload = resp.json()
    except Exception as exc:
        return [], f"Gemini Models API JSON 파싱 실패: {exc}"

    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return [], "Gemini Models API 응답에 models 배열이 없습니다."

    results: list[dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "")
        model_id = raw_name.split("/")[-1].strip().lower()
        if not model_id.startswith("gemini-") or _is_excluded_model(model_id):
            continue
        methods = item.get("supportedGenerationMethods") or []
        if not isinstance(methods, list):
            continue
        supports_gen = "generateContent" in methods
        supports_emb = "embedContent" in methods
        if not supports_gen and not supports_emb:
            continue
        category = "embedding" if (supports_emb and not supports_gen) or "embedding" in model_id else "chat"
        results.append(
            {
                "model_id": model_id,
                "display_name": str(item.get("displayName") or _format_display_name(model_id)),
                "category": category,
            }
        )
    return results, None


def _sort_entries(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    tier_order = {"fast": 0, "strong": 1, "frontier": 2, "embedding": 3, "legacy": 4}
    return sorted(
        entries,
        key=lambda e: (
            tier_order.get(e.recommended_tier, 9),
            -_parse_version_tuple(e.model_id)[0],
            -_parse_version_tuple(e.model_id)[1],
            e.model_id,
        ),
    )


def _pick_recommendations(entries: list[ModelCatalogEntry]) -> tuple[str, str, str]:
    any_verified_live = any(e.verified_live is True for e in entries)

    def _is_eligible(e: ModelCatalogEntry) -> bool:
        if e.deprecated:
            return False
        if any_verified_live and e.verified_live is False:
            return False
        return True

    active = [e for e in entries if _is_eligible(e)]

    fast_candidates = [e for e in active if e.category == "chat" and e.recommended_tier == "fast"]
    if fast_candidates:
        fast_candidates.sort(key=lambda e: (_parse_version_tuple(e.model_id), e.model_id == "gemini-3.5-flash-lite"), reverse=True)
        rec_fast = fast_candidates[0].model_id
    else:
        rec_fast = "gemini-3.5-flash-lite"

    strong_candidates = [e for e in active if e.category == "chat" and e.recommended_tier == "strong"]
    if strong_candidates:
        strong_candidates.sort(
            key=lambda e: (
                "flash" in e.model_id and "hydrogen" not in e.model_id,
                _parse_version_tuple(e.model_id),
                e.model_id == "gemini-3.8-flash",
            ),
            reverse=True,
        )
        rec_strong = strong_candidates[0].model_id
    else:
        rec_strong = "gemini-3.8-flash"

    emb_candidates = [e for e in active if e.category == "embedding"]
    if emb_candidates:
        if any(e.model_id == "gemini-embedding-001" for e in emb_candidates):
            rec_emb = "gemini-embedding-001"
        else:
            rec_emb = emb_candidates[0].model_id
    else:
        rec_emb = "gemini-embedding-001"

    return rec_fast, rec_strong, rec_emb


def _read_raw_catalog_file() -> dict[str, Any] | None:
    path = get_model_catalog_path()
    if not path.exists():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("model_catalog.json 로드 실패 (내장 카탈로그 폴백): %s", exc)
        return None


def _write_raw_catalog_file(payload: dict[str, Any]) -> None:
    from app.config import atomic_write_json

    atomic_write_json(get_model_catalog_path(), payload)


def _build_active_warnings(
    cfg: dict[str, Any],
    entries_by_id: dict[str, ModelCatalogEntry],
    rec_fast: str,
    rec_strong: str,
    rec_emb: str,
) -> list[ActiveModelWarning]:
    llm = cfg.get("llm", {})
    pricing_map = llm.get("pricing_per_1m") or {}
    warnings: list[ActiveModelWarning] = []

    slots = [
        ("fast_model", "고속 경량 모델", str(llm.get("fast_model") or ""), rec_fast),
        ("strong_model", "고품질 작성 모델", str(llm.get("strong_model") or ""), rec_strong),
        ("embedding_model", "임베딩 모델", str(llm.get("embedding_model") or ""), rec_emb),
    ]

    for slot, slot_label, model_id, fallback_rec in slots:
        mid = model_id.strip()
        if not mid:
            continue
        entry = entries_by_id.get(mid)
        if entry is not None and entry.deprecated:
            suggested = entry.replacement_model or fallback_rec
            warnings.append(
                ActiveModelWarning(
                    slot=slot,
                    model_id=mid,
                    severity="warning",
                    code="DEPRECATED",
                    message=f"{slot_label}({mid})은 지원 종료(Deprecated) 모델입니다. 권장 모델 '{suggested}'로 전환하세요.",
                    suggested_model=suggested,
                )
            )
        if entry is not None and entry.verified_live is False:
            suggested = entry.replacement_model or fallback_rec
            warnings.append(
                ActiveModelWarning(
                    slot=slot,
                    model_id=mid,
                    severity="error",
                    code="REMOVED_FROM_API",
                    message=f"{slot_label}({mid})이 현재 Gemini API 활성 모델 목록에서 확인되지 않습니다.",
                    suggested_model=suggested,
                )
            )

        p_cfg = pricing_map.get(mid)
        has_cfg_price = isinstance(p_cfg, dict) and (
            float(p_cfg.get("input", 0.0)) > 0.0
            or (slot != "embedding_model" and float(p_cfg.get("output", 0.0)) > 0.0)
        )
        has_cat_price = entry is not None and entry.input_price_per_1m is not None
        if not has_cfg_price and not has_cat_price:
            warnings.append(
                ActiveModelWarning(
                    slot=slot,
                    model_id=mid,
                    severity="warning",
                    code="PRICING_MISSING",
                    message=f"{slot_label}({mid})의 토큰 단가가 등록되지 않아 사용 비용이 0원으로 집계됩니다. 단가표에 등록하거나 공식 사이트 동기화를 실행하세요.",
                    suggested_model="",
                )
            )

    return warnings


def load_model_catalog(cfg: dict[str, Any] | None = None) -> ModelCatalogResponse:
    """저장된 공식 카탈로그(또는 내장 기본값)와 현재 config.json 단가를 병합해 반환한다."""
    cfg = cfg or load_config()
    raw_file = _read_raw_catalog_file()
    pricing_map: dict[str, Any] = cfg.get("llm", {}).get("pricing_per_1m") or {}

    entries_by_id: dict[str, ModelCatalogEntry] = {}
    if raw_file and isinstance(raw_file.get("models"), list) and raw_file["models"]:
        for item in raw_file["models"]:
            if not isinstance(item, dict) or not item.get("model_id"):
                continue
            try:
                entry = ModelCatalogEntry(**item)
                entries_by_id[entry.model_id] = entry
            except Exception:
                continue
    else:
        entries_by_id = _builtin_map()

    # 내장 필수 legacy/embedding 메타 보강 (누락된 경우에만)
    for builtin_id, builtin_entry in _builtin_map().items():
        if builtin_id not in entries_by_id:
            entries_by_id[builtin_id] = builtin_entry

    # config.json의 사용자 단가 수정사항을 카탈로그 표시 단가에 즉시 반영
    for mid, p_val in pricing_map.items():
        if not isinstance(p_val, dict):
            continue
        in_p = float(p_val.get("input", 0.0))
        out_p = float(p_val.get("output", 0.0))
        if mid in entries_by_id:
            cur = entries_by_id[mid]
            if cur.input_price_per_1m != in_p or cur.output_price_per_1m != out_p:
                entries_by_id[mid] = cur.model_copy(
                    update={"input_price_per_1m": in_p, "output_price_per_1m": out_p}
                )
        elif mid.startswith("gemini-") and not _is_excluded_model(mid):
            entries_by_id[mid] = classify_model(
                mid,
                input_price_per_1m=in_p,
                output_price_per_1m=out_p,
                price_note="사용자 단가표 등록",
                source="custom",
            )

    ordered = _sort_entries(list(entries_by_id.values()))
    rec_fast, rec_strong, rec_emb = _pick_recommendations(ordered)
    warnings = _build_active_warnings(cfg, entries_by_id, rec_fast, rec_strong, rec_emb)

    return ModelCatalogResponse(
        updated_at=str((raw_file or {}).get("updated_at") or ""),
        source_urls=list((raw_file or {}).get("source_urls") or [OFFICIAL_PRICING_PAGE_URL]),
        last_sync_status=str((raw_file or {}).get("last_sync_status") or "builtin"),
        last_sync_message=str((raw_file or {}).get("last_sync_message") or "내장 공식 모델 카탈로그(2026-11 기준)를 사용 중입니다."),
        error_code=str((raw_file or {}).get("error_code") or ""),
        recovery_hint=str((raw_file or {}).get("recovery_hint") or ""),
        recommended_fast=rec_fast,
        recommended_strong=rec_strong,
        recommended_embedding=rec_emb,
        models=ordered,
        active_warnings=warnings,
    )


def get_model_pricing(model_id: str, cfg: dict[str, Any] | None = None) -> tuple[float, float] | None:
    """config.json 또는 모델 카탈로그에서 1M 토큰당 (input_usd, output_usd) 단가를 조회한다."""
    mid = (model_id or "").strip()
    if not mid:
        return None
    cfg = cfg or load_config()
    pricing_map = cfg.get("llm", {}).get("pricing_per_1m") or {}
    if mid in pricing_map and isinstance(pricing_map[mid], dict):
        p = pricing_map[mid]
        return float(p.get("input", 0.0)), float(p.get("output", 0.0))

    raw_file = _read_raw_catalog_file()
    candidates = (raw_file or {}).get("models") if raw_file else None
    if isinstance(candidates, list):
        for item in candidates:
            if isinstance(item, dict) and item.get("model_id") == mid and item.get("input_price_per_1m") is not None:
                return float(item["input_price_per_1m"]), float(item.get("output_price_per_1m") or 0.0)

    b = _builtin_map().get(mid)
    if b and b.input_price_per_1m is not None:
        return float(b.input_price_per_1m), float(b.output_price_per_1m or 0.0)
    return None


def get_fallback_pricing(model_id: str, cfg: dict[str, Any] | None = None) -> tuple[float, float]:
    """단가 미등록 모델용 보수적 대체 단가: 같은 종류(채팅/임베딩)의 등록 단가 중 최댓값.

    단가를 모르는 모델의 Live 사용량을 0원으로 기록하면 월 예산 상한이 무력화되므로,
    과소 집계보다 과대 집계 쪽을 택한다.
    """
    cfg = cfg or load_config()
    is_emb = "embedding" in (model_id or "").lower()
    rates: list[tuple[float, float]] = []
    for mid, p in (cfg.get("llm", {}).get("pricing_per_1m") or {}).items():
        if isinstance(p, dict) and (("embedding" in mid.lower()) == is_emb):
            rates.append((float(p.get("input", 0.0) or 0.0), float(p.get("output", 0.0) or 0.0)))
    for entry in BUILTIN_MODEL_CATALOG:
        if entry.input_price_per_1m is not None and ((entry.category == "embedding") == is_emb):
            rates.append((float(entry.input_price_per_1m), float(entry.output_price_per_1m or 0.0)))
    if not rates:
        return (0.20, 0.0) if is_emb else (3.0, 15.0)
    return max(r[0] for r in rates), max(r[1] for r in rates)


async def _fetch_first_valid_pricing(
    client: httpx.AsyncClient,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    checked_urls: list[str] = []
    last_site_err: SiteStructureChangedError | None = None
    last_http_err: str | None = None
    last_net_err: str | None = None

    for url in PRICING_DOC_URLS:
        checked_urls.append(url)
        try:
            resp = await client.get(url, follow_redirects=True, timeout=15.0)
        except httpx.RequestError as exc:
            last_net_err = f"{url}: {exc}"
            continue

        if resp.status_code != 200:
            last_http_err = f"{url} (HTTP {resp.status_code})"
            continue

        try:
            parsed = parse_pricing_document(resp.text, source_url=url)
            return parsed, url, checked_urls
        except SiteStructureChangedError as exc:
            if last_site_err is None:
                last_site_err = exc
            continue

    if last_site_err is not None:
        raise last_site_err
    if last_http_err is not None:
        raise RuntimeError(f"HTTP_ERROR::{last_http_err}")
    raise ConnectionError(last_net_err or "공식 단가 사이트에 연결할 수 없습니다.")


async def _fetch_deprecations_best_effort(client: httpx.AsyncClient) -> tuple[dict[str, str], str | None, list[str]]:
    checked: list[str] = []
    for url in DEPRECATIONS_DOC_URLS:
        checked.append(url)
        try:
            resp = await client.get(url, follow_redirects=True, timeout=12.0)
            if resp.status_code == 200:
                mapping, ok = parse_deprecations_document(resp.text)
                if ok:
                    return mapping, url, checked
        except httpx.RequestError:
            continue
    return {}, None, checked


def _preserve_catalog_models(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_file = _read_raw_catalog_file()
    if raw_file and isinstance(raw_file.get("models"), list) and raw_file["models"]:
        return list(raw_file["models"])
    return [entry.model_dump() for entry in load_model_catalog(cfg).models]


async def sync_model_catalog_from_official(
    http_client: httpx.AsyncClient | None = None,
) -> ModelSyncResponse:
    """
    Google 공식 단가·지원종료 문서 및 Gemini Models API에서 최신 모델 목록·단가를 동기화한다.
    사이트 구조가 변경되었거나 네트워크 오류 시 기존 로컬 설정/단가는 절대 덮어쓰지 않고 명확한 오류를 반환한다.
    """
    cfg = load_config()
    api_key = str(cfg.get("llm", {}).get("api_key") or "").strip()
    now_iso = _now_iso()
    checked_urls: list[str] = []
    warnings: list[str] = []

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        headers={"User-Agent": "SocialBidAI/0.1 (+https://aistudio.google.com)"},
        timeout=15.0,
    )

    try:
        live_models, live_err = await fetch_live_gemini_models(api_key, client)
        if api_key and GEMINI_MODELS_API_URL not in checked_urls:
            checked_urls.append(GEMINI_MODELS_API_URL)
        if live_err:
            warnings.append(live_err)

        dep_map, dep_url, dep_checked = await _fetch_deprecations_best_effort(client)
        checked_urls.extend(dep_checked)
        if not dep_url:
            warnings.append("지원 종료(Deprecations) 문서 구조가 변경되었거나 접근할 수 없어 내장 지원 종료 기준을 유지했습니다.")

        try:
            parsed_pricing, pricing_url, pricing_checked = await _fetch_first_valid_pricing(client)
            checked_urls.extend(pricing_checked)
        except SiteStructureChangedError as exc:
            checked_urls.append(exc.url)
            preserved = _preserve_catalog_models(cfg)
            preserved_map: dict[str, ModelCatalogEntry] = {
                m["model_id"]: ModelCatalogEntry(**m)
                for m in preserved
                if isinstance(m, dict) and m.get("model_id")
            }

            # 단가 사이트 구조가 바뀌었더라도 live API 조회가 성공했다면 활성 모델 여부는 보조 병합(기존 단가는 보존)
            if live_models:
                live_ids = {m["model_id"] for m in live_models}
                for mid, entry in list(preserved_map.items()):
                    preserved_map[mid] = entry.model_copy(update={"verified_live": mid in live_ids})
                for lm in live_models:
                    mid = lm["model_id"]
                    if mid not in preserved_map:
                        preserved_map[mid] = classify_model(
                            mid,
                            display_name=lm["display_name"],
                            input_price_per_1m=None,
                            output_price_per_1m=None,
                            price_note="단가 미수집 (공식 사이트 확인 필요)",
                            verified_live=True,
                            source="api_live",
                        )
                status = "partial_schema_changed"
                msg = (
                    f"공식 단가 사이트 구조가 변경되어 단가 자동 업데이트에 실패했습니다({exc.reason}). "
                    "기존 저장 단가는 유지되며, Gemini API 활성 모델 목록만 보조 갱신했습니다."
                )
            else:
                status = "schema_changed"
                msg = (
                    f"공식 단가 사이트 구조가 변경되어 모델·단가를 자동 파싱할 수 없습니다: {exc.reason} "
                    "기존 저장 설정과 단가는 안전하게 유지되었습니다."
                )

            recovery_hint = (
                f"공식 단가 페이지({OFFICIAL_PRICING_PAGE_URL})를 직접 확인한 뒤, "
                "아래 [모델별 토큰 단가표]에 단가를 수동 입력하고 모델을 [직접 입력]으로 지정하세요."
            )
            _write_raw_catalog_file(
                {
                    "updated_at": now_iso,
                    "source_urls": [exc.url] if exc.url else [OFFICIAL_PRICING_PAGE_URL],
                    "last_sync_status": status,
                    "last_sync_message": msg,
                    "error_code": "SITE_STRUCTURE_CHANGED",
                    "recovery_hint": recovery_hint,
                    "models": [e.model_dump() for e in _sort_entries(list(preserved_map.values()))],
                }
            )
            catalog = load_model_catalog(cfg)
            return ModelSyncResponse(
                ok=False,
                status=status,
                error_code="SITE_STRUCTURE_CHANGED",
                message=msg,
                recovery_hint=recovery_hint,
                warnings=warnings,
                checked_urls=checked_urls,
                updated_models_count=0,
                updated_pricing_count=0,
                catalog=catalog,
            )

        except RuntimeError as exc:
            err_str = str(exc)
            detail = err_str.split("::", 1)[1] if "::" in err_str else err_str
            preserved = _preserve_catalog_models(cfg)
            msg = f"공식 사이트가 오류 응답을 반환하여 동기화하지 못했습니다 ({detail}). 기존 설정과 단가를 그대로 유지합니다."
            recovery_hint = f"잠시 후 다시 시도하거나 공식 사이트({OFFICIAL_PRICING_PAGE_URL}) 접속 상태를 확인하세요."
            _write_raw_catalog_file(
                {
                    "updated_at": now_iso,
                    "source_urls": [OFFICIAL_PRICING_PAGE_URL],
                    "last_sync_status": "http_error",
                    "last_sync_message": msg,
                    "error_code": "HTTP_ERROR",
                    "recovery_hint": recovery_hint,
                    "models": preserved,
                }
            )
            return ModelSyncResponse(
                ok=False,
                status="http_error",
                error_code="HTTP_ERROR",
                message=msg,
                recovery_hint=recovery_hint,
                warnings=warnings,
                checked_urls=checked_urls,
                catalog=load_model_catalog(cfg),
            )

        except ConnectionError as exc:
            preserved = _preserve_catalog_models(cfg)
            msg = f"네트워크 연결 문제로 공식 사이트에 접속하지 못했습니다 ({exc}). 기존 로컬 카탈로그와 단가를 유지합니다."
            recovery_hint = "인터넷 연결 또는 방화벽·프록시 설정을 확인한 뒤 다시 동기화를 시도하세요."
            _write_raw_catalog_file(
                {
                    "updated_at": now_iso,
                    "source_urls": [OFFICIAL_PRICING_PAGE_URL],
                    "last_sync_status": "network_error",
                    "last_sync_message": msg,
                    "error_code": "NETWORK_ERROR",
                    "recovery_hint": recovery_hint,
                    "models": preserved,
                }
            )
            return ModelSyncResponse(
                ok=False,
                status="network_error",
                error_code="NETWORK_ERROR",
                message=msg,
                recovery_hint=recovery_hint,
                warnings=warnings,
                checked_urls=checked_urls,
                catalog=load_model_catalog(cfg),
            )

        # 정상 동기화 병합
        builtin = _builtin_map()
        merged: dict[str, ModelCatalogEntry] = dict(builtin)
        live_by_id = {m["model_id"]: m for m in live_models}

        # 내장 모델 중 공식 단가·API 둘 다에 없는 비활성 모델은 그대로 두되 live 여부 표시
        if live_models:
            for mid, entry in list(merged.items()):
                merged[mid] = entry.model_copy(update={"verified_live": mid in live_by_id})

        updated_pricing_count = 0
        new_pricing_map = dict(cfg.get("llm", {}).get("pricing_per_1m") or {})

        for item in parsed_pricing:
            mid = item["model_id"]
            in_p = float(item["input_price"])
            out_p = float(item["output_price"])
            prev = merged.get(mid)
            is_dep = bool(mid in dep_map or (prev.deprecated if prev else False))
            repl = dep_map.get(mid) or (prev.replacement_model if prev else "")
            v_live = (mid in live_by_id) if live_models else (prev.verified_live if prev else None)
            disp = (live_by_id[mid]["display_name"] if mid in live_by_id else (prev.display_name if prev else ""))
            parsed_note = str(item.get("price_note") or "").strip()
            if parsed_note and parsed_note != "공식 사이트 동기화":
                note = parsed_note
            elif prev and prev.price_note and not prev.deprecated:
                note = prev.price_note
            else:
                note = "공식 사이트 동기화"

            merged[mid] = classify_model(
                mid,
                display_name=disp,
                input_price_per_1m=in_p,
                output_price_per_1m=out_p,
                price_note=note,
                deprecated=is_dep,
                replacement_model=repl,
                verified_live=v_live,
                source="official_sync",
            )
            new_pricing_map[mid] = {"input": in_p, "output": out_p}
            updated_pricing_count += 1

        # 지원 종료 매핑 반영
        for old_id, new_id in dep_map.items():
            if old_id in merged:
                cur = merged[old_id]
                merged[old_id] = cur.model_copy(
                    update={
                        "deprecated": True,
                        "recommended_tier": "legacy",
                        "tier_badge": "지원 종료 예정",
                        "replacement_model": new_id,
                    }
                )

        # live API에만 존재하는 신규 모델(단가 페이지에 아직 없는 Preview 등)도 선택 가능하게 추가
        for mid, lm in live_by_id.items():
            if mid not in merged:
                existing_p = new_pricing_map.get(mid)
                merged[mid] = classify_model(
                    mid,
                    display_name=lm["display_name"],
                    input_price_per_1m=float(existing_p["input"]) if isinstance(existing_p, dict) else None,
                    output_price_per_1m=float(existing_p["output"]) if isinstance(existing_p, dict) else None,
                    price_note="Gemini API 활성 모델 (공식 단가표 미기재)",
                    verified_live=True,
                    source="api_live",
                )

        # config.json에 최신 단가 자동 반영 (사용자 기타 설정은 유지)
        next_cfg = dict(cfg)
        next_llm = dict(next_cfg.get("llm") or {})
        next_llm["pricing_per_1m"] = new_pricing_map
        next_cfg["llm"] = next_llm
        cfg = save_config(next_cfg)

        ordered = _sort_entries(list(merged.values()))
        source_urls = [u for u in [pricing_url, dep_url, GEMINI_MODELS_API_URL if live_models else None] if u]
        sync_msg = (
            f"공식 문서({pricing_url})에서 단가 {updated_pricing_count}개 모델을 동기화했습니다."
            + (f" (API 활성 모델 {len(live_models)}개 검증 완료)" if live_models else "")
        )

        _write_raw_catalog_file(
            {
                "updated_at": now_iso,
                "source_urls": source_urls,
                "last_sync_status": "ok",
                "last_sync_message": sync_msg,
                "error_code": "",
                "recovery_hint": "",
                "models": [e.model_dump() for e in ordered],
            }
        )
        catalog = load_model_catalog(cfg)
        return ModelSyncResponse(
            ok=True,
            status="ok",
            error_code="",
            message=sync_msg,
            recovery_hint="",
            warnings=warnings,
            checked_urls=checked_urls,
            updated_models_count=len(ordered),
            updated_pricing_count=updated_pricing_count,
            catalog=catalog,
        )
    finally:
        if owns_client:
            await client.aclose()
