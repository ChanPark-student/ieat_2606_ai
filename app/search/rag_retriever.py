"""경량 RAG Retriever — rag_chunk_all 기반 근거 chunk 검색기.

절대 원칙:
- Retriever는 판단 엔진이 아니라 '근거 chunk 검색기'다.
- 법정 품목명/인증유형/KC 매칭/리콜 count 등 Rule 판단 결과를 바꾸지 않는다.
- LLM 또는 보고서에 넣을 근거 chunk를 찾는 용도로만 사용한다.
- rank_bm25 미설치 / 파일 없음 / corpus 비어있음 → graceful degradation (빈 결과).
- 불확실 입력(품목 미확정)에서는 품목 특정 근거를 과확정처럼 반환하지 않는다.
"""
from __future__ import annotations

import ast
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.search.text_utils import tokenize, meaningful_tokens

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 10
_MAX_CHUNK_TEXT = 1800
_MAX_USED_CHUNK_IDS = 12
_MAX_SOURCE_REFS = 20

# document_type별 최대 노출 수 (한 유형 쏠림 방지)
DOC_TYPE_QUOTA: Dict[str, int] = {
    "CERTIFICATION_RULE": 2,
    "SAFETY_STANDARD_DOCUMENT": 2,
    "SAFETY_STANDARD_CHECK_ITEM": 3,
    "TEST_INSTITUTION_SCOPE": 2,
    "CERTIFICATION_PROCESS_RULE": 1,
    "SUPPLIER_CONFORMITY_SCOPE": 1,
    "DOMESTIC_RECALL": 2,
    "KC_CERTIFICATION_SUMMARY": 2,
    "PRODUCT_CATEGORY_MAPPING": 1,
}
_DEFAULT_QUOTA = 1

# tie-break / 약한 가산점용 우선순위
DOC_TYPE_PRIORITY: List[str] = [
    "CERTIFICATION_RULE",
    "SAFETY_STANDARD_DOCUMENT",
    "SAFETY_STANDARD_CHECK_ITEM",
    "TEST_INSTITUTION_SCOPE",
    "CERTIFICATION_PROCESS_RULE",
    "SUPPLIER_CONFORMITY_SCOPE",
    "DOMESTIC_RECALL",
    "KC_CERTIFICATION_SUMMARY",
    "PRODUCT_CATEGORY_MAPPING",
]
_PRIORITY_RANK = {dt: i for i, dt in enumerate(DOC_TYPE_PRIORITY)}

# 품목 미확정 시 제외할 document_type (과확정 근거 방지)
_CATEGORY_SPECIFIC_TYPES = {"DOMESTIC_RECALL", "KC_CERTIFICATION_SUMMARY"}


