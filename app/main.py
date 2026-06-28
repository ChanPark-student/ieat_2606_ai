from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import Dict, Any

from app.core.config import settings
from app.loaders.json_loader import load_json
from app.loaders.jsonl_loader import load_jsonl

# In-memory storage for loaded data
app_data: Dict[str, Any] = {
    "master_json": {},
    "safety_json": {},
    "rag_chunk_all": []
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load data on startup
    # We will expand this to actually load specific files later based on MVP reqs.
    # For now we just verify if directories exist.
    
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
            "master_json": settings.MASTER_JSON_DIR.exists(),
            "safety_json": settings.SAFETY_JSON_DIR.exists(),
            "rag_chunk_all": settings.RAG_JSONL_DIR.exists(),
            "llm": False # Set to false initially as per MVP requirements
        }
    }

@app.post("/diagnose")
def diagnose(request_data: dict):
    # Stub for the /diagnose endpoint
    return {
        "case_id": "case_placeholder",
        "status": "success",
        "input_summary": request_data,
        "legal_product_candidates": [],
        "certification_diagnosis": {},
        "institution_guidance": {},
        "recall_reason_summary": {},
        "kc_certification_summary": {},
        "launch_checklist": [],
        "final_report_markdown": "",
        "used_rag_chunk_ids": [],
        "source_refs": [],
        "model_name": "None",
        "disclaimer": "공공데이터 기반 사전 검토용 안내이며 최종 확인은 관계 기관에 필요합니다."
    }
