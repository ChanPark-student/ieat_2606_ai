from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from app.schemas.request import DiagnosisRequest
from app.schemas.response import LegalProductCandidate

logger = logging.getLogger(__name__)

# 검색 대상 필드와 가중치
# 존재하지 않는 필드는 자동으로 건너뜀
_SEARCHABLE_FIELDS: Tuple[Tuple[str, float], ...] = (
    ("legal_product_name", 3.0),
    ("display_product_name", 3.0),
    ("user_expression", 2.0),
    ("normalized_expression", 2.0),
    ("aliases", 2.0),
    ("keywords", 1.0),
    ("hazard_keywords", 1.0),
)

# product_name의 부분 문자열 보너스: user_expression/aliases가 입력 product_name에 포함되거나
# 그 반대일 때 적용. 구체적 의도를 포착하는 가장 강한 신호.
_SUBSTRING_BONUS = 8.0
# legal/display name 완전 일치 보너스
_EXACT_MATCH_BONUS = 5.0

_TOKEN_SPLIT_RE = re.compile(r"[\s,/;:|()\[\]{}<>\"'`~!?.\-_+=]+")
_MIN_TOKEN_LEN = 2
_MAX_CANDIDATES = 5

# 핸드오프 §6 Phase 2 기준
_CONFIRMED_THRESHOLD = 0.7
_CANDIDATE_THRESHOLD = 0.4


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    parts = _TOKEN_SPLIT_RE.split(text.lower())
    return [p for p in parts if len(p) >= _MIN_TOKEN_LEN]


