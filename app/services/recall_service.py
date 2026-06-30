from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.response import (
    CertificationDiagnosis,
    LegalProductCandidate,
    RecallReasonSummary,
)

logger = logging.getLogger(__name__)

_MAX_TOP_REASONS = 7
_MAX_CASES = 5
_MAX_PREVENTION = 10
_MAX_SOURCE_REFS = 8

# confidence_level 우선순위
_LEVEL_PRIORITY = {"CONFIRMED": 0, "CANDIDATE": 1, "NEEDS_CONFIRMATION": 2}


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


def _pick_target_names(candidates: List[LegalProductCandidate]) -> List[str]:
    """검색 대상 법정 품목명 목록 결정.

    CONFIRMED·CANDIDATE가 있으면 해당 품목만.
    없으면(NEEDS_CONFIRMATION만) 상위 후보들을 모두 포함.
    """
    if not candidates:
        return []
    confirmed_or_candidate = [
        c for c in candidates
        if c.confidence_level in ("CONFIRMED", "CANDIDATE")
    ]
    if confirmed_or_candidate:
        return list(dict.fromkeys(
            _safe_str(c.legal_product_name) for c in confirmed_or_candidate
        ))
    # CONFIRMED/CANDIDATE가 하나도 없으면(전부 NEEDS_CONFIRMATION) 품목군 미확정.
    # 0.06 같은 약한 추정으로 리콜 사례를 노출하면 과확정이므로 빈 결과 반환 (E 케이스).
    return []


