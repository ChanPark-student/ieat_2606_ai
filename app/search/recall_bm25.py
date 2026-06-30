"""BM25 인덱스 — 리콜 사례 관련성 검색 및 정렬 전용.

주의:
- 법정 품목명 매칭·인증유형 판단에는 사용하지 않음.
- rank_bm25 미설치 시 graceful degradation (빈 결과 반환).
"""
from __future__ import annotations

import ast
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

BM25_TOP_K = 20


def _tokenize(text: str) -> List[str]:
    """간단한 한국어 토크나이저 (형태소 분석 없음).

    공백 분리 + 소문자화 + 특수문자 제거 + 2-gram 보조 토큰.
    """
    if not text:
        return []
    text = text.lower()
    text = re.sub(r"[^\w가-힣a-z0-9]", " ", text)
    unigrams = [t for t in text.split() if len(t) >= 2]
    bigrams = [
        tok[i : i + 2]
        for tok in unigrams
        if len(tok) >= 3
        for i in range(len(tok) - 1)
    ]
    return unigrams + bigrams


def _build_doc_text(record: Dict[str, Any]) -> str:
    """레코드의 주요 필드를 BM25 검색용 단일 텍스트로 조합."""
    parts: List[str] = []

    product = str(record.get("recallProductName") or "").strip()
    if product:
        parts.extend([product, product, product])  # 관련성 가중치

    model = str(record.get("recallModelName") or "").strip()
    if model:
        parts.append(model)

    legal = str(record.get("mapped_legal_product_name") or "").strip()
    if legal:
        parts.extend([legal, legal])  # 가중치

    rk = record.get("reason_keywords")
    if rk:
        try:
            kws = ast.literal_eval(str(rk)) if isinstance(rk, str) else rk
            if isinstance(kws, list):
                parts.extend(str(k) for k in kws if k)
        except Exception:
            parts.append(str(rk))

    combined = str(record.get("combined_recall_text") or "").strip()
    if combined:
        parts.append(combined[:300])

    return " ".join(parts)


class RecallBM25Index:
    """domestic_recall 전체 레코드에 대한 BM25 인덱스."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records
        self._uid_to_idx: Dict[str, int] = {}
        corpus: List[List[str]] = []

        for i, r in enumerate(records):
            uid = str(r.get("recallUid", i))
            self._uid_to_idx[uid] = i
            corpus.append(_tokenize(_build_doc_text(r)))

        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import]

            self._bm25: Any = BM25Okapi(corpus)
            logger.info("RecallBM25Index 구축 완료 (%d건)", len(records))
        except ImportError:
            self._bm25 = None
            logger.warning("rank_bm25 미설치 — BM25 리콜 검색 비활성화")

    @property
    def available(self) -> bool:
        return self._bm25 is not None

    def score_records(
        self,
        query: str,
        target_records: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """대상 레코드 부분집합의 BM25 점수 반환 (uid → score).

        전체 인덱스를 한 번에 스코어링 후 대상 uid만 추출 — O(N) 1회.
        """
        if not self._bm25 or not query or not target_records:
            return {}
        q_tokens = _tokenize(query)
        if not q_tokens:
            return {}
        all_scores = self._bm25.get_scores(q_tokens)
        result: Dict[str, float] = {}
        for r in target_records:
            uid = str(r.get("recallUid", ""))
            idx = self._uid_to_idx.get(uid)
            if idx is not None:
                result[uid] = float(all_scores[idx])
        return result

    def search_top_k(
        self,
        query: str,
        top_k: int = BM25_TOP_K,
        exclude_legal_names: Optional[Set[str]] = None,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """BM25 보조 검색 — (record, score) 내림차순 리스트.

        exclude_legal_names에 속한 mapped_legal_product_name을 가진 레코드는
        제외 (exact match 품목군 중복 방지).
        점수가 0인 레코드는 반환하지 않음.
        """
        if not self._bm25 or not query:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        all_scores = self._bm25.get_scores(q_tokens)
        ranked_idx = sorted(
            (i for i in range(len(all_scores)) if all_scores[i] > 0),
            key=lambda i: -all_scores[i],
        )

        exclude = set(n.lower() for n in (exclude_legal_names or set()))
        results: List[Tuple[Dict[str, Any], float]] = []

        for idx in ranked_idx:
            r = self._records[idx]
            if exclude:
                mapped = str(r.get("mapped_legal_product_name") or "").lower()
                if mapped in exclude:
                    continue
            results.append((r, float(all_scores[idx])))
            if len(results) >= top_k:
                break

        return results
