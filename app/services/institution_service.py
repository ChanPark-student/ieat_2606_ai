from typing import Dict, Any, List
from app.schemas.response import CertificationDiagnosis, InstitutionGuidance

def get_institution_guidance(diagnosis: CertificationDiagnosis, process_rule: List[Dict[str, Any]]) -> InstitutionGuidance:
    """
    Phase 4: 기관 및 절차 안내
    """
    cert_type = diagnosis.certification_type
    
    if cert_type == "알 수 없음" or not cert_type:
        return InstitutionGuidance(
            institution_required=False,
            summary="인증 유형을 확인할 수 없어 안내가 불가능합니다.",
            candidate_institutions=[]
        )
        
    summary = "해당 인증 유형의 절차 안내가 없습니다."
    required = False
    
    for rule in process_rule:
        if rule.get("certification_type") == cert_type:
            summary = rule.get("summary", summary)
            required = rule.get("institution_required", False)
            break
            
    # Hardcoded baseline if process_rule is empty or missing matching type
    if summary == "해당 인증 유형의 절차 안내가 없습니다.":
        if cert_type == "공급자적합성확인":
            summary = "공급자적합성확인은 지정기관 신고 대상이 아니며, 사업자가 안전기준 적합성을 입증할 수 있는 자료를 확보해야 합니다."
            required = False
        elif cert_type == "안전확인":
            summary = "지정된 시험검사기관에서 안전성에 대한 시험·검사를 받아야 합니다."
            required = True
        elif cert_type == "안전인증":
            summary = "안전인증기관으로부터 공장심사 및 제품검사를 받아야 합니다."
            required = True
            
    return InstitutionGuidance(
        institution_required=required,
        summary=summary,
        candidate_institutions=[]
    )
