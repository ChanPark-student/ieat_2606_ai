from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from app.schemas.response import (
    CertificationDiagnosis,
    InstitutionGuidance,
    InstitutionInfo,
    LegalProductCandidate,
)

logger = logging.getLogger(__name__)

_FALLBACK_SUMMARY = (
    "기준 데이터에서 기관 정보를 확인하지 못했습니다. 관계 기관 확인이 필요합니다."
)
_MAX_INSTITUTIONS = 10


def _safe_str(val: Any) -> str:
    return str(val).strip() if val is not None else ""


# ── 조회 헬퍼 ──────────────────────────────────────────────────────────────

def _find_process_rule(
    cert_type: str,
    rules: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """certification_process_rule에서 certification_type 기준 조회."""
    for rule in rules:
        if isinstance(rule, dict) and _safe_str(rule.get("certification_type")) == cert_type:
            return rule
    return None


def _find_supplier_scope(
    legal_name: str,
    cert_type: str,
    supplier_scope: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """supplier_conformity_scope에서 (product_name, certification_type) 기준 조회."""
    for entry in supplier_scope:
        if not isinstance(entry, dict):
            continue
        if not entry.get("is_active", True):
            continue
        if (
            _safe_str(entry.get("product_name")) == legal_name
            and _safe_str(entry.get("certification_type")) == cert_type
        ):
            return entry
    return None


def _build_institutions_from_lookup(
    legal_name: str,
    cert_type: str,
    lookup: List[Dict[str, Any]],
    institution_role: str,
) -> List[InstitutionInfo]:
    """product_institution_lookup에서 (product_name, certification_type) 기준 기관 목록."""
    for entry in lookup:
        if not isinstance(entry, dict):
            continue
        if (
            _safe_str(entry.get("product_name")) == legal_name
            and _safe_str(entry.get("certification_type")) == cert_type
        ):
            role = _safe_str(entry.get("institution_role")) or institution_role
            result: List[InstitutionInfo] = []
            for inst in entry.get("institutions") or []:
                if not isinstance(inst, dict):
                    continue
                result.append(
                    InstitutionInfo(
                        institution_name=_safe_str(inst.get("institution_name")),
                        short_name=_safe_str(inst.get("short_name")),
                        institution_role=role,
                        certification_type=cert_type,
                        website_url=_safe_str(inst.get("website_url")),
                        source_refs=[f"product_institution_lookup:{legal_name}"],
                    )
                )
            if result:
                return result
    return []


def _build_institutions_from_scope(
    legal_name: str,
    cert_type: str,
    scope_data: List[Dict[str, Any]],
) -> List[InstitutionInfo]:
    """institution_scope에서 (product_name, certification_type) → 없으면 cert_type만 기준."""
    active = [
        s for s in scope_data
        if isinstance(s, dict) and s.get("is_active", True)
        and _safe_str(s.get("certification_type")) == cert_type
    ]

    # 1순위: product_name 일치
    product_match = [s for s in active if _safe_str(s.get("product_name")) == legal_name]
    source = product_match if product_match else active

    seen: set[str] = set()
    result: List[InstitutionInfo] = []
    for s in source:
        name = _safe_str(s.get("institution_name"))
        if not name or name in seen:
            continue
        seen.add(name)
        scope_label = "product_match" if product_match else "cert_type_fallback"
        result.append(
            InstitutionInfo(
                institution_name=name,
                short_name=_safe_str(s.get("short_name")),
                institution_role=_safe_str(s.get("institution_role")),
                certification_type=cert_type,
                website_url=_safe_str(s.get("website_url")),
                product_scope=[_safe_str(s.get("product_name"))] if s.get("product_name") else [],
                source_refs=[f"institution_scope:{s.get('scope_id', scope_label)}"],
            )
        )
        if len(result) >= _MAX_INSTITUTIONS:
            break
    return result


def _build_institutions_from_test(
    legal_name: str,
    cert_type: str,
    test_data: List[Dict[str, Any]],
) -> List[InstitutionInfo]:
    """test_institution에서 (product_scope 포함, certification_type) 기준 기관 목록."""
    active = [
        t for t in test_data
        if isinstance(t, dict) and t.get("is_active", True)
        and _safe_str(t.get("certification_type")) == cert_type
    ]

    # 1순위: product_scope에 legal_name 포함
    product_match = [
        t for t in active
        if legal_name in (t.get("product_scope") or [])
    ]
    source = product_match if product_match else active

    seen: set[str] = set()
    result: List[InstitutionInfo] = []
    for t in source:
        name = _safe_str(t.get("institution_name"))
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(
            InstitutionInfo(
                institution_name=name,
                short_name=_safe_str(t.get("short_name")),
                institution_role=_safe_str(t.get("institution_role")),
                certification_type=cert_type,
                website_url=_safe_str(t.get("website_url")),
                product_scope=list(t.get("product_scope") or []),
                source_refs=[f"test_institution:{name}"],
            )
        )
        if len(result) >= _MAX_INSTITUTIONS:
            break
    return result


# ── 메인 함수 ──────────────────────────────────────────────────────────────

def get_institution_guidance(
    candidates: List[LegalProductCandidate],
    cert_diagnosis: CertificationDiagnosis,
    app_data: Dict[str, Any],
) -> Tuple[InstitutionGuidance, List[str]]:
    """Phase 4: 기관 및 절차 안내.

    Returns:
        (InstitutionGuidance, source_refs)

    - 데이터에 없는 기관명/절차는 생성하지 않음
    - 파일/매칭 없으면 에러 없이 fallback 반환
    """
    master = (app_data or {}).get("master_json", {})
    process_rules: List[Dict] = master.get("certification_process_rule") or []
    supplier_scope: List[Dict] = master.get("supplier_conformity_scope") or []
    product_lookup: List[Dict] = master.get("product_institution_lookup") or []
    inst_scope: List[Dict] = master.get("institution_scope") or []
    test_inst: List[Dict] = master.get("test_institution") or []

    cert_type = _safe_str(cert_diagnosis.certification_type) if cert_diagnosis else ""
    legal_name = _safe_str(candidates[0].legal_product_name) if candidates else ""

    # 후보·인증유형이 없으면 baseline 반환
    if not cert_type or cert_type == "확인 전":
        return (
            InstitutionGuidance(
                institution_required=False,
                summary=_FALLBACK_SUMMARY,
                candidate_institutions=[],
            ),
            [],
        )

    source_refs: List[str] = []
    summary_parts: List[str] = []
    institution_required: bool = False

    # ── Step 1: certification_process_rule ────────────────────────────────
    institution_role = ""
    try:
        rule = _find_process_rule(cert_type, process_rules)
        if rule:
            institution_required = bool(rule.get("institution_required", False))
            rule_summary = _safe_str(rule.get("summary"))
            if rule_summary:
                summary_parts.append(rule_summary)
            institution_role = _safe_str(rule.get("institution_role"))
            rule_id = rule.get("process_rule_id")
            if rule_id:
                source_refs.append(f"certification_process_rule:{rule_id}")
            logger.info("Phase 4 process_rule matched: %s (required=%s)", cert_type, institution_required)
        else:
            logger.info("Phase 4 process_rule: no match for '%s'", cert_type)
    except Exception as e:
        logger.warning("Phase 4 process_rule lookup failed: %s", e)

    # ── Step 2: supplier_conformity_scope (공급자적합성확인 전용) ──────────
    try:
        if legal_name:
            supplier_entry = _find_supplier_scope(legal_name, cert_type, supplier_scope)
            if supplier_entry:
                institution_required = bool(supplier_entry.get("institution_required", institution_required))
                guidance_text = _safe_str(supplier_entry.get("institution_guidance"))
                if guidance_text:
                    summary_parts.append(guidance_text)
                scope_id = supplier_entry.get("supplier_scope_id")
                if scope_id:
                    source_refs.append(f"supplier_conformity_scope:{scope_id}")
                logger.info("Phase 4 supplier_scope matched: %s", legal_name)
    except Exception as e:
        logger.warning("Phase 4 supplier_scope lookup failed: %s", e)

    # ── Step 3: 기관 목록 조회 (우선순위: lookup → scope → test_institution) ──
    candidate_institutions: List[InstitutionInfo] = []
    try:
        if legal_name and institution_required:
            # 3-1: product_institution_lookup (가장 구체적)
            candidate_institutions = _build_institutions_from_lookup(
                legal_name, cert_type, product_lookup, institution_role
            )
            if candidate_institutions:
                logger.info(
                    "Phase 4 lookup: %d institutions for '%s'", len(candidate_institutions), legal_name
                )
            else:
                # 3-2: institution_scope
                candidate_institutions = _build_institutions_from_scope(
                    legal_name, cert_type, inst_scope
                )
                if candidate_institutions:
                    logger.info(
                        "Phase 4 scope: %d institutions for '%s'", len(candidate_institutions), legal_name
                    )
                else:
                    # 3-3: test_institution
                    candidate_institutions = _build_institutions_from_test(
                        legal_name, cert_type, test_inst
                    )
                    if candidate_institutions:
                        logger.info(
                            "Phase 4 test_institution: %d institutions for '%s'",
                            len(candidate_institutions), legal_name,
                        )
    except Exception as e:
        logger.warning("Phase 4 institution lookup failed: %s", e)

    # ── 최종 summary 조합 ─────────────────────────────────────────────────
    if summary_parts:
        # 중복 제거: 두 번째 이후 문장이 첫 번째에 포함되면 생략
        final_parts: List[str] = [summary_parts[0]]
        for part in summary_parts[1:]:
            if part not in final_parts[0]:
                final_parts.append(part)
        summary = " ".join(final_parts)
    else:
        summary = _FALLBACK_SUMMARY

    return (
        InstitutionGuidance(
            institution_required=institution_required,
            summary=summary,
            candidate_institutions=candidate_institutions,
        ),
        source_refs,
    )
