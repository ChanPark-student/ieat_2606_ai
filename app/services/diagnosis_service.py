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
from app.services.report_service import generate_markdown_report
from app.services.category_matcher import match_category
from app.services.certification_service import diagnose_certification
from app.services.institution_service import get_institution_guidance
from app.services.recall_service import get_recall_summary
from app.services.kc_certification_service import get_kc_summary

logger = logging.getLogger(__name__)

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
        used_rag_chunk_ids=[],
        source_refs=list(dict.fromkeys(cert_source_refs + inst_source_refs + recall_source_refs + kc_source_refs)),
        model_name="Baseline (Template-only)",
        disclaimer="본 결과는 입력된 데이터를 바탕으로 한 예비 진단 결과이며, 최종 법적 판단 기준이 될 수 없습니다."
    )
    
    # Phase 7/8: Markdown 보고서 생성
    # ENABLE_LLM=true 이면 LLM으로 문장 정제 시도, 실패하면 템플릿 fallback
    from app.core.config import settings as _settings
    from app.llm.llm_service import generate_llm_report

    if _settings.ENABLE_LLM:
        try:
            md_report = generate_llm_report(response)
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
