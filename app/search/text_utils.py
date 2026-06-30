"""검색 공통 토큰화 유틸 (형태소 분석기 없음).

BM25 리콜 검색(recall_bm25.py)과 동일한 토큰화 전략을 RAG retriever에서 재사용.
- 소문자화 + 특수문자 제거 + 공백 분리
- len>=2 토큰 유지, 중요 단일 글자('납')는 예외 보존
- 2-gram 보조 토큰
- 품목 구분력 없는 일반어(GENERIC_TOKENS)는 meaningful overlap 판정에서 제외
"""
from __future__ import annotations

import re
from typing import List, Set

# 한 글자지만 유해물질로 중요한 토큰 (len>=2 필터 예외 보존)
IMPORTANT_SINGLE_CHAR_TOKENS = {"납"}

# 품목 구분력이 없는 일반어 — meaningful overlap 판정에서만 제외 (BM25 scoring엔 영향 X)
GENERIC_TOKENS: Set[str] = {
    "어린이", "어린이용", "어린", "린이", "이용",
    "유아", "유아용", "용품",
    "제품", "물건", "사용", "출시", "수입", "제조",
    "대상", "연령", "안전", "기준", "확인", "필요",
    "관련", "검사", "시험", "인증", "공통", "기타",
    "정보", "자료", "품목", "후보",
    "구매", "판매", "가능", "여부", "문의",
}


def tokenize(text: str) -> List[str]:
    """공백 분리 + 소문자 + 특수문자 제거 + 2-gram 보조 토큰."""
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


def meaningful_tokens(tokens: List[str]) -> Set[str]:
    """일반어(GENERIC_TOKENS)를 제외한 의미 토큰 집합."""
    return {t for t in tokens if t not in GENERIC_TOKENS}
