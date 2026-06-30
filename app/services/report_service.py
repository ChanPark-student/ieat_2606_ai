from __future__ import annotations

from typing import List

from app.schemas.response import DiagnosisResponse

# 인증유형별 한 줄 설명 (일반 제도 지식 기반 — 특정 제품 데이터 아님)
_CERT_TYPE_DESC = {
    "안전확인": (
        "지정시험기관에서 안전성 시험을 받은 후 신고하고 KC 마크를 부착해야 합니다."
    ),
    "안전인증": (
        "지정인증기관으로부터 인증을 취득한 후 KC 마크를 부착해야 합니다. "
        "사후관리(정기검사) 의무가 있습니다."
    ),
    "공급자적합성확인": (
        "사업자가 자체적으로 안전기준 적합성을 확인하는 방식입니다. "
        "지정기관 신고 없이 KC 마크를 부착할 수 있으나, "
        "시험성적서 및 적합성 입증자료를 5년간 보관해야 합니다."
    ),
    "확인 전": (
        "법정 품목명이 확정되지 않아 인증유형을 결정하기 어렵습니다. "
        "제품 세부정보를 보완하여 재진단하거나, 관계기관에 직접 확인하세요."
    ),
}

_CONFIDENCE_DESC = {
    "CONFIRMED": "가장 유력한 법정 품목명 후보입니다.",
    "CANDIDATE": "유력 후보이나 제품 세부정보(소재·연령·기능)에 따라 추가 확인이 필요합니다.",
    "NEEDS_CONFIRMATION": "입력 정보만으로는 품목군을 확정하기 어렵습니다.",
}


def _dedup_checklist(checklist: List[str], prevention_points: List[str]) -> List[str]:
    """체크리스트에서 예방 포인트와 동일하거나 포함 관계인 항목을 제거해 중복 방지."""
    pp_lower = [p.lower() for p in prevention_points]
    result = []
    for item in checklist:
        item_lower = item.lower()
        # 예방 포인트와 50자 이상 겹치거나 substring이면 제외
        is_dup = any(
            item_lower in p or p in item_lower or
            _overlap_ratio(item_lower, p) >= 0.6
            for p in pp_lower
        )
        if not is_dup:
            result.append(item)
    return result


def _overlap_ratio(a: str, b: str) -> float:
    """두 문자열의 공통 어절 비율."""
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / min(len(words_a), len(words_b))


