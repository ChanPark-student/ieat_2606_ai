from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from app.schemas.request import DiagnosisRequest
from app.schemas.response import LegalProductCandidate

logger = logging.getLogger(__name__)

# 검색 대상 필드와 가중치. 존재하지 않는 필드는 자동으로 건너뜀.
_SEARCHABLE_FIELDS: Tuple[Tuple[str, float], ...] = (
    ("legal_product_name", 3.0),
    ("display_product_name", 3.0),
    ("user_expression", 2.0),
    ("normalized_expression", 2.0),
    ("aliases", 2.0),
    ("keywords", 1.0),
    ("hazard_keywords", 1.0),
)

# 순수 질의 filler — 제품 구분과 무관한 일반 표현. 특정 입력을 막는 것이 아니라
# 품목 구분력이 본질적으로 없는 단어만 보조 신호에서 제외한다.
# (대부분 데이터에 등장하지 않아 df=0이므로 자동 제외되지만, 명시적으로도 차단)
_FILLER_TOKENS = frozenset({
    "정확한", "정체불명", "모르겠습니다", "모르겠어요", "합니다", "하려고",
    "출시", "출시하려고", "사용", "사용하는", "사용하려고", "수입", "수입하려고",
    "제조", "제조하려고", "있습니다", "입니다", "관련", "대해", "위한", "위해",
    "그리고", "이며", "또는", "제품을", "품목을", "품목", "물건", "물건을",
})

_TOKEN_SPLIT_RE = re.compile(r"[\s,/;:|()\[\]{}<>\"'`~!?.\-_+=]+")
_MIN_TOKEN_LEN = 2
_MAX_CANDIDATES = 5

# 데이터 기반 신호 판정 파라미터
# 토큰이 전체 품목의 LOW_SIGNAL_DF_RATIO 초과 비율에 등장하면 low-signal로 down-weight.
# "어린이용"처럼 수십 개 품목에 공통으로 들어가는 일반어를 자동 식별한다.
_LOW_SIGNAL_DF_RATIO = 0.25
_LOW_SIGNAL_MULT = 0.15
_SUBSTRING_BONUS = 8.0

# 핸드오프 §6 Phase 2 기준
_CONFIRMED_THRESHOLD = 0.7
_CANDIDATE_THRESHOLD = 0.4
# 상위 후보가 거의 동점으로 N개 이상이면 확정하지 않고 확인 요청
_NEAR_TIE_RATIO = 0.85
_NEAR_TIE_MIN_COUNT = 3
# CONFIRMED/CANDIDATE 허용을 위해 최소 1개의 high-signal 토큰 매칭 필요
_MIN_HIGH_SIGNAL = 1


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


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _build_product_texts(index_data: List[Dict[str, Any]]) -> List[str]:
    """legal_product_name 별로 결합한 검색 텍스트 목록.

    각 원소는 한 품목의 모든 검색 필드를 합쳐 소문자화한 문자열.
    document frequency를 substring 기준으로 세는 데 사용한다.
    """
    product_texts: Dict[str, List[str]] = {}
    for item in index_data:
        if not isinstance(item, dict):
            continue
        legal_name = _safe_str(item.get("legal_product_name"))
        if not legal_name:
            continue
        combined = " ".join(_field_text(item.get(f)) for f, _ in _SEARCHABLE_FIELDS)
        product_texts.setdefault(legal_name, []).append(combined)
    return [" ".join(texts) for texts in product_texts.values()]


def _token_signal(token: str, product_texts: List[str], low_signal_df: float) -> float:
    """토큰의 신호 강도. 1.0=high-signal, _LOW_SIGNAL_MULT=low-signal, 0.0=무시.

    document frequency를 substring 기준으로 계산한다 — scoring(`tok in field_text`)과
    동일한 기준이라, '내의'처럼 '내의류(...)' 복합어로만 존재하는 토큰도 일관되게 처리된다.
    """
    if token in _FILLER_TOKENS:
        return 0.0
    d = sum(1 for txt in product_texts if token in txt)
    if d == 0:
        return 0.0  # 데이터에 없는 토큰 (어차피 매칭되지 않음)
    if d >= low_signal_df:
        return _LOW_SIGNAL_MULT
    return 1.0


def _score_entry(
    entry: Dict[str, Any],
    tokens: List[str],
    raw_product_name: str,
    product_texts: List[str],
    low_signal_df: float,
) -> Tuple[float, List[str], List[str]]:
    """단일 인덱스 항목 채점 → (raw_score, matched_high_signal, matched_low_signal)."""
    field_text_cache = {f: _field_text(entry.get(f)) for f, _ in _SEARCHABLE_FIELDS}

    raw_score = 0.0
    matched_hi: List[str] = []
    matched_lo: List[str] = []

    for tok in tokens:
        sig = _token_signal(tok, product_texts, low_signal_df)
        if sig == 0.0:
            continue
        # 한 토큰은 등장하는 필드 중 최대 가중치 1개만 반영 (필드 간 중복 합산 방지)
        best_w = 0.0
        for field, weight in _SEARCHABLE_FIELDS:
            if tok in field_text_cache[field] and weight > best_w:
                best_w = weight
        if best_w == 0.0:
            continue
        raw_score += best_w * sig
        if sig >= 1.0:
            matched_hi.append(tok)
        else:
            matched_lo.append(tok)

    # 부분일치 보너스: high-signal 토큰을 포함하는 표현이 product_name과
    # substring 관계일 때만 부여 ("어린이용" 같은 low-signal로는 부여하지 않음)
    rp = raw_product_name.strip().lower()
    if rp:
        for field in ("user_expression", "normalized_expression", "aliases"):
            val = entry.get(field)
            if val is None:
                continue
            values = [val] if isinstance(val, str) else (
                list(val) if isinstance(val, (list, tuple)) else [str(val)]
            )
            granted = False
            for v in values:
                vl = str(v).strip().lower()
                if not vl:
                    continue
                if (vl in rp or rp in vl) and any(
                    _token_signal(t, product_texts, low_signal_df) >= 1.0 for t in _tokenize(vl)
                ):
                    raw_score += _SUBSTRING_BONUS
                    granted = True
                    break
            if granted:
                break

    return raw_score, matched_hi, matched_lo


