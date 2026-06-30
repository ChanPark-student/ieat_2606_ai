"""BM25 인덱스 — 리콜 사례 관련성 검색 및 정렬 전용.

절대 원칙:
- BM25는 (1) 국내 리콜 대표 사례 정렬, (2) exact match 없을 때의 제한적 보조 검색에만 사용.
- 법정 품목명 확정·인증유형·안전기준·KC 매칭·LLM 보고서 판단에는 절대 사용하지 않음.
- rank_bm25 미설치/구축 실패 시 graceful degradation (빈 결과 → 기존 정렬 fallback).
"""
from __future__ import annotations

import ast
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

BM25_TOP_K = 20

# 한 글자지만 리콜 사유에서 중요한 유해물질 토큰 (len>=2 필터에서 예외 보존)
IMPORTANT_SINGLE_CHAR_TOKENS = {"납"}

# 품목 구분력이 없는 일반어 — supplemental 검색의 meaningful overlap 판정에서 제외.
# (BM25 scoring 자체는 그대로 두고, 사용자 노출 전 guard에만 사용)
GENERIC_TOKENS = {
    "어린이", "어린이용", "어린", "린이", "이용",
    "유아", "유아용", "용품",
    "제품", "물건", "사용", "출시", "수입", "제조",
    "대상", "연령", "안전", "기준", "확인", "필요",
    "관련", "검사", "시험", "인증", "공통", "기타",
    "구매", "판매", "가능", "여부", "문의",
}


def _tokenize(text: str) -> List[str]:
    """간단한 한국어 토크나이저 (형태소 분석 없음).

    공백 분리 + 소문자화 + 특수문자 제거 + 2-gram 보조 토큰.
    len>=2 토큰만 유지하되, IMPORTANT_SINGLE_CHAR_TOKENS(예: '납')는 예외 보존.
    """
    if not text:
        return []
    text = text.lower()
    text = re.sub(r"[^\w가-힣a-z0-9]", " ", text)
    raw = text.split()
    unigrams = [
        t for t in raw
        if len(t) >= 2 or t in IMPORTANT_SINGLE_CHAR_TOKENS
    ]
    bigrams = [
        tok[i : i + 2]
        for tok in raw
        if len(tok) >= 3
        for i in range(len(tok) - 1)
    ]
    return unigrams + bigrams


def _meaningful_tokens(tokens: List[str]) -> Set[str]:
    """일반어(GENERIC_TOKENS)를 제외한 의미 토큰 집합."""
    return {t for t in tokens if t not in GENERIC_TOKENS}


def _build_doc_text(record: Dict[str, Any]) -> str:
    """레코드의 주요 필드를 BM25 검색용 단일 텍스트로 조합.

    combined_recall_text는 이미 harmDscr+accidentCaseDscr를 포함하므로 별도 추가하지 않음.
    recallTypeName(자발적리콜 등 리콜 유형)은 품목 구분력이 없어 제외.
    """
    parts: List[str] = []

    product = str(record.get("recallProductName") or "").strip()
    if product:
        parts.extend([product, product, product])  # 제품명 관련성 가중

    model = str(record.get("recallModelName") or "").strip()
    if model:
        parts.append(model)

    brand = str(record.get("recallBrandName") or "").strip()
    if brand:
        parts.append(brand)

    legal = str(record.get("mapped_legal_product_name") or "").strip()
    if legal:
        parts.extend([legal, legal])  # 품목군 가중

    rk = record.get("reason_keywords")
    if rk:
        try:
            kws = ast.literal_eval(str(rk)) if isinstance(rk, str) else rk
            if isinstance(kws, list):
                parts.extend(str(k) for k in kws if k)
        except Exception:
            parts.append(str(rk))

    # combined_recall_text: avg=218, p90=372, max=2117 → 800자면 대부분의 리콜 사유 보존
    combined = str(record.get("combined_recall_text") or "").strip()
    if combined:
        parts.append(combined[:800])

    return " ".join(parts)


