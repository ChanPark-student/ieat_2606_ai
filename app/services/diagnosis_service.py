import uuid
from typing import Dict, Any
import logging

from app.schemas.request import DiagnosisRequest
from app.schemas.response import (
    DiagnosisResponse,
    CertificationDiagnosis,
    InstitutionGuidance,
    RecallReasonSummary,
    KcCertificationSummary
)
from app.services.report_service import generate_markdown_report

logger = logging.getLogger(__name__)

def run_diagnosis(request: DiagnosisRequest, app_data: Dict[str, Any]) -> DiagnosisResponse:
    # 7. 절대 하드코딩 판단을 하지 말고, 현재는 입력값 요약과 빈 후보/빈 요약을 반환하는 baseline
    
    # 입력값 요약 (request의 필드들을 dict로 변환)
    input_summary = request.model_dump()
    
    # 빈 구조체 생성
    empty_cert_diagnosis = CertificationDiagnosis(
        certification_type="확인 전",
        applied_standards=[],
        judgement_level="미정",
        source_refs=[]
    )
    
    empty_inst_guidance = InstitutionGuidance(
        institution_required=False,
        summary="안내할 기관 정보가 없습니다.",
        candidate_institutions=[]
    )
    
    empty_recall_summary = RecallReasonSummary(
        recall_count=0,
        top_recall_reasons=[],
        representative_cases=[],
        prevention_points=[]
    )
    
    empty_kc_summary = KcCertificationSummary(
        similar_cert_count=0,
        top_cert_organ_names=[],
        representative_models=[],
        note="유사 KC 인증 정보가 없습니다."
    )
    
    # Build initial response without markdown
    response = DiagnosisResponse(
        case_id=f"case_{uuid.uuid4().hex[:8]}",
        status="success",
        input_summary=input_summary,
        legal_product_candidates=[],
        certification_diagnosis=empty_cert_diagnosis,
        institution_guidance=empty_inst_guidance,
        recall_reason_summary=empty_recall_summary,
        kc_certification_summary=empty_kc_summary,
        launch_checklist=[],
        final_report_markdown="",
        used_rag_chunk_ids=[],
        source_refs=[],
        model_name="Baseline (Template-only)",
        disclaimer="본 결과는 입력된 데이터를 바탕으로 한 예비 진단 결과이며, 최종 법적 판단 기준이 될 수 없습니다."
    )
    
    # Phase 7: Markdown Report Generation with LLM fallback
    try:
        from app.llm.llm_service import generate_llm_report
        
        md_report = generate_llm_report(response)
        response.final_report_markdown = md_report
        from app.core.config import settings
        response.model_name = settings.HF_MODEL_NAME
    except Exception as e:
        logger.warning(f"LLM report generation failed or not configured: {e}. Falling back to template-only report.")
        md_report = generate_markdown_report(response)
        response.final_report_markdown = md_report
        response.model_name = "Baseline (Template-only)"
    
    return response
