import uuid
from typing import Dict, Any
import logging

from app.schemas.request import DiagnosisRequest
from app.schemas.response import (
    DiagnosisResponse,
    CertificationDiagnosis,
    InstitutionGuidance,
    RecallReasonSummary,
    KcCertificationSummary,
)
from app.services.report_service import (
    generate_markdown_report,
    FABRIC_MATERIAL_TOKENS,
    PLUSH_TOY_TOKENS,
)
from app.services.category_matcher import match_category
from app.services.certification_service import diagnose_certification
from app.services.institution_service import get_institution_guidance
from app.services.recall_service import get_recall_summary
from app.services.kc_certification_service import get_kc_summary
from app.search.rag_retriever import collect_refs
from app.search.text_utils import tokenize, meaningful_tokens

logger = logging.getLogger(__name__)


def _normalize_material_tags(material_text: str, product_name: str, user_query: str) -> list:
    """소재·제품 원문에서 정규화된 검색 태그 추출 (RAG 질의 보강용).

    예: "극세사 원단, 솜, 실" → ["섬유", "원단", "봉제", "충전재", "봉제완구"]
    report_service._prioritize_checklist와 동일한 키워드 그룹을 사용해
    체크리스트 우선순위 판단과 RAG 검색 신호를 일치시킨다.
    """
    text = f"{material_text} {product_name} {user_query}".lower()
    material = (material_text or "").lower()

    tags: list = []
    has_fabric = any(t in material for t in FABRIC_MATERIAL_TOKENS)
    has_plush = any(t in text for t in PLUSH_TOY_TOKENS)

    if has_fabric:
        tags += ["섬유", "원단", "봉제", "충전재"]
    if has_fabric and has_plush:
        tags.append("봉제완구")

    return list(dict.fromkeys(tags))

