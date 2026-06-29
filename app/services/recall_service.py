from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Tuple

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
    # NEEDS_CONFIRMATION만 있는 경우: 가장 높은 후보 1개만 (과도한 union 방지)
    top = max(candidates, key=lambda c: c.confidence_score)
    name = _safe_str(top.legal_product_name)
    return [name] if name else []


def _filter_recalls(
    target_names: List[str],
    recall_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """mapped_legal_product_name 기준 1차 필터링.

    1차: mapped_legal_product_name이 target_names에 속하는 레코드
    """
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


def _build_representative_cases(
    records: List[Dict[str, Any]],
) -> List[str]:
    """대표 리콜 사례를 한 줄 문자열로 구성.

    형식: "{recallProductName} ({publishDate}): {harmDscr 요약}"
    """
    cases: List[str] = []
    seen: set[str] = set()
    for r in records[:_MAX_CASES * 3]:  # 중복 제거를 위해 여유분 순회
        product = _safe_str(r.get("recallProductName"))
        if not product or product in seen:
            continue
        seen.add(product)
        date = _safe_str(r.get("publishDate"))[:8] if r.get("publishDate") else ""
        harm = _safe_str(r.get("harmDscr")).replace("\n", " ").strip()
        harm_short = harm[:80] + "…" if len(harm) > 80 else harm
        date_str = f" ({date})" if date else ""
        line = f"{product}{date_str}: {harm_short}" if harm_short else product
        cases.append(line)
        if len(cases) >= _MAX_CASES:
            break
    return cases


def _build_prevention_points(
    top_reason_keywords: List[str],
    legal_name: str,
    check_items: List[Dict[str, Any]],
) -> List[str]:
    """safety_standard_check_items에서 리콜 reason_keywords와 매칭되는 예방 확인사항 추출.

    1순위: product_name == legal_name AND hazard_keyword in top_reason_keywords
    2순위: hazard_keyword in top_reason_keywords (품목 무관)
    """
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
                continue  # 1순위에서 처리됨
            hk = _safe_str(item.get("hazard_keyword")).lower()
            if any(hk and kw in hk or hk in kw for kw in kw_set):
                try_add(item)
            if len(result) >= _MAX_PREVENTION:
                break

    return result


def get_recall_summary(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
    app_data: Dict[str, Any],
) -> Tuple[RecallReasonSummary, List[str]]:
    """Phase 5: 국내 리콜 사유 검색 및 예방 확인사항 생성.

    Returns:
        (RecallReasonSummary, source_refs)

    - 데이터에 없는 리콜 사례/사유는 생성하지 않음
    - recall 데이터 없으면 recall_count=0, 빈 리스트 반환 (서버 죽지 않음)
    - 리콜 사유는 단순 나열 대신 safety_standard_check_items 기반 예방 확인사항으로 변환
    """
    safety = (app_data or {}).get("safety_json", {})
    master = (app_data or {}).get("master_json", {})
    recall_data: List[Dict] = safety.get("domestic_recall") or []
    check_items: List[Dict] = master.get("safety_standard_check_items") or []

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

    if not matched:
        logger.info(
            "Phase 5: '%s' 관련 리콜 레코드 없음", ", ".join(target_names)
        )
        return (
            RecallReasonSummary(
                recall_count=0,
                top_recall_reasons=[],
                representative_cases=[],
                prevention_points=[],
            ),
            [],
        )

    logger.info(
        "Phase 5: '%s' 관련 리콜 %d건 매칭", ", ".join(target_names), len(matched)
    )

    # ── reason_keywords 집계 ──────────────────────────────────────────────
    top_reasons = _aggregate_reason_keywords(matched)

    # ── 대표 사례 구성 ────────────────────────────────────────────────────
    rep_cases = _build_representative_cases(matched)

    # ── 예방 확인사항 (safety_standard_check_items 기반) ──────────────────
    # 첫 번째 타겟 품목(가장 신뢰도 높은 후보) 기준
    primary_name = target_names[0] if target_names else ""
    prevention = []
    try:
        prevention = _build_prevention_points(top_reasons, primary_name, check_items)
    except Exception as e:
        logger.warning("Phase 5 prevention_points 생성 실패: %s", e)

    # ── source_refs ───────────────────────────────────────────────────────
    source_refs: List[str] = [f"domestic_recall:{primary_name}:{len(matched)}건"]
    for r in matched[:3]:
        uid = r.get("recallUid")
        if uid:
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