def _field_text(value: Any) -> str:
    """필드 값을 검색용 단일 문자열로 변환. list/str/None 모두 안전 처리."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, (list, tuple, set)):
        return " ".join(_field_text(v) for v in value)
    return str(value).lower()


def _score_item(
    item: Dict[str, Any],
    query_tokens: List[str],
    raw_product_name: str,
) -> Tuple[float, List[str]]:
    """단일 인덱스 항목에 대한 (raw_score, matched_fields) 계산."""
    if not query_tokens:
        return 0.0, []

    raw_score = 0.0
    matched_fields: List[str] = []

    # ── 1. 토큰 매칭 점수 ──────────────────────────────────────────────────
    for field, weight in _SEARCHABLE_FIELDS:
        if field not in item:
            continue
        haystack = _field_text(item.get(field))
        if not haystack:
            continue
        hits = sum(1 for tok in query_tokens if tok and tok in haystack)
        if hits > 0:
            raw_score += weight * hits
            matched_fields.append(field)

    # ── 2. product_name 부분 문자열 보너스 ────────────────────────────────
    # user_expression / normalized_expression / aliases 가 product_name에 포함되거나
    # 반대로 product_name이 해당 값에 포함되면 강한 신호
    if raw_product_name:
        rp = raw_product_name.strip().lower()
        _bonus_added = False
        for field in ("user_expression", "normalized_expression", "aliases"):
            if _bonus_added:
                break
            val = item.get(field)
            if val is None:
                continue
            values = [val] if isinstance(val, str) else (list(val) if isinstance(val, (list, tuple)) else [str(val)])
            for v in values:
                v_lower = str(v).strip().lower()
                if not v_lower:
                    continue
                if v_lower in rp or rp in v_lower:
                    raw_score += _SUBSTRING_BONUS
                    if field not in matched_fields:
                        matched_fields.append(field + "(직접일치)")
                    _bonus_added = True
                    break

    # ── 3. legal/display name 완전 일치 보너스 ────────────────────────────
    if raw_product_name:
        rp = raw_product_name.strip().lower()
        for field in ("legal_product_name", "display_product_name"):
            val = item.get(field)
            if isinstance(val, str) and val.strip().lower() == rp:
                raw_score += _EXACT_MATCH_BONUS
                if field not in matched_fields:
                    matched_fields.append(field + "(완전일치)")

    return raw_score, matched_fields


def _confidence_level(score: float) -> Tuple[str, bool]:
    """confidence_score → (level, needs_user_confirmation).

    핸드오프 §6 Phase 2 기준:
      CONFIRMED          : 명확하게 매칭됨
      CANDIDATE          : 후보로 제시 가능함
      NEEDS_CONFIRMATION : 사용자 추가 확인 필요
    """
    if score >= _CONFIRMED_THRESHOLD:
        return "CONFIRMED", False
    if score >= _CANDIDATE_THRESHOLD:
        return "CANDIDATE", True
    return "NEEDS_CONFIRMATION", True


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def match_category(
    request: DiagnosisRequest,
    index_data: Any,
) -> List[LegalProductCandidate]:
    """product_category_index.json으로부터 법정 품목명 후보 매칭.

    - 데이터에 없는 값은 만들어내지 않는다.
    - 매칭이 없으면 빈 리스트 반환 (더미 추가 금지).
    - JSON 스키마가 예상과 달라도 예외를 던지지 않는다.
    - legal_product_name 기준으로 dedup하여 최고 점수 항목만 후보로 반환한다.
    """
    if not isinstance(index_data, list) or not index_data:
        return []

    raw_product_name = _safe_str(getattr(request, "product_name", "")).strip()
    query_text_parts = [
        raw_product_name,
        _safe_str(getattr(request, "user_query", "")),
        _safe_str(getattr(request, "material_text", "")),
    ]
    tokens = _tokenize(" ".join(part for part in query_text_parts if part))
    if not tokens:
        return []

    # 토큰 중복 제거, 순서 유지
    seen: set[str] = set()
    deduped_tokens: List[str] = []
    for tok in tokens:
        if tok not in seen:
            seen.add(tok)
            deduped_tokens.append(tok)

    # ── legal_product_name 기준 dedup ─────────────────────────────────────
    # 978개 항목 중 동일 legal_product_name이 수십~수백 개씩 존재.
    # 각 product별 최고 점수 항목(best entry)만 후보로 유지한다.
    best_per_product: Dict[str, Tuple[float, List[str], Dict[str, Any]]] = {}

    for item in index_data:
        if not isinstance(item, dict):
            continue
        raw_score, matched_fields = _score_item(item, deduped_tokens, raw_product_name)
        if raw_score <= 0:
            continue
        legal_name = _safe_str(item.get("legal_product_name"))
        if not legal_name:
            continue
        existing = best_per_product.get(legal_name)
        if existing is None or raw_score > existing[0]:
            best_per_product[legal_name] = (raw_score, matched_fields, item)

    if not best_per_product:
        return []

    # 점수 기준 정렬
    sorted_products = sorted(best_per_product.values(), key=lambda x: x[0], reverse=True)

    # 정규화 기준: 최고 점수 항목
    max_raw = sorted_products[0][0]
    if max_raw <= 0:
        return []

    candidates: List[LegalProductCandidate] = []
    for raw_score, matched_fields, item in sorted_products[:_MAX_CANDIDATES]:
        norm_score = max(0.0, min(1.0, raw_score / max(max_raw, 8.0)))
        level, needs_confirm = _confidence_level(norm_score)

        legal_name = _safe_str(item.get("legal_product_name"))
        display_name = _safe_str(item.get("display_product_name")) or legal_name
        cert_type = _safe_str(item.get("certification_type"))

        basis = (
            "검색 일치 필드: " + ", ".join(matched_fields)
            if matched_fields
            else "검색 일치 필드 없음"
        )

        try:
            candidates.append(
                LegalProductCandidate(
                    legal_product_name=legal_name,
                    display_product_name=display_name,
                    certification_type=cert_type,
                    confidence_level=level,
                    confidence_score=round(norm_score, 4),
                    needs_user_confirmation=needs_confirm,
                    match_basis=basis,
                )
            )
        except Exception as e:
            logger.warning("Skipping malformed category index item: %s", e)
            continue

    return candidates
