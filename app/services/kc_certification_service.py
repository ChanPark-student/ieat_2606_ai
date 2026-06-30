from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.response import (
    CertificationDiagnosis,
    KcCertificationSummary,
    LegalProductCandidate,
)

logger = logging.getLogger(__name__)

_MAX_MODELS = 5
_NOTE = (
    "KC 인증정보는 유사 인증사례 확인용 보조 근거이며, "
    "실제 인증 가능 여부나 접수 가능 기관은 현재 지정기관 업무범위와 관계 기관 확인이 필요합니다."
)
_NOTE_NO_DATA = (
    "유사 KC 인증사례 정보가 없습니다. "
    "관계 기관 또는 SafetyKorea에서 직접 확인해주세요."
)

_LEVEL_PRIORITY = {"CONFIRMED": 0, "CANDIDATE": 1, "NEEDS_CONFIRMATION": 2}

# 법정 품목명 정규화 시 제거할 접두사 (길이 내림차순 — 긴 것부터 시도)
_STRIP_PREFIXES = (
    "어린이용 ", "유아용 ", "아동용 ", "어린이 ", "유아 ", "아동 ",
)


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _normalize_name(name: str) -> str:
    """법정 품목명 정규화: 앞 접두사 제거 + 괄호 내 공백 통일."""
    n = name.strip()
    for prefix in _STRIP_PREFIXES:
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    # 괄호 내 공백 정규화 "(보호 장구" → "(보호장구" 방향으로 비교할 때 사용
    return n


def _find_kc_match(legal_name: str, kc_agg: Dict[str, Any]) -> Optional[str]:
    """법정 품목명을 KC 집계 인덱스 키에 3단계 매칭.

    1. 정확 일치
    2. substring 포함 관계 (A in B, B in A)
    3. 정규화 후 정확 일치 또는 substring
    반환: 매칭된 KC 카테고리 키, 없으면 None
    """
    if not legal_name or not kc_agg:
        return None

    # 1. 정확 일치
    if legal_name in kc_agg:
        return legal_name

    # 2. substring 포함
    for key in kc_agg:
        if key in legal_name or legal_name in key:
            return key

    # 3. 정규화 후 비교
    norm_legal = _normalize_name(legal_name)
    for key in kc_agg:
        norm_key = _normalize_name(key)
        if norm_legal == norm_key:
            return key
        if norm_legal and norm_key and (norm_legal in norm_key or norm_key in norm_legal):
            return key

    return None


def _pick_target_names(candidates: List[LegalProductCandidate]) -> List[str]:
    """검색 대상 법정 품목명 목록.

    CONFIRMED·CANDIDATE → 해당 품목들.
    NEEDS_CONFIRMATION만 → 최고점 1개 (과도한 union 방지).
    """
    if not candidates:
        return []
    valid = [c for c in candidates if c.confidence_level in ("CONFIRMED", "CANDIDATE")]
    if valid:
        return list(dict.fromkeys(_safe_str(c.legal_product_name) for c in valid if c.legal_product_name))
    top = max(candidates, key=lambda c: c.confidence_score)
    name = _safe_str(top.legal_product_name)
    return [name] if name else []


def _format_sample(cert: Dict[str, Any]) -> str:
    """KC sample certification을 representative_models 문자열로 변환."""
    model = _safe_str(cert.get("modelName"))
    cert_num = _safe_str(cert.get("certNum"))
    organ = _safe_str(cert.get("certOrganName"))
    if "(" in organ:
        organ_short = organ[organ.rfind("(")+1:organ.rfind(")")]
    else:
        organ_short = organ
    state = _safe_str(cert.get("certState"))
    cert_date = _safe_str(cert.get("certDate"))
    import_div = _safe_str(cert.get("importDiv"))

    # 날짜 포맷: "20151123" → "2015-11-23"
    if len(cert_date) == 8 and cert_date.isdigit():
        cert_date = f"{cert_date[:4]}-{cert_date[4:6]}-{cert_date[6:]}"

    parts = []
    if model:
        parts.append(model)
    details: List[str] = []
    if cert_num:
        details.append(f"인증번호: {cert_num}")
    if organ_short:
        details.append(f"기관: {organ_short}")
    if state:
        details.append(f"상태: {state}")
    if cert_date:
        details.append(f"인증일: {cert_date}")
    if import_div:
        details.append(f"{import_div}")
    if details:
        parts.append(f"({', '.join(details)})")
    return " ".join(parts) if parts else ""


def get_kc_summary(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
    app_data: Dict[str, Any],
) -> Tuple[KcCertificationSummary, List[str]]:
    """Phase 6: KC 유사 인증사례 요약.

    kc_agg (main.py 시작 시 집계된 compact index) 기반으로 법정 품목명 후보를 검색.
    데이터 없는 모델명·인증번호·기관명은 생성하지 않음.
    KC 인증정보는 보조 참고자료 — 인증 가능 여부 확정 표현 금지.
    """
    kc_agg: Dict[str, Any] = (app_data or {}).get("kc_agg") or {}

    empty = KcCertificationSummary(
        similar_cert_count=0,
        top_cert_organ_names=[],
        representative_models=[],
        note=_NOTE_NO_DATA,
    )

    if not kc_agg:
        logger.info("Phase 6: kc_agg 없음 → 빈 KC 요약 반환")
        return empty, []

    target_names = _pick_target_names(candidates)
    if not target_names:
        logger.info("Phase 6: 검색 대상 법정 품목명 없음")
        return empty, []

    # 첫 번째로 매칭되는 KC 카테고리 사용
    matched_kc_key: Optional[str] = None
    matched_legal_name: str = ""
    for name in target_names:
        key = _find_kc_match(name, kc_agg)
        if key:
            matched_kc_key = key
            matched_legal_name = name
            break

    if matched_kc_key is None:
        logger.info("Phase 6: '%s' 관련 KC 카테고리 없음", ", ".join(target_names))
        return empty, []

    entry = kc_agg[matched_kc_key]
    cert_count: int = entry.get("total") or 0
    top_organs: List[str] = entry.get("top_organs") or []
    samples: List[Dict[str, Any]] = entry.get("samples") or []

    logger.info(
        "Phase 6: KC 매칭 법정품목='%s' → KC카테고리='%s' / %d건",
        matched_legal_name, matched_kc_key, cert_count,
    )

    # representative_models: 모델명 있는 항목 우선
    rep_models: List[str] = []
    seen_models: set = set()
    for cert in samples:
        if not isinstance(cert, dict):
            continue
        model = _safe_str(cert.get("modelName"))
        if model and model in seen_models:
            continue
        formatted = _format_sample(cert)
        if formatted:
            if model:
                seen_models.add(model)
            rep_models.append(formatted)
        if len(rep_models) >= _MAX_MODELS:
            break

    # source_refs
    source_refs: List[str] = [
        f"kc_certification:{matched_kc_key}:{cert_count}건",
    ]

    return (
        KcCertificationSummary(
            similar_cert_count=cert_count,
            top_cert_organ_names=top_organs,
            representative_models=rep_models,
            note=_NOTE,
        ),
        source_refs,
    )