def generate_markdown_report(response: DiagnosisResponse) -> str:
    """Phase 7: 최종 Markdown 보고서 생성 (Rule-based Baseline).

    섹션 구조:
    1. 입력 제품 요약
    2. 법정 품목명 후보
    3. 예상 인증유형 및 적용 안전기준
    4. 시험기관 및 절차 안내
    5. 국내 리콜 사유 요약
    6. KC 유사 인증사례 참고
    7. 출시 전 확인 체크리스트
    8. 추가 확인 필요사항
    9. 안내 문구
    """
    cands = response.legal_product_candidates
    cert = response.certification_diagnosis
    inst = response.institution_guidance
    recall = response.recall_reason_summary
    kc = response.kc_certification_summary
    checklist = response.launch_checklist

    # 최상위 CONFIRMED/CANDIDATE 후보
    top_cand = next(
        (c for c in cands if c.confidence_level in ("CONFIRMED", "CANDIDATE")), None
    )
    top_legal_name = top_cand.legal_product_name if top_cand else ""
    cert_type = (cert.certification_type or "확인 전").strip()

    md = "# 신제품 출시 전 인증 및 리콜 리스크 사전 검토 결과\n\n"

    # ── 1. 입력 제품 요약 ──────────────────────────────────────────────────
    md += "## 1. 입력 제품 요약\n\n"
    label_map = {
        "product_name": "제품명",
        "user_query": "문의 내용",
        "target_age": "대상 연령",
        "material_text": "소재",
        "power_type": "전원",
        "battery_included": "배터리 포함",
        "import_or_manufacture": "수입/제조",
    }
    for k, v in response.input_summary.items():
        if v is None or v == "" or v == [] or v is False:
            continue
        label = label_map.get(k, k)
        md += f"- **{label}**: {v}\n"
    md += "\n"

    # ── 2. 법정 품목명 후보 ───────────────────────────────────────────────
    md += "## 2. 법정 품목명 후보\n\n"
    if not cands:
        md += (
            "> 법정 품목명 후보를 찾지 못했습니다. "
            "제품의 사용연령, 용도, 소재를 추가로 입력하면 더 정확한 진단이 가능합니다.\n\n"
        )
    else:
        # 상위 1개는 상세 표시, 나머지는 간략 표시
        for i, cand in enumerate(cands):
            level = cand.confidence_level
            level_desc = _CONFIDENCE_DESC.get(level, "")
            if i == 0:
                md += f"### {cand.display_product_name} (`{cand.legal_product_name}`)\n\n"
                md += f"- **신뢰도**: `{level}` (score: {cand.confidence_score:.2f}) — {level_desc}\n"
                md += f"- **인증유형 후보**: `{cand.certification_type or '확인 전'}`\n"
                md += f"- **매칭 근거**: {cand.match_basis}\n"
                if cand.needs_user_confirmation:
                    md += (
                        "- ⚠️ **사용자 확인 필요**: 실제 사용연령·소재·기능·판매 문구에 따라 "
                        "품목이 달라질 수 있습니다.\n"
                    )
                md += "\n"
            else:
                # 하위 후보는 간략 1줄
                md += (
                    f"- **{cand.display_product_name}** (`{cand.legal_product_name}`) "
                    f"— `{level}` {cand.confidence_score:.2f}\n"
                )
        md += "\n"

    # ── 3. 예상 인증유형 및 적용 안전기준 ────────────────────────────────
    md += "## 3. 예상 인증유형 및 적용 안전기준\n\n"

    cert_desc = _CERT_TYPE_DESC.get(cert_type, "")
    if cert_type == "확인 전":
        md += f"> **인증유형 미확정**: {cert_desc}\n\n"
    else:
        md += f"- **예상 인증유형**: `{cert_type}`\n"
        if cert_desc:
            md += f"  - {cert_desc}\n"
        md += f"- **판단 수준**: `{cert.judgement_level}`\n\n"

    if cert.applied_standards:
        md += "**적용 안전기준**\n\n"
        for std in cert.applied_standards:
            md += f"- {std}\n"
        md += "\n"
    elif cert_type != "확인 전":
        md += "> 적용 안전기준 정보가 없습니다. 법정 품목명 확인 후 재조회하세요.\n\n"

    # ── 4. 시험기관 및 절차 안내 ──────────────────────────────────────────
    md += "## 4. 시험기관 및 절차 안내\n\n"

    if not inst.institution_required:
        if cert_type == "공급자적합성확인":
            md += (
                "- **지정기관 신고 불필요** (공급자적합성확인 대상)\n"
                "- 지정기관 신고 없이 출시 가능하나, 사업자가 직접 안전기준 적합성을 입증해야 합니다.\n"
                "  시험성적서 및 적합성 입증자료를 준비하고, 5년간 보관 의무가 있습니다.\n"
            )
        else:
            md += f"- **지정기관 필요 여부**: 불필요 (공급자 자체 확인)\n"
            md += f"- {inst.summary}\n"
    else:
        md += f"- **지정기관 신고/인증 필요**\n"
        md += f"- {inst.summary}\n"

    if inst.candidate_institutions:
        md += "\n**후보 기관 목록**\n\n"
        for org in inst.candidate_institutions:
            name_str = org.institution_name
            if org.short_name:
                name_str += f" ({org.short_name})"
            role_str = f" — {org.institution_role}" if org.institution_role else ""
            url_str = f" / {org.website_url}" if org.website_url else ""
            md += f"- **{name_str}**{role_str}{url_str}\n"
    md += "\n"

    # ── 5. 국내 리콜 사유 요약 ────────────────────────────────────────────
    md += "## 5. 국내 리콜 사유 요약\n\n"

    if recall.recall_count > 0 and top_legal_name:
        md += (
            f"「{top_legal_name}」 품목군에서 수집된 국내 유사 리콜 사례는 "
            f"**{recall.recall_count}건**입니다. "
            "아래 주요 사유는 출시 전 반드시 점검해야 할 위험 포인트입니다.\n\n"
        )
    elif recall.recall_count > 0:
        md += f"유사 국내 리콜 사례: **{recall.recall_count}건**\n\n"
    else:
        md += "해당 품목군의 국내 리콜 사례 정보가 없습니다.\n\n"

    if recall.top_recall_reasons:
        md += "**주요 리콜 사유**\n\n"
        for reason in recall.top_recall_reasons:
            md += f"- {reason}\n"
        md += "\n"

    if recall.representative_cases:
        md += "**대표 리콜 사례**\n\n"
        for case in recall.representative_cases:
            md += f"- {case}\n"
        md += "\n"

    if recall.prevention_points:
        md += (
            "**리콜 데이터 기반 출시 전 위험 포인트**\n\n"
            "> 과거 리콜 사유에서 도출된 항목입니다. 출시 전 우선 확인하세요.\n\n"
        )
        for pt in recall.prevention_points:
            md += f"- [ ] {pt}\n"
        md += "\n"

    # ── 6. KC 유사 인증사례 참고 ──────────────────────────────────────────
    md += "## 6. KC 유사 인증사례 참고\n\n"

    if kc.similar_cert_count > 0 and top_legal_name:
        md += (
            f"「{top_legal_name}」 품목군 관련 KC 인증 데이터에서 "
            f"**{kc.similar_cert_count:,}건**의 유사 인증사례를 확인했습니다.\n\n"
        )
        md += (
            "> **주의**: 유사 인증사례는 최종 인증 가능성을 의미하지 않습니다. "
            "실제 인증 여부는 제품 상세 스펙과 시험 결과에 따라 달라질 수 있습니다.\n\n"
        )

        if kc.top_cert_organ_names:
            md += f"**주요 인증기관**: {', '.join(kc.top_cert_organ_names)}\n\n"

        if kc.representative_models:
            md += "**대표 인증 사례 (참고용)**\n\n"
            for model in kc.representative_models:
                md += f"- {model}\n"
            md += "\n"
    else:
        # 0건이거나 매칭 없음: note만 표시
        md += f"> {kc.note}\n\n"

    # ── 7. 출시 전 확인 체크리스트 ───────────────────────────────────────
    md += "## 7. 출시 전 확인 체크리스트\n\n"

    if checklist:
        # 예방 포인트와 중복 제거
        deduped = _dedup_checklist(checklist, recall.prevention_points)
        items_to_show = deduped if deduped else checklist
        for item in items_to_show:
            md += f"- [ ] {item}\n"
    else:
        # NEEDS_CONFIRMATION 등 checklist가 빈 경우 기본 안내
        md += "- [ ] 법정 품목명 최종 확인\n"
        md += "- [ ] 적용 안전기준 및 표시사항 확인\n"
        md += "- [ ] 관련 시험성적서 확보 여부 확인\n"
    md += "\n"

    # ── 8. 추가 확인 필요사항 ─────────────────────────────────────────────
    md += "## 8. 추가 확인 필요사항\n\n"

    has_confirmed = any(c.confidence_level == "CONFIRMED" for c in cands)
    has_candidate = any(c.confidence_level == "CANDIDATE" for c in cands)
    # CONFIRMED가 있을 때는 CANDIDATE 수준 confirm 항목만 표시 (노이즈 방지)
    if has_confirmed:
        confirm_needed = [c for c in cands
                          if c.needs_user_confirmation and c.confidence_level == "CANDIDATE"]
    else:
        confirm_needed = [c for c in cands if c.needs_user_confirmation]

    if not cands:
        md += (
            "- 법정 품목명 후보를 찾지 못했습니다. "
            "사용연령·소재·용도·판매 문구 등 추가 정보를 입력하여 재진단하세요.\n"
        )
    elif not has_confirmed and not has_candidate:
        # 전부 NEEDS_CONFIRMATION
        md += (
            "입력 정보만으로는 법정 품목명을 확정하기 어렵습니다. "
            "아래 정보를 보완하여 재진단하거나 관계기관에 직접 문의하세요.\n\n"
        )
        md += "- 제품의 구체적인 사용연령 및 대상 (예: 만 3세 이상, 영아용 등)\n"
        md += "- 주요 소재 및 부품 구성\n"
        md += "- 제품의 핵심 기능 또는 사용 목적\n"
        md += "- 판매 예정 채널 및 표기 예정 명칭\n"
    else:
        if confirm_needed:
            md += "아래 후보 품목은 제품 세부정보에 따라 달라질 수 있으므로 최종 확인이 필요합니다.\n\n"
            for c in confirm_needed:
                md += (
                    f"- **{c.display_product_name}** (`{c.legal_product_name}`): "
                    "실제 사용연령·소재·기능·판매 문구에 따라 품목이 달라질 수 있습니다.\n"
                )
        elif has_candidate and not has_confirmed:
            md += (
                f"최상위 후보(`{top_legal_name}`)가 `CANDIDATE` 수준으로, "
                "제품 스펙을 확인하거나 추가 정보를 제공하여 재진단을 권장합니다.\n"
            )
        else:
            md += "법정 품목명 및 인증유형이 확정되었습니다. 위 체크리스트 항목을 순차적으로 이행하세요.\n"
    md += "\n"

    # ── 9. 안내 문구 ──────────────────────────────────────────────────────
    md += "## 9. 안내 문구\n\n"
    md += (
        "> 본 결과는 공공데이터와 기준 데이터 기반의 사전 진단 참고자료이며, "
        "최종 법적 판단은 관계기관 또는 전문가 확인이 필요합니다.\n"
    )
    md += "\n---\n"
    md += f"*{response.disclaimer}*\n"

    return md