def _classify(
    norm_score: float,
    matched_hi: List[str],
    multi_near_tie: bool,
) -> Tuple[str, bool]:
    """confidence_score + high-signal 매칭 여부 → (level, needs_user_confirmation).

    핸드오프 §6 Phase 2: CONFIRMED / CANDIDATE / NEEDS_CONFIRMATION
    - high-signal 토큰이 1개도 없으면 CONFIRMED/CANDIDATE 금지
    - 상위 후보가 거의 동점으로 다수면(multi_near_tie) 확정 금지
    """
    has_hi = len(matched_hi) >= _MIN_HIGH_SIGNAL
    if norm_score >= _CONFIRMED_THRESHOLD and has_hi and not multi_near_tie:
        return "CONFIRMED", False
    if norm_score >= _CANDIDATE_THRESHOLD and has_hi:
        return "CANDIDATE", True
    return "NEEDS_CONFIRMATION", True


def match_category(
    request: DiagnosisRequest,
    index_data: Any,
) -> List[LegalProductCandidate]:
    """product_category_index.json으로부터 법정 품목명 후보 매칭.

    - 데이터에 없는 값은 만들어내지 않는다.
    - 매칭이 없으면 빈 리스트 반환 (더미 추가 금지).
    - JSON 스키마가 예상과 달라도 예외를 던지지 않는다.
    - legal_product_name 기준 dedup 후 최고 점수 항목만 후보로 반환한다.
    - "어린이용" 같은 저신호 일반어는 데이터 기반(df)으로 자동 down-weight한다.
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

    # 데이터 기반 product별 결합 텍스트 (substring df 계산용)
    try:
        product_texts = _build_product_texts(index_data)
    except Exception as e:
        logger.warning("product 텍스트 구축 실패, 빈 후보 반환: %s", e)
        return []
    total_products = len(product_texts)
    if total_products == 0:
        return []
    low_signal_df = max(2.0, _LOW_SIGNAL_DF_RATIO * total_products)

    # 의미 있는(high-signal) 토큰이 하나도 없으면 후보를 만들지 않음
    has_any_high_signal = any(
        _token_signal(t, product_texts, low_signal_df) >= 1.0 for t in deduped_tokens
    )

    # legal_product_name 기준 dedup: 품목별 최고 점수 항목만 유지
    best_per_product: Dict[str, Tuple[float, List[str], List[str], Dict[str, Any]]] = {}
    for item in index_data:
        if not isinstance(item, dict):
            continue
        raw_score, matched_hi, matched_lo = _score_entry(
            item, deduped_tokens, raw_product_name, product_texts, low_signal_df
        )
        if raw_score <= 0:
            continue
        legal_name = _safe_str(item.get("legal_product_name"))
        if not legal_name:
            continue
        existing = best_per_product.get(legal_name)
        if existing is None or raw_score > existing[0]:
            best_per_product[legal_name] = (raw_score, matched_hi, matched_lo, item)

    if not best_per_product:
        return []

    ranked = sorted(best_per_product.values(), key=lambda x: x[0], reverse=True)

    max_raw = ranked[0][0]
    norm_base = max(max_raw, 8.0)

    # near-tie 판정: 상위에 max_raw의 _NEAR_TIE_RATIO 이상인 후보가 다수인가
    near_tie_count = sum(1 for r in ranked if r[0] >= max_raw * _NEAR_TIE_RATIO)
    multi_near_tie = near_tie_count >= _NEAR_TIE_MIN_COUNT

    candidates: List[LegalProductCandidate] = []
    for raw_score, matched_hi, matched_lo, item in ranked[:_MAX_CANDIDATES]:
        norm_score = max(0.0, min(1.0, raw_score / norm_base))

        # high-signal 토큰이 전혀 없는 입력이면 모든 후보를 확인 요청 수준으로 강등
        if not has_any_high_signal:
            level, needs_confirm = "NEEDS_CONFIRMATION", True
        else:
            level, needs_confirm = _classify(norm_score, matched_hi, multi_near_tie)

        legal_name = _safe_str(item.get("legal_product_name"))
        display_name = _safe_str(item.get("display_product_name")) or legal_name
        cert_type = _safe_str(item.get("certification_type"))

        basis_bits: List[str] = []
        if matched_hi:
            basis_bits.append(f"핵심 매칭 토큰: {', '.join(matched_hi)}")
        if matched_lo:
            basis_bits.append(f"보조 토큰: {', '.join(matched_lo)}")
        if multi_near_tie:
            basis_bits.append("유사 점수 후보 다수 → 사용자 확인 필요")
        match_basis = " / ".join(basis_bits) if basis_bits else "매칭 근거 없음"

        try:
            candidates.append(
                LegalProductCandidate(
                    legal_product_name=legal_name,
                    display_product_name=display_name,
                    certification_type=cert_type,
                    confidence_level=level,
                    confidence_score=round(norm_score, 4),
                    needs_user_confirmation=needs_confirm,
                    match_basis=match_basis,
                )
            )
        except Exception as e:
            logger.warning("Skipping malformed category index item: %s", e)
            continue

    return candidates
