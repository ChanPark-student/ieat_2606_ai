from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from app.schemas.response import CertificationDiagnosis, LegalProductCandidate

logger = logging.getLogger(__name__)

# confidence_level 우선순위: 낮을수록 더 신뢰
_LEVEL_PRIORITY: Dict[str, int] = {
    "CONFIRMED": 0,
    "CANDIDATE": 1,
    "NEEDS_CONFIRMATION": 2,
    "NO_MATCH": 9,
}

_MAX_CHECKLIST = 15  # launch_checklist 최대 항목 수
_MAX_SOURCE_REFS = 10


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _pick_best_candidate(
    candidates: List[LegalProductCandidate],
) -> LegalProductCandidate | None:
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (
            _LEVEL_PRIORITY.get(c.confidence_level, 9),
            -c.confidence_score,
        ),
    )


def _lookup_annex_rule(
    legal_name: str,
    annex_rules: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """certification_annex_rule에서 product_name 기준 룰 조회."""
    if not isinstance(annex_rules, list):
        return None
    for rule in annex_rules:
        if not isinstance(rule, dict):
            continue
        if rule.get("is_deleted"):
            continue
        if _safe_str(rule.get("product_name")) == legal_name:
            return rule
    return None


def _lookup_std_docs(
    legal_name: str,
    std_docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """safety_standard_document에서 is_latest=True 문서 조회."""
    if not isinstance(std_docs, list):
        return []
    return [
        d for d in std_docs
        if isinstance(d, dict)
        and d.get("is_active", True)
        and d.get("is_latest", False)
        and _safe_str(d.get("product_name")) == legal_name
    ]


def _lookup_check_items(
    legal_name: str,
    check_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """safety_standard_check_items에서 is_active 항목 조회."""
    if not isinstance(check_items, list):
        return []
    return [
        it for it in check_items
        if isinstance(it, dict)
        and it.get("is_active", True)
        and _safe_str(it.get("product_name")) == legal_name
    ]


def diagnose_certification(
    candidates: List[LegalProductCandidate],
    app_data: Dict[str, Any],
) -> Tuple[CertificationDiagnosis, List[str], List[str]]:
    """Phase 3: 인증유형 및 안전기준 조회.

    Returns:
        (CertificationDiagnosis, launch_checklist, source_refs)

    - 데이터에 없는 인증유형/안전기준은 생성하지 않음
    - 파일/매칭 없으면 에러 없이 빈 결과 반환
    """
    master = (app_data or {}).get("master_json", {})
    annex_rules: List[Dict[str, Any]] = master.get("certification_annex_rule") or []
    std_docs: List[Dict[str, Any]] = master.get("safety_standard_document") or []
    check_items: List[Dict[str, Any]] = master.get("safety_standard_check_items") or []

    # 후보 없음 → baseline 빈 결과
    if not candidates:
        return (
            CertificationDiagnosis(
                certification_type="확인 전",
                applied_standards=[],
                judgement_level="NO_MATCH",
                source_refs=[],
            ),
            [],
            [],
        )

    top = _pick_best_candidate(candidates)
    if top is None:
        return (
            CertificationDiagnosis(
                certification_type="확인 전",
                applied_standards=[],
                judgement_level="NO_MATCH",
                source_refs=[],
            ),
            [],
            [],
        )

    # 과확정 방지: 최우선 후보가 NEEDS_CONFIRMATION 수준이면 (CONFIRMED/CANDIDATE가 하나도 없음)
    # 특정 품목으로 인증유형·안전기준·체크리스트를 확정하지 않는다.
    # 후보 자체는 응답에 남아 사용자가 확인할 수 있다.
    if _safe_str(top.confidence_level) == "NEEDS_CONFIRMATION":
        logger.info(
            "Phase 3 과확정 방지: 최우선 후보 '%s'가 NEEDS_CONFIRMATION → 확인 전 반환",
            _safe_str(top.legal_product_name),
        )
        return (
            CertificationDiagnosis(
                certification_type="확인 전",
                applied_standards=[],
                judgement_level="NEEDS_CONFIRMATION",
                source_refs=[],
            ),
            [],
            [],
        )

    legal_name = _safe_str(top.legal_product_name)
    # product_category_index에서 이미 가져온 certification_type이 있으면 fallback으로 사용
    cert_type: str = _safe_str(top.certification_type)

    applied_standards: List[str] = []
    source_refs: List[str] = []

    # ── Step 1: certification_annex_rule 조회 ──────────────────────────────
    try:
        rule = _lookup_annex_rule(legal_name, annex_rules)
        if rule:
            # 가장 권위 있는 source이므로 certification_type을 덮어씀
            rule_cert_type = _safe_str(rule.get("certification_type"))
            if rule_cert_type:
                cert_type = rule_cert_type

            common_std = _safe_str(rule.get("common_safety_standard"))
            product_std = _safe_str(rule.get("product_safety_standard"))
            if common_std:
                applied_standards.append(common_std)
            if product_std and product_std not in applied_standards:
                applied_standards.append(product_std)

            if rule.get("rule_id"):
                source_refs.append(f"certification_annex_rule:{rule['rule_id']}")
            if rule.get("source_file"):
                source_refs.append(rule["source_file"])
            logger.info(
                "Phase 3 annex_rule matched: %s → %s", legal_name, cert_type
            )
        else:
            logger.info(
                "Phase 3 annex_rule: no match for '%s', using index fallback", legal_name
            )
            # product_category_index에서 이미 넘어온 standard 정보 활용 (있으면)
            # (candidates의 match_basis나 기타 필드에는 standard 직접 없으므로 skip)
    except Exception as e:
        logger.warning("Phase 3 annex_rule lookup failed: %s", e)

    # ── Step 2: safety_standard_document 조회 (is_latest=True만) ───────────
    try:
        matched_docs = _lookup_std_docs(legal_name, std_docs)
        for doc in matched_docs:
            doc_id = doc.get("standard_doc_id")
            src_file = _safe_str(doc.get("source_file"))
            if doc_id:
                source_refs.append(f"safety_standard_document:{doc_id}")
            if src_file and src_file not in source_refs:
                # 파일명만 source_refs에 추가 (경로 제외)
                import os
                source_refs.append(os.path.basename(src_file))
        if matched_docs:
            logger.info(
                "Phase 3 std_doc matched: %d latest doc(s) for '%s'",
                len(matched_docs), legal_name,
            )
    except Exception as e:
        logger.warning("Phase 3 std_doc lookup failed: %s", e)

    # ── Step 3: safety_standard_check_items → launch_checklist ────────────
    launch_checklist: List[str] = []
    try:
        matched_items = _lookup_check_items(legal_name, check_items)
        seen_items: set[str] = set()
        for it in matched_items:
            item_text = _safe_str(it.get("pre_launch_check_item"))
            if item_text and item_text not in seen_items:
                seen_items.add(item_text)
                launch_checklist.append(item_text)
            if len(launch_checklist) >= _MAX_CHECKLIST:
                break

        # check_item source_refs (첫 3개만)
        for it in matched_items[:3]:
            cid = it.get("check_item_id")
            if cid:
                source_refs.append(f"safety_standard_check_items:{cid}")

        if launch_checklist:
            logger.info(
                "Phase 3 check_items: %d items for '%s'",
                len(launch_checklist), legal_name,
            )
        else:
            logger.info(
                "Phase 3 check_items: no items for '%s'", legal_name
            )
    except Exception as e:
        logger.warning("Phase 3 check_items lookup failed: %s", e)

    # source_refs 중복 제거 및 제한
    seen_refs: set[str] = set()
    deduped_refs: List[str] = []
    for ref in source_refs:
        if ref and ref not in seen_refs:
            seen_refs.add(ref)
            deduped_refs.append(ref)
    source_refs = deduped_refs[:_MAX_SOURCE_REFS]

    diag = CertificationDiagnosis(
        certification_type=cert_type or "확인 전",
        applied_standards=applied_standards,
        judgement_level=top.confidence_level,
        source_refs=source_refs,
    )

    return diag, launch_checklist, source_refs
