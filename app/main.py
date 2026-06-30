from fastapi import FastAPI
from contextlib import asynccontextmanager
from typing import Dict, Any, List

from app.core.config import settings
from app.loaders.json_loader import load_json
from app.loaders.jsonl_loader import load_jsonl

from app.schemas.request import DiagnosisRequest
from app.schemas.response import DiagnosisResponse
from app.services.diagnosis_service import run_diagnosis
from app.search.recall_bm25 import RecallBM25Index
from app.search.rag_retriever import RagRetriever

import logging
logger = logging.getLogger(__name__)

# In-memory storage for loaded data
app_data: Dict[str, Any] = {
    "master_json": {},
    "safety_json": {},
    "rag_chunk_all": [],
    "kc_agg": {},           # KC 인증 집계 인덱스: categoryName[2] → {total, valid, top_organs, samples}
    "recall_bm25_idx": None,  # RecallBM25Index — 리콜 BM25 검색용
    "rag_retriever": None,    # RagRetriever — 근거 chunk 검색용
}


def _build_kc_agg(raw: List[Dict]) -> Dict[str, Any]:
    """KC 인증 원본 목록을 categoryName[2] 기준으로 집계해 compact index 반환.

    226MB 원본은 집계 후 호출부에서 del 처리. 결과 인덱스는 ~수십KB.
    certState=='적합'인 사례를 up-to 10개씩 샘플로 보관.
    """
    index: Dict[str, Any] = {}
    for r in raw:
        cat = r.get("categoryName") or ""
        parts = [p.strip() for p in cat.split(" > ")]
        if len(parts) < 3:
            continue
        key = parts[2]
        if key not in index:
            index[key] = {"total": 0, "valid": 0, "organs": {}, "samples": []}
        entry = index[key]
        entry["total"] += 1
        state = r.get("certState") or ""
        if state == "적합":
            entry["valid"] += 1
            if len(entry["samples"]) < 50:  # 50개 보관 → query 시 keyword 정렬 풀 확보
                entry["samples"].append({
                    "certNum": r.get("certNum"),
                    "certOrganName": r.get("certOrganName"),
                    "certState": state,
                    "certDate": r.get("certDate"),
                    "modelName": r.get("modelName"),
                    "productName": r.get("productName"),
                    "importDiv": r.get("importDiv"),
                })
        organ = r.get("certOrganName") or ""
        if "(" in organ:
            short = organ[organ.rfind("(")+1:organ.rfind(")")]
        else:
            short = organ
        if short:
            entry["organs"][short] = entry["organs"].get(short, 0) + 1

    for entry in index.values():
        entry["top_organs"] = [
            k for k, v in sorted(entry["organs"].items(), key=lambda x: -x[1])[:5]
        ]
        del entry["organs"]

    return index

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
            
    # Load safety_json (domestic_recall 전체 보관, kc_certification은 집계 후 raw 폐기)
    domestic_path = settings.SAFETY_JSON_DIR / "domestic_recall.json"
    if domestic_path.exists():
        app_data["safety_json"]["domestic_recall"] = load_json(domestic_path)
        logger.info(f"Loaded domestic_recall.json ({len(app_data['safety_json']['domestic_recall'])}건)")
    else:
        app_data["safety_json"]["domestic_recall"] = []
        logger.warning("domestic_recall.json 없음 (skipped)")

    # BM25 인덱스 구축 (domestic_recall 로드 직후)
    try:
        recall_records = app_data["safety_json"].get("domestic_recall") or []
        if recall_records:
            app_data["recall_bm25_idx"] = RecallBM25Index(recall_records)
        else:
            logger.warning("domestic_recall 비어있어 BM25 인덱스 미구축")
    except Exception as e:
        logger.warning("BM25 인덱스 구축 실패 (skipped): %s", e)

    # KC 인증: 226MB raw 목록을 집계 후 즉시 폐기 → compact index만 유지
    kc_path = settings.SAFETY_JSON_DIR / "kc_certification.json"
    if kc_path.exists():
        logger.info("kc_certification.json 로드 + 집계 시작 (약 3~5초 소요)...")
        try:
            kc_raw: List[Dict] = load_json(kc_path)
            app_data["kc_agg"] = _build_kc_agg(kc_raw)
            del kc_raw  # 226MB raw 즉시 해제
            logger.info(
                f"kc_certification.json 집계 완료: {len(app_data['kc_agg'])}개 카테고리 인덱스 구축"
            )
        except Exception as e:
            app_data["kc_agg"] = {}
            logger.warning(f"kc_certification.json 집계 실패 (skipped): {e}")
    else:
        app_data["kc_agg"] = {}
        logger.warning("kc_certification.json 없음 (skipped)")
            
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

    # RAG Retriever 인덱스 구축 (rag_chunk_all 로드 직후)
    try:
        rag_chunks = app_data.get("rag_chunk_all") or []
        if rag_chunks:
            app_data["rag_retriever"] = RagRetriever(rag_chunks)
        else:
            logger.warning("rag_chunk_all 비어있어 RAG retriever 미구축")
    except Exception as e:
        logger.warning("RAG retriever 구축 실패 (skipped): %s", e)

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
    recall_idx = app_data.get("recall_bm25_idx")
    rag = app_data.get("rag_retriever")
    return {
        "status": "ok",
        "loaded": {
            "master_json": settings.MASTER_JSON_DIR.exists() and bool(app_data["master_json"]),
            "safety_json": settings.SAFETY_JSON_DIR.exists() and bool(app_data["safety_json"]),
            "rag_chunk_all": settings.RAG_JSONL_DIR.exists() and bool(app_data["rag_chunk_all"]),
            # 리콜 BM25 / RAG retriever / KC 인덱스 로드 상태 (팀장 확인용)
            "recall_bm25": bool(getattr(recall_idx, "available", False)),
            "rag_retriever": bool(getattr(rag, "available", False)),
            "rag_chunk_count": getattr(rag, "chunk_count", 0),
            "kc_index": bool(app_data.get("kc_agg")),
            "llm": settings.ENABLE_LLM,  # 기본 false (MVP), 모델 다운로드는 첫 /diagnose 시 lazy
        }
    }

@app.post("/diagnose", response_model=DiagnosisResponse)
def diagnose(request_data: DiagnosisRequest):
    return run_diagnosis(request_data, app_data)