def _filter_recalls(
    target_names: List[str],
    recall_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """mapped_legal_product_name 기준 1차 필터링."""
    if not target_names or not isinstance(recall_data, list):
        return []

    name_set = set(n.lower() for n in target_names if n)
    matched: List[Dict[str, Any]] = []
    for record in recall_data:
        if not isinstance(record, dict):
            continue
        mapped = _safe_str(record.get("mapped_legal_product_name")).lower()
        if mapped in name_set:
            matched.append(record)
    return matched


def _aggregate_reason_keywords(
    records: List[Dict[str, Any]],
) -> List[str]:
    """reason_keywords 빈도 집계 → 상위 키워드 리스트."""
    counter: Counter = Counter()
    for r in records:
        for kw in r.get("reason_keywords") or []:
            if kw:
                counter[_safe_str(kw)] += 1
    return [kw for kw, _ in counter.most_common(_MAX_TOP_REASONS)]


def _description_score(record: Dict[str, Any]) -> int:
    """설명 필드의 충실도 점수 (높을수록 우선 선택)."""
    score = 0
    if (record.get("harmDscr") or "").strip():
        score += 4
    if (record.get("accidentCaseDscr") or "").strip():
        score += 2
    if (record.get("publishActionDscr") or "").strip():
        score += 1
    return score


def _build_representative_cases(
    records: List[Dict[str, Any]],
    bm25_scores: Optional[Dict[int, float]] = None,
) -> Tuple[List[str], List[Any]]:
    """대표 리콜 사례를 한 줄 문자열로 구성.

    - recallProductName 기준 dedup: 설명이 가장 풍부한 레코드 선택
    - bm25_scores 제공 시: BM25 점수 우선 정렬, description_score 보조
      (키는 id(record) — recallUid 누락/중복 시에도 안정적)
    - 없으면: description_score 내림차순 정렬

    Returns:
        (cases: List[str], uids: List[Any])
    """
    best_by_name: Dict[str, Dict[str, Any]] = {}
    for r in records:
        product = _safe_str(r.get("recallProductName"))
        if not product:
            continue
        existing = best_by_name.get(product)
        if existing is None or _description_score(r) > _description_score(existing):
            best_by_name[product] = r

    if bm25_scores:
        # 설명이 있는 사례를 우선(대표 사례는 위험 패턴을 보여야 함),
        # 그 안에서 BM25 관련도 → description 충실도 순.
        # '상세 사유 없음' 사례는 설명 있는 사례가 부족할 때만 노출.
        def _sort_key(r: Dict[str, Any]) -> Tuple[int, float, int]:
            desc = _description_score(r)
            has_desc = 1 if desc > 0 else 0
            return (-has_desc, -bm25_scores.get(id(r), 0.0), -desc)
        ranked = sorted(best_by_name.values(), key=_sort_key)
    else:
        ranked = sorted(best_by_name.values(), key=_description_score, reverse=True)

    cases: List[str] = []
    uids: List[Any] = []

    for r in ranked:
        product = _safe_str(r.get("recallProductName"))
        date = str(r.get("publishDate", ""))[:8] if r.get("publishDate") else ""
        date_str = f" ({date})" if date else ""

        harm = (r.get("harmDscr") or "").replace("\n", " ").strip()
        if not harm:
            harm = (r.get("accidentCaseDscr") or "").replace("\n", " ").strip()
        if not harm:
            harm = (r.get("publishActionDscr") or "").replace("\n", " ").strip()

        if harm:
            harm_short = harm[:80] + "…" if len(harm) > 80 else harm
            line = f"{product}{date_str}: {harm_short}"
        else:
            line = f"{product}{date_str}: 상세 사유 없음"

        cases.append(line)
        uid = r.get("recallUid")
        if uid:
            uids.append(uid)
        if len(cases) >= _MAX_CASES:
            break

    return cases, uids


def _build_prevention_points(
    top_reason_keywords: List[str],
    legal_name: str,
    check_items: List[Dict[str, Any]],
) -> List[str]:
    """safety_standard_check_items에서 리콜 reason_keywords와 매칭되는 예방 확인사항 추출."""
    if not top_reason_keywords or not isinstance(check_items, list):
        return []

    kw_set = set(kw.lower() for kw in top_reason_keywords if kw)
    seen: set[str] = set()
    result: List[str] = []

    def try_add(item: Dict[str, Any]) -> None:
        if not item.get("is_active", True):
            return
        text = _safe_str(item.get("pre_launch_check_item"))
        if text and text not in seen:
            seen.add(text)
            result.append(text)

    # 1순위: product_name 일치
    for item in check_items:
        if not isinstance(item, dict):
            continue
        if _safe_str(item.get("product_name")) != legal_name:
            continue
        hk = _safe_str(item.get("hazard_keyword")).lower()
        if any(hk and kw in hk or hk in kw for kw in kw_set):
            try_add(item)
        if len(result) >= _MAX_PREVENTION:
            return result

    # 2순위: 품목 무관, hazard_keyword 매칭
    if len(result) < _MAX_PREVENTION:
        for item in check_items:
            if not isinstance(item, dict):
                continue
            if _safe_str(item.get("product_name")) == legal_name:
                continue
            hk = _safe_str(item.get("hazard_keyword")).lower()
            if any(hk and kw in hk or hk in kw for kw in kw_set):
                try_add(item)
            if len(result) >= _MAX_PREVENTION:
                break

    return result


def _should_allow_supplemental(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
) -> bool:
    """BM25 보조 검색 허용 여부 (E 케이스 과확정 방지).

    품목군이 충분히 확정된 경우에만 보조 검색을 허용한다:
    - 최상위 후보가 CONFIRMED, 또는 CANDIDATE이면서 score>=0.5
    - 그리고 인증유형이 '확인 전'이 아님

    정체불명 입력(CONFIRMED 없음 / cert '확인 전')에서는 임의 리콜 사례를
    노출하지 않는다.
    """
    if not candidates:
        return False
    cert_type = (cert_diagnosis.certification_type or "").strip()
    if cert_type in ("", "확인 전", "미정"):
        return False
    best = max(candidates, key=lambda c: c.confidence_score)
    if best.confidence_level == "CONFIRMED":
        return True
    if best.confidence_level == "CANDIDATE" and best.confidence_score >= 0.5:
        return True
    return False


def get_recall_summary(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
    app_data: Dict[str, Any],
    query_text: Optional[str] = None,
) -> Tuple[RecallReasonSummary, List[str]]:
    """Phase 5: 국내 리콜 사유 검색 및 예방 확인사항 생성.

    query_text가 주어지면:
    - exact match 레코드를 BM25 점수 내림차순으로 정렬해 대표 사례 선택
    - exact match가 없으면 BM25 보조 검색으로 유사 사례 supplemental_cases 제공

    Returns:
        (RecallReasonSummary, source_refs)
    """
    safety = (app_data or {}).get("safety_json", {})
    master = (app_data or {}).get("master_json", {})
    recall_data: List[Dict] = safety.get("domestic_recall") or []
    check_items: List[Dict] = master.get("safety_standard_check_items") or []
    bm25_idx = (app_data or {}).get("recall_bm25_idx")

    empty = RecallReasonSummary(
        recall_count=0,
        top_recall_reasons=[],
        representative_cases=[],
        prevention_points=[],
    )

    if not recall_data:
        logger.info("Phase 5: domestic_recall 데이터 없음 → 빈 결과 반환")
        return empty, []

    target_names = _pick_target_names(candidates)
    if not target_names:
        logger.info("Phase 5: 검색 대상 법정 품목명 없음")
        return empty, []

    # ── 리콜 레코드 필터링 ─────────────────────────────────────────────────
    try:
        matched = _filter_recalls(target_names, recall_data)
    except Exception as e:
        logger.warning("Phase 5 recall filter 실패: %s", e)
        return empty, []

    # ── exact match 없을 때: BM25 보조 검색 ──────────────────────────────
    if not matched:
        logger.info("Phase 5: '%s' 관련 리콜 레코드 없음", ", ".join(target_names))

        supplemental_cases: List[str] = []
        allow_supp = _should_allow_supplemental(candidates, cert_diagnosis)
        if not allow_supp:
            logger.info(
                "Phase 5: 품목군 미확정 → BM25 보조 검색 차단 (과확정 방지)"
            )
        elif query_text and bm25_idx and getattr(bm25_idx, "available", False):
            try:
                exclude = set(n.lower() for n in target_names if n)
                bm25_results = bm25_idx.search_top_k(
                    query_text, top_k=20, exclude_legal_names=exclude
                )
                if bm25_results:
                    # 최고점이 0 이하면 의미 없음 / 최고점 대비 30% 미만 제거
                    max_score = bm25_results[0][1]
                    if max_score > 0:
                        threshold = max_score * 0.3
                        filtered = [
                            (r, s) for r, s in bm25_results if s >= threshold
                        ][:_MAX_CASES]
                        sup_records = [r for r, _ in filtered]
                        sup_scores = {id(r): s for r, s in filtered}
                        sup_cases, _ = _build_representative_cases(
                            sup_records, sup_scores
                        )
                        supplemental_cases = sup_cases
                        logger.info(
                            "Phase 5 BM25 보조 검색: %d건 (threshold=%.2f)",
                            len(supplemental_cases), threshold,
                        )
            except Exception as e:
                logger.warning("Phase 5 BM25 보조 검색 실패: %s", e)

        return (
            RecallReasonSummary(
                recall_count=0,
                top_recall_reasons=[],
                representative_cases=[],
                prevention_points=[],
                supplemental_cases=supplemental_cases,
            ),
            [],
        )

    logger.info(
        "Phase 5: '%s' 관련 리콜 %d건 매칭", ", ".join(target_names), len(matched)
    )

    # ── reason_keywords 집계 ──────────────────────────────────────────────
    top_reasons = _aggregate_reason_keywords(matched)

    # ── BM25 점수로 대표 사례 정렬 (exact match subset, 품목군 이미 확정) ──
    bm25_scores: Optional[Dict[int, float]] = None
    if query_text and bm25_idx and getattr(bm25_idx, "available", False):
        try:
            bm25_scores = bm25_idx.score_records(query_text, matched)
        except Exception as e:
            logger.warning("Phase 5 BM25 score_records 실패: %s", e)

    rep_cases, rep_uids = _build_representative_cases(matched, bm25_scores)

    # ── 예방 확인사항 (safety_standard_check_items 기반) ──────────────────
    primary_name = target_names[0] if target_names else ""
    prevention: List[str] = []
    try:
        prevention = _build_prevention_points(top_reasons, primary_name, check_items)
    except Exception as e:
        logger.warning("Phase 5 prevention_points 생성 실패: %s", e)

    # ── source_refs ───────────────────────────────────────────────────────
    source_refs: List[str] = [f"domestic_recall:{primary_name}:{len(matched)}건"]
    for uid in rep_uids:
        source_refs.append(f"domestic_recall:uid={uid}")
    source_refs = source_refs[:_MAX_SOURCE_REFS]

    return (
        RecallReasonSummary(
            recall_count=len(matched),
            top_recall_reasons=top_reasons,
            representative_cases=rep_cases,
            prevention_points=prevention,
        ),
        source_refs,
    )