def _as_list(val: Any) -> List[str]:
    """keywords 등 list/str 혼용 필드를 list[str]로 정규화."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val if v is not None]
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed if v is not None]
            except Exception:
                pass
        return [s] if s else []
    return [str(val)]


def _as_dict(val: Any) -> Dict[str, Any]:
    """metadata 등 dict/str 혼용 필드를 dict로 정규화."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("{"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


def _normalize_chunk(raw: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    """rag_chunk row를 안전하게 정규화. is_active=false면 None 반환(제외)."""
    if not isinstance(raw, dict):
        return None
    # is_active 필드가 있으면 존중 (없으면 활성으로 간주)
    if raw.get("is_active") is False:
        return None

    metadata = _as_dict(raw.get("metadata"))
    doc_type = str(raw.get("document_type") or "").strip()
    source_table = str(raw.get("source_table") or "").strip()
    source_pk = str(raw.get("source_pk") or "").strip()

    # chunk_id 안정적 fallback (276/385는 chunk_id 없음 → source_pk 기반 생성)
    chunk_id = raw.get("chunk_id") or raw.get("rag_chunk_id")
    if not chunk_id:
        if source_table and source_pk:
            chunk_id = f"{source_table}:{source_pk}"
        else:
            chunk_id = f"RAGIDX-{idx}"
    chunk_id = str(chunk_id)

    # legal_product_name: product_category 우선, 없으면 metadata 후보
    product_category = raw.get("product_category") or ""
    legal_name = str(
        product_category
        or metadata.get("legal_product_name_candidate")
        or ""
    ).strip()

    keywords = _as_list(raw.get("keywords"))
    chunk_text = str(raw.get("chunk_text") or "").strip()
    title = str(raw.get("title") or "").strip()

    # chunk_text가 비거나 너무 짧으면 title/keywords 중심으로라도 검색 가능해야 함
    if not chunk_text and not title and not keywords:
        return None

    return {
        "chunk_id": chunk_id,
        "document_type": doc_type,
        "title": title,
        "chunk_text": chunk_text[:_MAX_CHUNK_TEXT],
        "product_category": str(product_category).strip(),
        "legal_product_name": legal_name,
        "certification_type": str(raw.get("certification_type") or "").strip(),
        "keywords": keywords,
        "source_table": source_table,
        "source_pk": source_pk,
        "source_file": str(metadata.get("source_file") or "").strip(),
    }


def _build_doc_text(c: Dict[str, Any]) -> str:
    """chunk의 검색용 문서 텍스트 (단순 반복으로 가중)."""
    parts: List[str] = []
    if c["title"]:
        parts.extend([c["title"], c["title"]])  # ×2
    if c["document_type"]:
        parts.append(c["document_type"])
    if c["product_category"]:
        parts.extend([c["product_category"], c["product_category"]])  # ×2
    if c["legal_product_name"] and c["legal_product_name"] != c["product_category"]:
        parts.extend([c["legal_product_name"], c["legal_product_name"]])
    if c["certification_type"]:
        parts.append(c["certification_type"])
    if c["keywords"]:
        kw = " ".join(c["keywords"])
        parts.extend([kw, kw, kw])  # ×3 (키워드는 강한 신호)
    if c["chunk_text"]:
        parts.append(c["chunk_text"])
    return " ".join(parts)


class RagRetriever:
    """rag_chunk_all 전체에 대한 BM25 인덱스 + rule-aware 재랭킹."""

    def __init__(self, raw_chunks: List[Dict[str, Any]]) -> None:
        self._chunks: List[Dict[str, Any]] = []
        corpus: List[List[str]] = []
        self._chunk_kw_tokens: List[Set[str]] = []
        self._chunk_meaningful: List[Set[str]] = []

        for i, raw in enumerate(raw_chunks or []):
            c = _normalize_chunk(raw, i)
            if c is None:
                continue
            doc_text = _build_doc_text(c)
            toks = tokenize(doc_text)
            self._chunks.append(c)
            corpus.append(toks)
            self._chunk_meaningful.append(meaningful_tokens(toks))
            self._chunk_kw_tokens.append(
                meaningful_tokens(tokenize(" ".join(c["keywords"])))
            )

        self._bm25: Any = None
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import]

            if corpus and any(corpus):
                self._bm25 = BM25Okapi(corpus)
                logger.info("RagRetriever 구축 완료 (%d chunk)", len(self._chunks))
            else:
                logger.warning("RAG corpus 비어있음 — retriever 비활성화")
        except ImportError:
            logger.warning("rank_bm25 미설치 — RAG retriever 비활성화")
        except Exception:
            logger.exception("RAG 인덱스 구축 실패 — retriever 비활성화")

    @property
    def available(self) -> bool:
        return self._bm25 is not None

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def _boost(
        self,
        i: int,
        top_legal_name: str,
        cert_type: str,
        product_tokens: Set[str],
        material_power_tokens: Set[str],
    ) -> float:
        c = self._chunks[i]
        boost = 0.0
        legal = c["legal_product_name"]
        if top_legal_name and legal:
            if legal == top_legal_name:
                boost += 5.0
            elif top_legal_name in legal or legal in top_legal_name:
                boost += 2.0
        if cert_type and c["certification_type"] == cert_type and cert_type != "확인 전":
            boost += 2.0
        kw_tokens = self._chunk_kw_tokens[i]
        if product_tokens and (product_tokens & kw_tokens):
            boost += 3.0
        if material_power_tokens and (material_power_tokens & kw_tokens):
            boost += 2.0
        # 상위 우선순위 문서유형에 약한 가산점 (cert/standard가 묻히지 않도록)
        rank = _PRIORITY_RANK.get(c["document_type"], len(DOC_TYPE_PRIORITY))
        if rank <= 2:
            boost += 1.5
        return boost

    def retrieve(
        self,
        query_text: str,
        top_legal_name: str = "",
        cert_type: str = "",
        product_tokens: Optional[Set[str]] = None,
        material_power_tokens: Optional[Set[str]] = None,
        allow_category_specific: bool = True,
        top_k: int = DEFAULT_TOP_K,
    ) -> List[Dict[str, Any]]:
        """근거 chunk 검색 → score 포함 chunk dict 리스트 (quota 적용).

        allow_category_specific=False (품목 미확정)면 품목 특정 근거를 반환하지 않음.
        """
        if not self._bm25 or not query_text:
            return []
        # 품목 미확정: 과확정 근거 방지 — 품목 특정 chunk를 제공하지 않음
        if not allow_category_specific and not top_legal_name:
            return []

        q_tokens = tokenize(query_text)
        if not q_tokens:
            return []
        q_meaningful = meaningful_tokens(q_tokens)
        if not q_meaningful:
            return []

        product_tokens = product_tokens or set()
        material_power_tokens = material_power_tokens or set()

        base_scores = self._bm25.get_scores(q_tokens)

        scored: List[Tuple[float, int]] = []
        for i, c in enumerate(self._chunks):
            # 불확실 케이스: 품목 특정/과확정 유형 제외
            if not allow_category_specific and c["document_type"] in _CATEGORY_SPECIFIC_TYPES:
                continue
            # 품목 확정 시: 다른 특정 품목의 chunk는 제외 (근거 정확도).
            # 품목명이 없는 chunk(일반 절차/제도 규칙 등)는 유지.
            if top_legal_name and c["legal_product_name"]:
                ln = c["legal_product_name"]
                if not (ln == top_legal_name
                        or top_legal_name in ln or ln in top_legal_name):
                    continue
            base = float(base_scores[i])
            boost = self._boost(
                i, top_legal_name, cert_type, product_tokens, material_power_tokens
            )
            total = base + boost
            if total <= 0:
                continue
            # 의미 토큰 교집합 없으면 제외 (일반어만 겹친 무관 chunk 차단)
            if not (q_meaningful & self._chunk_meaningful[i]):
                continue
            scored.append((total, i))

        if not scored:
            return []

        # 점수 내림차순 → 문서유형 우선순위 tie-break
        scored.sort(key=lambda t: (-t[0], _PRIORITY_RANK.get(
            self._chunks[t[1]]["document_type"], 99)))

        # quota 적용하며 top_k 채우기
        used_by_type: Dict[str, int] = {}
        results: List[Dict[str, Any]] = []
        for total, i in scored:
            c = self._chunks[i]
            dt = c["document_type"]
            quota = DOC_TYPE_QUOTA.get(dt, _DEFAULT_QUOTA)
            if used_by_type.get(dt, 0) >= quota:
                continue
            used_by_type[dt] = used_by_type.get(dt, 0) + 1
            out = dict(c)
            out["score"] = round(total, 3)
            out["source_refs"] = _chunk_source_refs(c)
            results.append(out)
            if len(results) >= top_k:
                break

        return results


def _chunk_source_refs(c: Dict[str, Any]) -> List[str]:
    """chunk → source_refs 문자열 목록."""
    refs: List[str] = []
    if c["source_table"] and c["source_pk"]:
        refs.append(f"{c['source_table']}:{c['source_pk']}")
    elif c["source_table"]:
        refs.append(c["source_table"])
    if c["source_file"]:
        refs.append(c["source_file"])
    return refs


def collect_refs(retrieved: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """retrieved chunks → (used_rag_chunk_ids, source_refs) 중복제거·상한 적용."""
    used_ids: List[str] = []
    refs: List[str] = []
    for c in retrieved:
        cid = c.get("chunk_id")
        if cid and cid not in used_ids:
            used_ids.append(cid)
        for r in c.get("source_refs", []):
            if r and r not in refs:
                refs.append(r)
    return used_ids[:_MAX_USED_CHUNK_IDS], refs[:_MAX_SOURCE_REFS]
