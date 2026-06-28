from typing import Dict, Any, List
from app.schemas.response import LegalProductCandidate, CertificationDiagnosis

def diagnose_certification(candidates: List[LegalProductCandidate], annex_rule: List[Dict[str, Any]]) -> CertificationDiagnosis:
    """
    Phase 3: 인증유형 및 안전기준 조회
    가장 가능성 높은 후보 하나를 기준으로 진단.
    """
    if not candidates or candidates[0].confidence_level == "NO_MATCH":
        return CertificationDiagnosis(
            certification_type="알 수 없음",
            applied_standards=[],
            judgement_level="NO_MATCH",
            source_refs=[]
        )
        
    top_candidate = candidates[0]
    legal_name = top_candidate.legal_product_name
    
    cert_type = "알 수 없음"
    applied_standards = []
    
    # lookup in annex_rule
    for rule in annex_rule:
        if rule.get("legal_product_name") == legal_name:
            cert_type = rule.get("certification_type", "알 수 없음")
            applied_standards = rule.get("applied_standards", [])
            break
            
    # Update candidate with found cert type
    top_candidate.certification_type = cert_type
            
    return CertificationDiagnosis(
        certification_type=cert_type,
        applied_standards=applied_standards,
        judgement_level="CANDIDATE",
        source_refs=["certification_annex_rule.json"]
    )
