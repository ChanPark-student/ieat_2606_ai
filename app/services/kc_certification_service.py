from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from app.schemas.response import (
    CertificationDiagnosis,
    KcCertificationSummary,
    LegalProductCandidate,
)

logger = logging.getLogger(__name__)

_KC_DOCUMENT_TYPE = "KC_CERTIFICATION_SUMMARY"
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


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _build_kc_index(rag_chunks: List[Any]) -> Dict[str, Dict[str, Any]]:
    """rag_chunk_all에서 KC_CERTIFICATION_SUMMARY 청크를 추출해 product_name → chunk 인덱스 생성."""
    index: Dict[str, Dict[str, Any]] = {}
    if not isinstance(rag_chunks, list):
        return index
    for chunk in rag_chunks:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("document_type") != _KC_DOCUMENT_TYPE:
            continue
        if not chunk.get("is_active", True):
            continue
        product = _safe_str(chunk.get("product_name"))
        if not product:
            # metadata.legal_product_name_candidate 폴백
            product = _safe_str(
                (chunk.get("metadata") or {}).get("legal_product_name_candidate")
            )
        if product:
            index[product] = chunk
    return index


def _pick_target_names(candidates: List[LegalProductCandidate]) -> List[str]:
    """검색 대상 법정 품목명 목록 결정.

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


def _format_model(cert: Dict[str, Any]) -> str:
    """sample_certifications 항목을 representative_models 문자열로 변환."""
    model = _safe_str(cert.get("model_name"))
    cert_num = _safe_str(cert.get("cert_num"))
    organ = _safe_str(cert.get("cert_organ_name"))
    state = _safe_str(cert.get("cert_state"))
    parts = []
    if model:
        parts.append(model)
    detail = []
    if cert_num:
        detail.append(f"인증번호: {cert_num}")
    if organ:
        detail.append(f"기관: {organ}")
    if state:
        detail.append(f"상태: {state}")
    if detail:
        parts.append(f"({', '.join(detail)})")
    return " ".join(parts) if parts else ""


def get_kc_summary(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
    app_data: Dict[str, Any],
) -> Tuple[KcCertificationSummary, List[str]]:
    """Phase 6: KC 유사 인증사례 요약.

    - rag_chunk_all의 KC_CERTIFICATION_SUMMARY 청크만 사용 (227MB JSON 파일 불필요)
    - 데이터에 없는 모델명/기관명/인증번호는 생성하지 않음
    - 보조 참고자료 원칙 엄수 — 인증 가능 여부 확정 표현 금지
    """
    rag_chunks: List[Any] = (app_data or {}).get("rag_chunk_all") or []

    empty = KcCertificationSummary(
        similar_cert_count=0,
        top_cert_organ_names=[],
        representative_models=[],
        note=_NOTE_NO_DATA,
    )

    if not rag_chunks:
        logger.info("Phase 6: rag_chunk_all 없음 → 빈 KC 요약 반환")
        return empty, []

    # KC 청크 인덱스 구축 (product_name → chunk)
    try:
        kc_index = _build_kc_index(rag_chunks)
    except Exception as e:
        logger.warning("Phase 6 KC 인덱스 구축 실패: %s", e)
        return empty, []

    if not kc_index:
        logger.info("Phase 6: KC_CERTIFICATION_SUMMARY 청크 없음")
        return empty, []

    target_names = _pick_target_names(candidates)
    if not target_names:
        logger.info("Phase 6: 검색 대상 법정 품목명 없음")
        return empty, []

    # 첫 번째 타겟 품목으로 청크 조회 (KC 청크는 품목별 1개)
    chunk = None
    matched_name = ""
    for name in target_names:
        if name in kc_index:
            chunk = kc_index[name]
            matched_name = name
            break

    if chunk is None:
        logger.info("Phase 6: '%s' 관련 KC 청크 없음", ", ".join(target_names))
        return empty, []

    logger.info("Phase 6: KC 청크 매칭 '%s' (%s)", matched_name, chunk.get("chunk_id"))

    meta = chunk.get("metadata") or {}
    cert_count: int = meta.get("kc_cert_count") or 0
    top_organs: List[str] = meta.get("top_cert_organ_names") or []
    sample_certs: List[Dict[str, Any]] = meta.get("sample_certifications") or []

    # representative_models: 모델명 있는 항목 우선 선택
    rep_models: List[str] = []
    seen_models: set[str] = set()
    for cert in sample_certs:
        if not isinstance(cert, dict):
            continue
        model = _safe_str(cert.get("model_name"))
        if not model or model in seen_models:
            continue
        formatted = _format_model(cert)
        if formatted:
            seen_models.add(model)
            rep_models.append(formatted)
        if len(rep_models) >= _MAX_MODELS:
            break

    # source_refs
    chunk_id = _safe_str(chunk.get("chunk_id"))
    source_refs: List[str] = []
    if chunk_id:
        source_refs.append(f"kc_certification_summary:{chunk_id}")
    source_refs.append(f"kc_certification_summary:{matched_name}:{cert_count}건")

    return (
        KcCertificationSummary(
            similar_cert_count=cert_count,
            top_cert_organ_names=top_organs,
            representative_models=rep_models,
            note=_NOTE,
        ),
        source_refs,
    )