class RecallBM25Index:
    """domestic_recall 전체 레코드에 대한 BM25 인덱스."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records
        # id(record) 기반 매핑 — recallUid가 비거나 중복돼도 안정적
        self._record_id_to_idx: Dict[int, int] = {}
        self._uid_to_idx: Dict[str, int] = {}
        corpus: List[List[str]] = []

        for i, r in enumerate(records):
            self._record_id_to_idx[id(r)] = i
            uid = str(r.get("recallUid", "")).strip()
            if uid and uid not in self._uid_to_idx:
                self._uid_to_idx[uid] = i
            corpus.append(_tokenize(_build_doc_text(r)))

        self._bm25: Any = None
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import]

            if corpus and any(corpus):
                self._bm25 = BM25Okapi(corpus)
                logger.info("RecallBM25Index 구축 완료 (%d건)", len(records))
            else:
                logger.warning("BM25 corpus 비어있음 — 비활성화")
        except ImportError:
            logger.warning("rank_bm25 미설치 — BM25 리콜 검색 비활성화 (기존 정렬 fallback)")
        except Exception:
            logger.exception("BM25 인덱스 구축 실패 — 기존 리콜 정렬로 fallback")

    @property
    def available(self) -> bool:
        return self._bm25 is not None

    def _resolve_idx(self, record: Dict[str, Any]) -> Optional[int]:
        """record → corpus index. id() 우선, 실패 시 recallUid 폴백."""
        idx = self._record_id_to_idx.get(id(record))
        if idx is not None:
            return idx
        uid = str(record.get("recallUid", "")).strip()
        if uid:
            return self._uid_to_idx.get(uid)
        return None

    def score_records(
        self,
        query: str,
        target_records: List[Dict[str, Any]],
    ) -> Dict[int, float]:
        """대상 레코드 부분집합의 BM25 점수 반환 (id(record) → score).

        전체 인덱스를 한 번에 스코어링 후 대상만 추출 — O(N) 1회.
        키를 id(record)로 두어 recallUid 누락/중복 시에도 정렬이 안정적.
        """
        if not self._bm25 or not query or not target_records:
            return {}
        q_tokens = _tokenize(query)
        if not q_tokens:
            return {}
        all_scores = self._bm25.get_scores(q_tokens)
        result: Dict[int, float] = {}
        for r in target_records:
            idx = self._resolve_idx(r)
            if idx is not None:
                result[id(r)] = float(all_scores[idx])
        return result

    def search_top_k(
        self,
        query: str,
        top_k: int = BM25_TOP_K,
        exclude_legal_names: Optional[Set[str]] = None,
        require_meaningful_overlap: bool = True,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """BM25 보조 검색 — (record, score) 내림차순 리스트.

        - exclude_legal_names에 속한 mapped_legal_product_name 레코드는 제외 (exact 중복 방지).
        - 점수 0 이하 레코드는 반환하지 않음.
        - require_meaningful_overlap=True: 질의가 일반어뿐이면 빈 결과,
          개별 결과도 질의·문서의 의미 토큰 교집합이 없으면 제외 (일반어만 겹친 결과 차단).
        """
        if not self._bm25 or not query:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        q_meaningful = _meaningful_tokens(q_tokens)
        # 질의가 일반어로만 구성되면 보조 검색 생략
        if require_meaningful_overlap and not q_meaningful:
            logger.info("BM25 보조 검색 생략: 질의에 의미 토큰 없음 (일반어뿐)")
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
            if require_meaningful_overlap:
                doc_meaningful = _meaningful_tokens(
                    _tokenize(_build_doc_text(r))
                )
                if not (q_meaningful & doc_meaningful):
                    continue  # 일반어만 겹친 결과 제외
            results.append((r, float(all_scores[idx])))
            if len(results) >= top_k:
                break

        return results
