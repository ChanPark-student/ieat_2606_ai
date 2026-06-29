from app.schemas.response import DiagnosisResponse


def generate_markdown_report(response: DiagnosisResponse) -> str:
    """Phase 7: 최종 Markdown 보고서 생성 (Rule-based Baseline).

    핸드오프 §10.2 기준 섹션 구조:
    1. 입력 제품 요약
    2. 법정 품목명 후보
    3. 예상 인증유형 및 적용 안전기준
    4. 기관 및 절차 안내
    5. 국내 리콜 사유 요약
    6. KC 유사 인증사례 참고
    7. 출시 전 확인 체크리스트
    8. 최종 확인 필요사항
    9. 안내 문구
    """
    md = "# 신제품 출시 전 인증 및 리콜 리스크 사전 검토 결과\n\n"

    # ── 1. 입력 제품 요약 ──────────────────────────────────────────────────
    md += "## 1. 입력 제품 요약\n\n"
    summary = response.input_summary
    label_map = {
        "product_name": "제품명",
        "user_query": "문의 내용",
        "target_age": "대상 연령",
        "material_text": "소재",
        "power_type": "전원",
        "battery_included": "배터리 포함",
        "import_or_manufacture": "수입/제조",
    }
    for k, v in summary.items():
        if v is None or v == "" or v == []:
            continue
        label = label_map.get(k, k)
        md += f"- **{label}**: {v}\n"
    md += "\n"

    # ── 2. 법정 품목명 후보 ───────────────────────────────────────────────
    md += "## 2. 법정 품목명 후보\n\n"
    if response.legal_product_candidates:
        for cand in response.legal_product_candidates:
            confirm_flag = " *(사용자 확인 필요)*" if cand.needs_user_confirmation else ""
            md += f"- **{cand.display_product_name}** ({cand.legal_product_name}){confirm_flag}\n"
            md += f"  - 인증유형 후보: `{cand.certification_type or '확인 전'}`\n"
            md += f"  - 신뢰도: `{cand.confidence_level}` (score: {cand.confidence_score:.2f})\n"
            md += f"  - 매칭 근거: {cand.match_basis}\n"
    else:
        md += "- 법정 품목명 후보를 찾지 못했습니다. 제품의 사용연령, 용도, 소재를 추가로 제공해 주세요.\n"
    md += "\n"

    # ── 3. 예상 인증유형 및 적용 안전기준 ────────────────────────────────
    md += "## 3. 예상 인증유형 및 적용 안전기준\n\n"
    cert = response.certification_diagnosis
    md += f"- **예상 인증유형**: `{cert.certification_type}`\n"
    md += f"- **판단 수준**: `{cert.judgement_level}`\n"
    if cert.applied_standards:
        md += "\n**적용 안전기준**\n\n"
        for std in cert.applied_standards:
            md += f"- {std}\n"
    else:
        md += "\n적용 안전기준 정보가 없습니다. 법정 품목명 확인 후 재조회 해주세요.\n"
    if cert.source_refs:
        md += "\n**근거 자료**\n\n"
        for ref in cert.source_refs[:5]:
            md += f"- `{ref}`\n"
    md += "\n"

    # ── 4. 기관 및 절차 안내 ──────────────────────────────────────────────
    md += "## 4. 기관 및 절차 안내\n\n"
    inst = response.institution_guidance
    required_label = "필요" if inst.institution_required else "불필요 (공급자 자체 확인)"
    md += f"- **지정기관 필요 여부**: {required_label}\n"
    md += f"- {inst.summary}\n"
    if inst.candidate_institutions:
        md += "\n**후보 기관 목록**\n\n"
        for org in inst.candidate_institutions:
            name_str = f"{org.institution_name}"
            if org.short_name:
                name_str += f" ({org.short_name})"
            role_str = f" — {org.institution_role}" if org.institution_role else ""
            url_str = f" / {org.website_url}" if org.website_url else ""
            md += f"- **{name_str}**{role_str}{url_str}\n"
    md += "\n"

    # ── 5. 국내 리콜 사유 요약 ────────────────────────────────────────────
    md += "## 5. 국내 리콜 사유 요약\n\n"
    recall = response.recall_reason_summary

    # 법정 품목군 이름을 헤더에 표시 (CONFIRMED/CANDIDATE 후보가 있을 때)
    top_cand = next(
        (c for c in response.legal_product_candidates
         if c.confidence_level in ("CONFIRMED", "CANDIDATE")),
        None,
    )
    if recall.recall_count > 0 and top_cand:
        md += (
            f"- **「{top_cand.legal_product_name}」 계열 동일 법정 품목군 기준 "
            f"유사 리콜 사례**: **{recall.recall_count}건**\n"
        )
    else:
        md += f"- 유사 국내 리콜 사례 건수: **{recall.recall_count}건**\n"

    if recall.top_recall_reasons:
        md += "\n**주요 리콜 사유**\n\n"
        for reason in recall.top_recall_reasons:
            md += f"- {reason}\n"

    if recall.representative_cases:
        md += "\n**대표 리콜 사례**\n\n"
        for case in recall.representative_cases:
            md += f"- {case}\n"

    if recall.prevention_points:
        md += "\n**리콜 사유 기반 예방 포인트** *(리콜 데이터 기반, 출시 전 우선 확인)*\n\n"
        for pt in recall.prevention_points:
            md += f"- [ ] {pt}\n"

    if not recall.top_recall_reasons and not recall.representative_cases:
        md += "\n유사 리콜 사례 정보가 없습니다.\n"
    md += "\n"

    # ── 6. KC 유사 인증사례 참고 ──────────────────────────────────────────
    md += "## 6. KC 유사 인증사례 참고\n\n"
    kc = response.kc_certification_summary

    top_cand_for_kc = next(
        (c for c in response.legal_product_candidates
         if c.confidence_level in ("CONFIRMED", "CANDIDATE")),
        None,
    )
    if kc.similar_cert_count > 0 and top_cand_for_kc:
        md += (
            f"- **「{top_cand_for_kc.legal_product_name}」 계열 유사 KC 인증사례**: "
            f"**{kc.similar_cert_count:,}건**\n"
        )
    else:
        md += f"- 유사 KC 인증사례 건수: **{kc.similar_cert_count}건**\n"

    if kc.top_cert_organ_names:
        md += "\n**주요 인증기관**\n\n"
        for organ in kc.top_cert_organ_names:
            md += f"- {organ}\n"

    if kc.representative_models:
        md += "\n**대표 인증 사례**\n\n"
        for model in kc.representative_models:
            md += f"- {model}\n"

    md += f"\n> {kc.note}\n\n"

    # ── 7. 출시 전 확인 체크리스트 ───────────────────────────────────────
    md += "## 7. 출시 전 확인 체크리스트\n\n"
    checklist = response.launch_checklist
    if checklist:
        for item in checklist:
            md += f"- [ ] {item}\n"
    else:
        md += "- [ ] 법정 품목명 최종 확인\n"
        md += "- [ ] 적용 안전기준 및 표시사항 확인\n"
        md += "- [ ] 관련 시험성적서 확보 여부 확인\n"
    md += "\n"

    # ── 8. 최종 확인 필요사항 ─────────────────────────────────────────────
    md += "## 8. 최종 확인 필요사항\n\n"
    if response.legal_product_candidates:
        # 확인 필요 후보가 있는 경우
        confirm_needed = [c for c in response.legal_product_candidates if c.needs_user_confirmation]
        if confirm_needed:
            md += "아래 법정 품목명 후보는 사용자 최종 확인이 필요합니다.\n\n"
            for c in confirm_needed:
                md += f"- **{c.display_product_name}**: 실제 사용연령, 소재, 기능, 판매 문구에 따라 품목이 달라질 수 있습니다.\n"
    else:
        md += "- 안정적인 법정 품목명 후보를 찾지 못했습니다. 사용연령, 소재, 용도 등을 추가로 제공해 주세요.\n"
    md += "\n"

    # ── 9. 안내 문구 ──────────────────────────────────────────────────────
    md += "## 9. 안내 문구\n\n"
    md += f"> {response.disclaimer}\n"
    md += "\n---\n"
    md += "*본 결과는 공공데이터 기반 사전 검토용 안내이며, KC 인증정보는 유사 인증사례 확인용 보조 근거입니다. 실제 인증 가능 여부나 접수 가능 기관은 현재 지정기관 업무범위와 관계 기관 확인이 필요합니다.*\n"

    return md
