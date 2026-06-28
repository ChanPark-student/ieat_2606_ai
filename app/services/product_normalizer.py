from typing import Dict, Any
from app.schemas.request import DiagnosisRequest

def normalize_product_input(request: DiagnosisRequest) -> Dict[str, Any]:
    """
    Phase 1: 제품 정보 구조화
    사용자의 자연어 입력을 내부 처리용 포맷으로 변환.
    현재는 LLM 없이 request 필드를 바로 딕셔너리로 매핑.
    """
    # Extract materials as simple list from comma-separated string
    materials = [m.strip() for m in request.material_text.split(",")] if request.material_text else []
    
    return {
        "normalized_product_name": request.product_name.strip(),
        "key_features": {
            "target_age": request.target_age,
            "material": materials,
            "power_type": request.power_type,
            "battery_included": request.battery_included,
            "import_or_manufacture": request.import_or_manufacture
        },
        "missing_fields": [],
        "confidence": 1.0 # Baseline assume 1.0
    }
