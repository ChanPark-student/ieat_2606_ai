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
_NOTE_SELF_CONFORMITY = (
    "해당 품목은 공급자적합성확인 대상이므로 KC 인증정보 데이터에서 동일 품목 유사 인증사례가 "
    "제한적으로 확인될 수 있습니다. 시험성적서 및 적합성 입증자료 확보가 필요합니다."
)

_LEVEL_PRIORITY = {"CONFIRMED": 0, "CANDIDATE": 1, "NEEDS_CONFIRMATION": 2}


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _find_kc_match(legal_name: str, kc_agg: Dict[str, Any]) -> Optional[str]:
    """법정 품목명을 KC 집계 인덱스 키에 2단계 매칭.

    1. 정확 일치
    2. substring 포함 관계 (A in B 또는 B in A)

    접두사 제거 정규화(3단계)는 의도적으로 제외한다.
    "아동용 섬유제품"과 "유아용 섬유제품"처럼 법정 품목군이 다른 항목이
    접두사 제거 후 "섬유제품"으로 동일시되어 잘못 매칭되는 문제를 방지하기 위함.
    KC 데이터에 정확한 카테고리가 없으면 None을 반환한다.
    """
    if not legal_name or not kc_agg:
        return None

    # 1. 정확 일치
    if legal_name in kc_agg:
        return legal_name

    # 2. substring 포함 — 한쪽이 다른 쪽에 완전히 포함될 때만 허용
    #    예: KC "물안경" ⊂ 법정 "어린이용 물안경" → 매칭 O
    #    반례: "아동용 섬유제품" vs "유아용 섬유제품" → 둘 다 방향 불성립 → 매칭 X
    for key in kc_agg:
        if key in legal_name or legal_name in key:
            return key

    return None


def _pick_target_names(candidates: List[LegalProductCandidate]) -> List[str]:
    """검색 대상 법정 품목명 목록.

    각 신뢰도 레벨에서 최고점 1개만 반환.
    → 아동용/유아용 섬유제품처럼 유사 품목명 간 KC 데이터 교차 매칭 방지.
    CONFIRMED 존재 → 최고점 CONFIRMED 1개.
    CONFIRMED 없고 CANDIDATE 존재 → 최고점 CANDIDATE 1개.
    둘 다 없음 → 최고점 NEEDS_CONFIRMATION 1개.
    """
    if not candidates:
        return []
    for level in ("CONFIRMED", "CANDIDATE"):
        group = [c for c in candidates if c.confidence_level == level]
        if group:
            top = max(group, key=lambda c: c.confidence_score)
            name = _safe_str(top.legal_product_name)
            return [name] if name else []
    top = max(candidates, key=lambda c: c.confidence_score)
    name = _safe_str(top.legal_product_name)
    return [name] if name else []


def _sample_relevance(sample: Dict[str, Any], query_words: set) -> int:
    """query 키워드와 sample 모델명/제품명의 겹치는 단어 수 반환 (정렬용 점수)."""
    model = (sample.get("modelName") or "").lower()
    product = (sample.get("productName") or "").lower()
    combined = model + " " + product
    return sum(1 for w in query_words if len(w) > 1 and w in combined)


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
    query_text: str = "",
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
        matched_category="",
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
        # 공급자적합성확인 대상은 KC 인증 데이터에 해당 카테고리가 없는 것이 제도적으로 자연스러움
        cert_type = (cert_diagnosis.certification_type or "").strip()
        if cert_type == "공급자적합성확인":
            return KcCertificationSummary(
                similar_cert_count=0,
                top_cert_organ_names=[],
                representative_models=[],
                note=_NOTE_SELF_CONFORMITY,
                matched_category="",
            ), []
        return empty, []

    entry = kc_agg[matched_kc_key]
    cert_count: int = entry.get("total") or 0
    top_organs: List[str] = entry.get("top_organs") or []
    samples: List[Dict[str, Any]] = entry.get("samples") or []

    logger.info(
        "Phase 6: KC 매칭 법정품목='%s' → KC카테고리='%s' / %d건",
        matched_legal_name, matched_kc_key, cert_count,
    )

    # query 키워드로 samples 정렬: 입력 제품과 연관도 높은 사례를 먼저 표시
    query_words = {w for w in query_text.lower().split() if len(w) > 1} if query_text else set()
    if query_words:
        samples_sorted = sorted(
            (s for s in samples if isinstance(s, dict)),
            key=lambda s: _sample_relevance(s, query_words),
            reverse=True,
        )
    else:
        samples_sorted = [s for s in samples if isinstance(s, dict)]

    # representative_models: 정렬된 순서로 모델명 있는 항목 우선, 최대 _MAX_MODELS개
    rep_models: List[str] = []
    seen_models: set = set()
    for cert in samples_sorted:
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
            matched_category=matched_kc_key,
        ),
        source_refs,
    )
