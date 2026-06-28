from typing import Dict, Any, List
from app.schemas.response import KcCertificationSummary

def summarize_kc_certifications(
    product_name: str,
    kc_data: List[Dict[str, Any]]
) -> KcCertificationSummary:
    """
    Phase 6: KC 유사 인증사례 검색
    """
    if not product_name:
        return KcCertificationSummary(
            similar_cert_count=0,
            top_cert_organ_names=[],
            representative_models=[],
            note="KC 인증정보는 유사 인증사례 확인용 보조 근거입니다."
        )
        
    matched = []
    for case in kc_data:
        pname = case.get("productName", "")
        cname = case.get("categoryName", "")
        if product_name.lower() in pname.lower() or product_name.lower() in cname.lower():
            matched.append(case)
            
    organs = {}
    for case in matched:
        organ = case.get("certOrganName")
        if organ:
            organs[organ] = organs.get(organ, 0) + 1
            
    top_organs = sorted(organs.keys(), key=lambda k: organs[k], reverse=True)[:3]
    rep_models = [c.get("modelName", "") for c in matched[:3] if c.get("modelName")]
    
    return KcCertificationSummary(
        similar_cert_count=len(matched),
        top_cert_organ_names=top_organs,
        representative_models=rep_models,
        note="KC 인증정보는 유사 인증사례 확인용 보조 근거이며, 실제 인증 가능 여부나 접수 가능 기관은 현재 지정기관 업무범위와 관계 기관 확인이 필요합니다."
    )
