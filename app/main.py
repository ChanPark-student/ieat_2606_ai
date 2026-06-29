from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import Dict, Any

from app.core.config import settings
from app.loaders.json_loader import load_json
from app.loaders.jsonl_loader import load_jsonl

from app.schemas.request import DiagnosisRequest
from app.schemas.response import DiagnosisResponse
from app.services.diagnosis_service import run_diagnosis

import logging
logger = logging.getLogger(__name__)

# In-memory storage for loaded data
app_data: Dict[str, Any] = {
    "master_json": {},
    "safety_json": {},
    "rag_chunk_all": []
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load data on startup
    logger.info("Loading baseline JSON data...")
    
    # Load master_json: {실제파일명: app_data 키} 매핑
    # 실제 업로드된 파일명이 예상과 다를 수 있으므로 명시적으로 매핑
    master_file_map = {
        "product_category_index.json": "product_category_index",
        "product_category_dictionary.json": "product_category_dictionary",
        "product_category_alias.json": "product_category_alias",
        "certification_annex_rule(DB 적재용 원본 JSON).json": "certification_annex_rule",
        "certification_process_rule.json": "certification_process_rule",
        "safety_standard_document.json": "safety_standard_document",
        "safety_standard_check_items.json": "safety_standard_check_items",
        "test_institution.json": "test_institution",
        "institution_scope.json": "institution_scope",
        "supplier_conformity_scope.json": "supplier_conformity_scope",
        "product_institution_lookup.json": "product_institution_lookup",
    }
    for filename, key_name in master_file_map.items():
        filepath = settings.MASTER_JSON_DIR / filename
        if filepath.exists():
            app_data["master_json"][key_name] = load_json(filepath)
            logger.info(f"Loaded master_json/{filename} → key '{key_name}'")
        else:
            app_data["master_json"][key_name] = []
            logger.warning(f"Not found (skipped): master_json/{filename}")
            
    # Load safety_json
    safety_files = [
        "domestic_recall.json",
        "kc_certification.json"
    ]
    for filename in safety_files:
        filepath = settings.SAFETY_JSON_DIR / filename
        name_key = filename.replace(".json", "")
        if filepath.exists():
            app_data["safety_json"][name_key] = load_json(filepath)
        else:
            app_data["safety_json"][name_key] = []
            
    # Load rag chunks (핸드오프 §5.2: 파일명이 rag_chunk_all_with_kc.jsonl인 경우 폴백)
    for rag_filename in ("rag_chunk_all.jsonl", "rag_chunk_all_with_kc.jsonl"):
        rag_file = settings.RAG_JSONL_DIR / rag_filename
        if rag_file.exists():
            app_data["rag_chunk_all"] = load_jsonl(rag_file)
            logger.info(f"Loaded rag_jsonl/{rag_filename} ({len(app_data['rag_chunk_all'])}건)")
            break
    else:
        app_data["rag_chunk_all"] = []
        logger.warning("rag_chunk_all*.jsonl 파일 없음 (skipped)")
        
    yield
    # Cleanup on shutdown
    app_data.clear()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "loaded": {
            "master_json": settings.MASTER_JSON_DIR.exists() and bool(app_data["master_json"]),
            "safety_json": settings.SAFETY_JSON_DIR.exists() and bool(app_data["safety_json"]),
            "rag_chunk_all": settings.RAG_JSONL_DIR.exists() and bool(app_data["rag_chunk_all"]),
            "llm": False # Set to false initially as per MVP requirements
        }
    }

@app.post("/diagnose", response_model=DiagnosisResponse)
def diagnose(request_data: DiagnosisRequest):
    return run_diagnosis(request_data, app_data)