def run_diagnosis(request: DiagnosisRequest, app_data: Dict[str, Any]) -> DiagnosisResponse:
    # 7. 절대 하드코딩 판단을 하지 말고, 현재는 입력값 요약과 빈 후보/빈 요약을 반환하는 baseline
    
    # 입력값 요약 (request의 필드들을 dict로 변환)
    input_summary = request.model_dump()

    # Phase 2: 법정 품목명 후보 매칭 (data/master_json/product_category_index.json 기반)
    # 파일이 없거나 비어 있으면 빈 리스트로 안전 반환
    index_data = (app_data or {}).get("master_json", {}).get("product_category_index", [])
    try:
        legal_product_candidates = match_category(request, index_data)
    except Exception as e:
        logger.warning(f"category matching failed, returning empty candidates: {e}")
        legal_product_candidates = []

    # Phase 3: 인증유형 및 안전기준 조회
    try:
        cert_diagnosis, launch_checklist, cert_source_refs = diagnose_certification(
            legal_product_candidates, app_data
        )
    except Exception as e:
        logger.warning(f"certification diagnosis failed: {e}")
        cert_diagnosis = CertificationDiagnosis(
            certification_type="확인 전",
            applied_standards=[],
            judgement_level="미정",
            source_refs=[],
        )
        launch_checklist = []
        cert_source_refs = []

    # Phase 4: 기관 및 절차 안내
    try:
        inst_guidance, inst_source_refs = get_institution_guidance(
            legal_product_candidates, cert_diagnosis, app_data
        )
    except Exception as e:
        logger.warning(f"institution guidance failed: {e}")
        inst_guidance = InstitutionGuidance(
            institution_required=False,
            summary="기준 데이터에서 기관 정보를 확인하지 못했습니다. 관계 기관 확인이 필요합니다.",
            candidate_institutions=[],
        )
        inst_source_refs = []
    
    # Phase 5: 국내 리콜 사유 검색
    _recall_query_text = " ".join(filter(None, [
        request.product_name or "",
        request.user_query or "",
        request.material_text or "",
        request.power_type or "",
        "배터리" if request.battery_included else "",
    ]))
    try:
        recall_summary, recall_source_refs = get_recall_summary(
            legal_product_candidates, cert_diagnosis, app_data,
            query_text=_recall_query_text or None,
        )
    except Exception as e:
        logger.warning(f"recall summary failed: {e}")
        recall_summary = RecallReasonSummary(
            recall_count=0,
            top_recall_reasons=[],
            representative_cases=[],
            prevention_points=[],
        )
        recall_source_refs = []

    # Phase 6: KC 유사 인증사례 요약
    _kc_query_text = " ".join(filter(None, [
        request.product_name or "",
        request.user_query or "",
        request.material_text or "",
    ]))
    try:
        kc_summary, kc_source_refs = get_kc_summary(
            legal_product_candidates, cert_diagnosis, app_data,
            query_text=_kc_query_text,
        )
    except Exception as e:
        logger.warning(f"kc summary failed: {e}")
        kc_summary = KcCertificationSummary(
            similar_cert_count=0,
            top_cert_organ_names=[],
            representative_models=[],
            note="유사 KC 인증사례 정보를 확인하지 못했습니다. 관계 기관 확인이 필요합니다.",
        )
        kc_source_refs = []

    # Phase 6.5: RAG Retriever — 근거 chunk 검색 (판단을 바꾸지 않음, 근거만 수집)
    # 품목 미확정(CONFIRMED/CANDIDATE 없음 or 인증유형 '확인 전')이면 품목 특정 근거 미수집
    _top_conf_cand = next(
        (c for c in legal_product_candidates
         if c.confidence_level in ("CONFIRMED", "CANDIDATE")),
        None,
    )
    _top_legal_name = _top_conf_cand.legal_product_name if _top_conf_cand else ""
    _cert_type = (cert_diagnosis.certification_type or "").strip()
    _allow_category_specific = (
        _top_conf_cand is not None
        and _cert_type not in ("", "확인 전", "미정")
    )

    retrieved_chunks = []
    rag_used_ids: list = []
    rag_refs: list = []
    rag = (app_data or {}).get("rag_retriever")
    if rag is not None and getattr(rag, "available", False):
        try:
            _material_tags = _normalize_material_tags(
                request.material_text or "", request.product_name or "", request.user_query or ""
            )
            _query_parts = [
                request.product_name or "",
                request.user_query or "",
                request.target_age or "",
                request.material_text or "",
                request.power_type or "",
                "배터리 포함" if request.battery_included else "",
                request.import_or_manufacture or "",
                " ".join(_material_tags),
            ]
            # 품목이 확정된 경우에만 rule 결과를 query에 추가 (불확실 입력 과확정 방지)
            if _allow_category_specific:
                _query_parts.append(_top_legal_name)
                _query_parts.append(_top_conf_cand.display_product_name or "")
                _query_parts.append(_cert_type)
                _query_parts.extend(cert_diagnosis.applied_standards or [])
                _query_parts.extend(recall_summary.top_recall_reasons or [])
                if kc_summary.matched_category:
                    _query_parts.append(kc_summary.matched_category)
            _query_text = " ".join(p for p in _query_parts if p)

            _product_tokens = meaningful_tokens(tokenize(request.product_name or ""))
            _mp_tokens = meaningful_tokens(
                tokenize(" ".join(filter(None, [
                    request.material_text or "",
                    request.power_type or "",
                    "배터리" if request.battery_included else "",
                    " ".join(_material_tags),
                ])))
            )

            retrieved_chunks = rag.retrieve(
                query_text=_query_text,
                top_legal_name=_top_legal_name,
                cert_type=_cert_type,
                product_tokens=_product_tokens,
                material_power_tokens=_mp_tokens,
                allow_category_specific=_allow_category_specific,
            )
            rag_used_ids, rag_refs = collect_refs(retrieved_chunks)
            logger.info("Phase 6.5 RAG: %d chunk 검색 (allow_specific=%s)",
                        len(retrieved_chunks), _allow_category_specific)
        except Exception as e:
            logger.warning("RAG retriever 검색 실패 (무시): %s", e)

    # Build initial response without markdown
    response = DiagnosisResponse(
        case_id=f"case_{uuid.uuid4().hex[:8]}",
        status="success",
        input_summary=input_summary,
        legal_product_candidates=legal_product_candidates,
        certification_diagnosis=cert_diagnosis,
        institution_guidance=inst_guidance,
        recall_reason_summary=recall_summary,
        kc_certification_summary=kc_summary,
        launch_checklist=launch_checklist,
        final_report_markdown="",
        used_rag_chunk_ids=rag_used_ids,
        source_refs=list(dict.fromkeys(
            cert_source_refs + inst_source_refs + recall_source_refs
            + kc_source_refs + rag_refs
        )),
        model_name="Baseline (Template-only)",
        disclaimer="본 결과는 입력된 데이터를 바탕으로 한 예비 진단 결과이며, 최종 법적 판단 기준이 될 수 없습니다."
    )
    
    # Phase 7/8: Markdown 보고서 생성
    # ENABLE_LLM=true 이면 LLM으로 문장 정제 시도, 실패하면 템플릿 fallback
    from app.core.config import settings as _settings
    from app.llm.llm_service import generate_llm_report

    if _settings.ENABLE_LLM:
        try:
            md_report = generate_llm_report(response, retrieved_chunks=retrieved_chunks)
            response.final_report_markdown = md_report
            response.report_generation_mode = "llm"
            response.model_name = _settings.HF_MODEL_NAME
            logger.info("LLM 보고서 생성 성공 (model=%s)", _settings.HF_MODEL_NAME)
        except Exception as e:
            logger.warning(
                "LLM 보고서 생성 실패 → 템플릿 fallback: %s", e
            )
            response.final_report_markdown = generate_markdown_report(response)
            response.report_generation_mode = "template"
            response.model_name = "Baseline (Template-only)"
    else:
        response.final_report_markdown = generate_markdown_report(response)
        response.report_generation_mode = "template"
        response.model_name = "Baseline (Template-only)"

    return response
