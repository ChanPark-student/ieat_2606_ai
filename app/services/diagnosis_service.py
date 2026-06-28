import uuid
from typing import Dict, Any

from app.schemas.request import DiagnosisRequest
from app.schemas.response import DiagnosisResponse
from app.services.product_normalizer import normalize_product_input
from app.services.category_matcher import match_category
from app.services.certification_service import diagnose_certification
from app.services.institution_service import get_institution_guidance
from app.services.recall_service import summarize_recall_reasons
from app.services.kc_service import summarize_kc_certifications
from app.services.report_service import generate_markdown_report

def run_diagnosis(request: DiagnosisRequest, app_data: Dict[str, Any]) -> DiagnosisResponse:
    # Get loaded data
    master_json = app_data.get("master_json", {})
    safety_json = app_data.get("safety_json", {})
    
    # Extract specific data sets
    index_data = master_json.get("product_category_index", [])
    annex_rule = master_json.get("certification_annex_rule", [])
    process_rule = master_json.get("certification_process_rule", [])
    domestic_recall = safety_json.get("domestic_recall", [])
    kc_cert = safety_json.get("kc_certification", [])
    
    # Phase 1: Normalize
    normalized_info = normalize_product_input(request)
    
    # Phase 2: Category Match
    candidates = match_category(normalized_info, index_data)
    
    # Phase 3: Certification Diagnosis
    cert_diagnosis = diagnose_certification(candidates, annex_rule)
    
    # Phase 4: Institution Guidance
    inst_guidance = get_institution_guidance(cert_diagnosis, process_rule)
    
    # Phase 5: Recall Search
    material_kw = normalized_info["key_features"]["material"]
    recall_summary = summarize_recall_reasons(
        normalized_info["normalized_product_name"],
        material_kw,
        domestic_recall
    )
    
    # Phase 6: KC Summary
    kc_summary = summarize_kc_certifications(
        normalized_info["normalized_product_name"],
        kc_cert
    )
    
    # Build initial response without markdown
    response = DiagnosisResponse(
        case_id=f"case_{uuid.uuid4().hex[:8]}",
        status="success",
        input_summary=normalized_info,
        legal_product_candidates=candidates,
        certification_diagnosis=cert_diagnosis,
        institution_guidance=inst_guidance,
        recall_reason_summary=recall_summary,
        kc_certification_summary=kc_summary,
        launch_checklist=recall_summary.prevention_points,
        final_report_markdown="",
        used_rag_chunk_ids=[],
        source_refs=["baseline_rule_search"],
        model_name="Baseline (No LLM)",
        disclaimer="공공데이터 기반 사전 검토용 안내이며 최종 확인은 관계 기관에 필요합니다."
    )
    
    # Phase 7: Markdown Report Generation
    try:
        from app.llm.llm_service import generate_llm_report
        import logging
        logger = logging.getLogger(__name__)
        
        md_report = generate_llm_report(response)
        response.final_report_markdown = md_report
        from app.core.config import settings
        response.model_name = settings.HF_MODEL_NAME
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"LLM report generation failed: {e}. Falling back to template-only report.")
        md_report = generate_markdown_report(response)
        response.final_report_markdown = md_report
        response.model_name = "Baseline (Template-only)"
    
    return response
