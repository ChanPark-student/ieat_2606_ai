from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class LegalProductCandidate(BaseModel):
    legal_product_name: str
    display_product_name: str
    certification_type: str
    confidence_level: str
    confidence_score: float
    needs_user_confirmation: bool
    match_basis: str

class CertificationDiagnosis(BaseModel):
    certification_type: str
    applied_standards: List[str]
    judgement_level: str
    source_refs: List[str]

class InstitutionInfo(BaseModel):
    institution_name: str
    short_name: str = ""
    institution_role: str = ""
    certification_type: str = ""
    website_url: str = ""
    product_scope: List[str] = []
    source_refs: List[str] = []

class InstitutionGuidance(BaseModel):
    institution_required: bool
    summary: str
    candidate_institutions: List[InstitutionInfo]

class RecallReasonSummary(BaseModel):
    recall_count: int
    top_recall_reasons: List[str]
    representative_cases: List[str]
    prevention_points: List[str]
    supplemental_cases: List[str] = []  # BM25 보조 검색 결과 (exact match 없을 때)

class KcCertificationSummary(BaseModel):
    similar_cert_count: int
    top_cert_organ_names: List[str]
    representative_models: List[str]
    note: str
    matched_category: str = ""  # KC 인덱스에서 매칭된 카테고리명 (예: "완구")

class DiagnosisResponse(BaseModel):
    case_id: str
    status: str
    input_summary: Dict[str, Any]
    legal_product_candidates: List[LegalProductCandidate]
    certification_diagnosis: CertificationDiagnosis
    institution_guidance: InstitutionGuidance
    recall_reason_summary: RecallReasonSummary
    kc_certification_summary: KcCertificationSummary
    launch_checklist: List[str]
    final_report_markdown: str
    # "template": 템플릿 기반, "llm": LLM 정제 사용
    report_generation_mode: str = "template"
    used_rag_chunk_ids: List[str]
    source_refs: List[str]
    model_name: str
    disclaimer: str
