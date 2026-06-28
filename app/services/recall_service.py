from typing import Dict, Any, List
from app.schemas.response import RecallReasonSummary

def summarize_recall_reasons(
    product_name: str, 
    material_keywords: List[str],
    recalls: List[Dict[str, Any]]
) -> RecallReasonSummary:
    """
    Phase 5: 국내리콜 사유 검색 및 출시 전 체크리스트 변환
    """
    keywords = [product_name] + material_keywords
    keywords = [k for k in keywords if k]
    
    # Baseline: keyword mapping to checklists
    checklist_mapping = {
        "프탈레이트": "원단, 코팅, 프린팅, 플라스틱 부자재에 대해 프탈레이트계 가소제 시험성적서를 확보한다.",
        "납": "금속 부자재, 지퍼, 페인트 코팅 등에 대해 납(Pb) 함유량 시험성적서를 확보한다.",
        "카드뮴": "표면 코팅 및 플라스틱 부품에 대해 카드뮴(Cd) 함유량 시험성적서를 확보한다.",
        "폼알데하이드": "원단 및 가죽 소재에 대해 폼알데하이드 시험성적서를 확보한다.",
        "pH": "피부에 닿는 섬유 원단에 대해 pH 농도 적합성 시험을 진행한다.",
        "작은 부품": "어린이가 삼킬 수 있는 작은 부품이 쉽게 떨어지지 않는지 인장 강도 시험을 확인한다.",
        "끈": "어린이용 의류의 끈(조임끈 등) 길이나 위치가 안전기준(얽힘 위험)을 만족하는지 확인한다."
    }
    
    # Keyword search over domestic recalls
    matched_cases = []
    for case in recalls:
        text_content = (
            str(case.get("product_name", "")) + " " +
            str(case.get("recall_reason", "")) + " " +
            str(case.get("hazard_content", ""))
        ).lower()
        
        is_match = False
        for kw in keywords:
            if kw.lower() in text_content:
                is_match = True
                break
        
        if is_match:
            matched_cases.append(case)
            
    # Extract reasons
    extracted_reasons = []
    for case in matched_cases:
        reason = case.get("recall_reason")
        if reason:
            extracted_reasons.append(reason)
            
    # Generate checklist based on mapping and found reasons
    prevention_points = set()
    combined_reasons_text = " ".join(extracted_reasons).lower()
    
    for kw, checklist_item in checklist_mapping.items():
        if kw in combined_reasons_text:
            prevention_points.add(checklist_item)
            
    # Also check if material keywords directly trigger checklist even without recall case
    # This acts as a safety net baseline
    for mat in material_keywords:
        for kw, checklist_item in checklist_mapping.items():
            if kw in mat.lower():
                prevention_points.add(checklist_item)
                
    if not prevention_points:
        prevention_points.add("법정 품목명 최종 확인 및 적용 안전기준과 표시사항을 확인한다.")
        
    # Default representative cases
    rep_cases = [c.get("product_name", "알 수 없는 제품") for c in matched_cases[:3]]
    
    return RecallReasonSummary(
        recall_count=len(matched_cases),
        top_recall_reasons=list(set(extracted_reasons))[:5],
        representative_cases=rep_cases,
        prevention_points=list(prevention_points)
    )
