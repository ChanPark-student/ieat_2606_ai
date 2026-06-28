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
    
    # Load master_json
    master_files = [
        "product_category_index.json",
        "certification_annex_rule.json",
        "certification_process_rule.json"
    ]
    for filename in master_files:
        filepath = settings.MASTER_JSON_DIR / filename
        name_key = filename.replace(".json", "")
        if filepath.exists():
            app_data["master_json"][name_key] = load_json(filepath)
        else:
            app_data["master_json"][name_key] = []
            
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
            
    # Load rag chunks
    rag_file = settings.RAG_JSONL_DIR / "rag_chunk_all.jsonl"
    if rag_file.exists():
        app_data["rag_chunk_all"] = load_jsonl(rag_file)
    else:
        app_data["rag_chunk_all"] = []
        
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
