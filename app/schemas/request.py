from pydantic import BaseModel, Field
from typing import Optional

class DiagnosisRequest(BaseModel):
    product_name: str
    user_query: Optional[str] = ""
    target_age: Optional[str] = ""
    material_text: Optional[str] = ""
    power_type: Optional[str] = ""
    battery_included: Optional[bool] = False
    import_or_manufacture: Optional[str] = ""
