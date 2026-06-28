from typing import Dict, Any, List
from app.schemas.response import LegalProductCandidate

def match_category(normalized_info: Dict[str, Any], index_data: List[Dict[str, Any]]) -> List[LegalProductCandidate]:
    """
    Phase 2: 법정 품목명 후보 매칭
    product_category_index.json 을 사용하여 매칭.
    """
    candidates = []
    product_name = normalized_info.get("normalized_product_name", "").lower()
    
    for item in index_data:
        # Simple baseline match
        legal_name = item.get("legal_product_name", "")
        display_name = item.get("display_product_name", legal_name)
        aliases = item.get("aliases", [])
        
        is_match = False
        if product_name in legal_name.lower() or legal_name.lower() in product_name:
            is_match = True
        else:
            for alias in aliases:
                if product_name in alias.lower() or alias.lower() in product_name:
                    is_match = True
                    break
                    
        if is_match:
            candidates.append(LegalProductCandidate(
                legal_product_name=legal_name,
                display_product_name=display_name,
                certification_type="알 수 없음", # Will be filled in phase 3
                confidence_level="CANDIDATE",
                confidence_score=0.8,
                needs_user_confirmation=True,
                match_basis="제품명 키워드 매칭 (Baseline)"
            ))
            
    # If no match, provide a fallback
    if not candidates:
         candidates.append(LegalProductCandidate(
            legal_product_name="알 수 없음",
            display_product_name="매칭된 품목 없음",
            certification_type="알 수 없음",
            confidence_level="NO_MATCH",
            confidence_score=0.0,
            needs_user_confirmation=True,
            match_basis="매칭 실패"
        ))
        
    return candidates
