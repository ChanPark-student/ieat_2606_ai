from __future__ import annotations

from typing import List

from app.schemas.response import DiagnosisResponse
from app.services.kc_certification_service import has_relevant_representative_model

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

# ── 체크리스트 우선순위용 소재/품목 키워드 그룹 ──────────────────────────────
# 섬유/원단 계열 — 봉제완구·섬유제품의 소재 신호로 사용 (diagnosis_service의
# RAG 질의 정규화에서도 동일 그룹을 재사용한다)
FABRIC_MATERIAL_TOKENS: List[str] = [
    "극세사", "원단", "솜", "실", "봉제", "섬유", "직물", "패브릭", "천",
    "면", "폴리", "폴리에스터", "나일론", "아크릴", "가죽", "스웨이드",
    "플리스", "털", "퍼", "충전재", "안감", "겉감",
]

# 인형/봉제완구 계열 — product_name·user_query에서 품목 신호로 사용
PLUSH_TOY_TOKENS: List[str] = [
    "인형", "곰인형", "봉제인형", "봉제완구", "캐릭터인형",
    "plush", "doll", "stuffed", "teddy", "bear",
]

# 장식/부속품 계열 — 작은 부품 탈락 위험 신호
_ACCESSORY_TOKENS = ["단추", "눈알", "장식", "부속품", "마개"]

# 끈/코드/리본/고리 관련 항목은 입력에 이 신호가 있을 때만 우선 노출
_STRAP_SIGNAL_TOKENS = ["끈", "코드", "리본", "고리", "스트랩", "줄"]

# 발열/전기 신호 — 배터리 신호가 없어도 이 토큰이 있으면 온열 항목 유지
_HEAT_SIGNAL_TOKENS = ["온열", "발열", "히터", "전열", "전기"]


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


def _prioritize_checklist(
    items: List[str],
    input_summary: dict,
    legal_name: str,
) -> List[str]:
    """입력 제품 특성에 따라 체크리스트 항목 우선순위 정렬.

    원본 데이터는 유지하며 표시 순서만 조정(삭제하지 않음).
    점수가 높을수록 앞에, 낮을수록 뒤에 표시된다.
    """
    material = (input_summary.get("material_text") or "").lower()
    power_type = (input_summary.get("power_type") or "").lower()
    battery_included = bool(input_summary.get("battery_included", False))
    product_name = (input_summary.get("product_name") or "").lower()
    user_query = (input_summary.get("user_query") or "").lower()
    text_all = f"{product_name} {user_query}"

    priority_kw: List[str] = []
    depriority_kw: List[str] = []

    has_battery_signal = (
        battery_included
        or any(t in power_type for t in ["건전지", "배터리", "충전", "usb", "전기"])
    )
    if has_battery_signal:
        priority_kw += ["배터리", "전지", "충전", "전원"]

    if "플라스틱" in material or "합성수지" in material or "pvc" in material or "abs" in material:
        priority_kw += ["가소제", "프탈레이트"]

    if "금속" in material or "나사" in material or "철" in material or "알루미늄" in material:
        priority_kw += ["납", "카드뮴", "중금속", "유해원소"]

    if "완구" in legal_name or "장난감" in product_name:
        priority_kw += ["작은 부품", "날카로운", "자석", "기계적", "작동"]

    # 인형/봉제완구 판별: 품목명·문의내용에 인형류 신호 + 소재에 섬유·충전재류 신호가
    # 함께 있을 때만 인정 (섬유 신호만으로 오탐하지 않도록 함께 요구)
    has_plush_signal = any(t in text_all for t in PLUSH_TOY_TOKENS)
    has_fabric_material = any(t in material for t in FABRIC_MATERIAL_TOKENS)
    is_plush_toy = has_plush_signal and has_fabric_material

    if is_plush_toy:
        # 인형/봉제완구 특화 우선순위: 봉제선·충전재 노출, 장식·부속품(작은 부품),
        # 원단·염색·프린팅 유해물질, 폼알데하이드, 아릴아민/아조염료,
        # 코팅·단추 부위 납/카드뮴/가소제, 표시사항류를 상위로 끌어올림
        priority_kw += [
            "봉제", "충전재", "솜", "작은 부품",
            *_ACCESSORY_TOKENS,
            "염색", "프린팅", "유해원소",
            "폼알데하이드",
            "아릴아민", "아조염료",
            "납", "카드뮴",
            "가소제", "프탈레이트",
            "표시사항", "관련 표시", "주의사항", "사용연령", "재질", "제조자", "수입자",
        ]

    # 섬유(원단 포함)/가죽 관련 항목은 실제 섬유 소재(또는 인형/봉제완구)일 때만 유지,
    # 그렇지 않으면 후순위
    is_fabric_product = (
        any(k in legal_name for k in ["섬유", "의류"])
        or has_fabric_material
        or is_plush_toy
    )
    if not is_fabric_product:
        depriority_kw += ["섬유", "가죽", "아릴아민", "아조염료", "폼알데하이드"]

    # 온열/발열: 배터리·발열 신호가 있을 때만 표시 대상으로 유지
    has_heat_signal = any(t in material or t in power_type for t in _HEAT_SIGNAL_TOKENS)
    show_heat_items = has_battery_signal or has_heat_signal
    if show_heat_items:
        priority_kw += _HEAT_SIGNAL_TOKENS if has_heat_signal else []

    # 끈/코드/리본/고리/스트랩/줄: 입력에 실제 신호가 있을 때만 표시 대상으로 유지
    has_strap_signal = any(t in text_all or t in material for t in _STRAP_SIGNAL_TOKENS)
    if has_strap_signal:
        priority_kw += _STRAP_SIGNAL_TOKENS

    priority_kw = list(dict.fromkeys(priority_kw))
    depriority_kw = list(dict.fromkeys(depriority_kw))

    # 하드 필터: 트리거 신호가 없는 온열/끈-코드류 항목은 화면에서 숨김.
    # 폼알데하이드·아릴아민/아조염료 등 섬유 유해물질 항목은 절대 숨기지 않음(요구사항).
    # 전부 제거되어 빈 리스트가 되는 경우에는 안전장치로 원본을 유지한다.
    def _is_heat_item(item_l: str) -> bool:
        return any(t in item_l for t in _HEAT_SIGNAL_TOKENS)

    def _is_strap_item(item_l: str) -> bool:
        return any(t in item_l for t in _STRAP_SIGNAL_TOKENS)

    filtered: List[str] = []
    for item in items:
        item_l = item.lower()
        if _is_heat_item(item_l) and not show_heat_items:
            continue
        if _is_strap_item(item_l) and not has_strap_signal:
            continue
        filtered.append(item)
    if not filtered:
        filtered = items

    def _score(item: str) -> int:
        item_l = item.lower()
        p_score = sum(10 for kw in priority_kw if kw in item_l)
        if p_score > 0:
            return p_score  # priority 신호 있으면 depriority 패널티 무시
        return sum(-5 for kw in depriority_kw if kw in item_l)

    return sorted(filtered, key=lambda x: -_score(x))


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
        label = label_map.get(k, k)
        if k == "battery_included":
            # 배터리 포함 여부는 False("아니오")도 의미 있는 정보이므로 항상 표시
            md += f"- **{label}**: {'예' if v else '아니오'}\n"
            continue
        if v is None or v == "" or v == [] or v is False:
            continue
        display_v = "예" if v is True else v
        md += f"- **{label}**: {display_v}\n"
    md += "\n"

    # ── 2. 법정 품목명 후보 ───────────────────────────────────────────────
    md += "## 2. 법정 품목명 후보\n\n"
    if not cands:
        md += (
            "> 법정 품목명 후보를 찾지 못했습니다. "
            "제품의 사용연령, 용도, 소재를 추가로 입력하면 더 정확한 진단이 가능합니다.\n\n"
        )
    else:
        _has_confirmed_cand = any(c.confidence_level == "CONFIRMED" for c in cands)
        # CONFIRMED가 있으면 매우 낮은 NEEDS_CONFIRMATION 후보는 보고서에서 생략
        _HIDE_THRESHOLD = 0.10
        if _has_confirmed_cand:
            visible = [
                c for c in cands
                if not (c.confidence_level == "NEEDS_CONFIRMATION"
                        and c.confidence_score < _HIDE_THRESHOLD)
            ]
            hidden_count = len(cands) - len(visible)
        else:
            visible = cands
            hidden_count = 0

        for i, cand in enumerate(visible):
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
                md += (
                    f"- **{cand.display_product_name}** (`{cand.legal_product_name}`) "
                    f"— `{level}` {cand.confidence_score:.2f}\n"
                )
        if hidden_count > 0:
            md += f"- *기타 낮은 신뢰도 후보 {hidden_count}건은 내부 검토용으로 생략됩니다.*\n"
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
    elif recall.supplemental_cases:
        md += (
            "해당 품목군의 직접 매칭 리콜 사례는 없으나, "
            "입력 내용과 유사한 리콜 사례를 참고용으로 제공합니다.\n\n"
        )
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

    if recall.supplemental_cases:
        md += "**보조 검색으로 확인된 유사 리콜 사례 (참고용)**\n\n"
        for case in recall.supplemental_cases:
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

    # ── 6. KC 동일 품목군 인증사례 참고 ────────────────────────────────────
    md += "## 6. KC 동일 품목군 인증사례 참고\n\n"

    if kc.similar_cert_count > 0:
        category_label = kc.matched_category or top_legal_name
        md += (
            f"「{category_label}」 품목군 KC 인증 데이터에서 "
            f"**{kc.similar_cert_count:,}건**의 인증사례를 확인했습니다.\n\n"
        )
        md += (
            "> **주의**: 인증사례는 최종 인증 가능성을 의미하지 않습니다. "
            "실제 인증 여부는 제품 상세 스펙과 시험 결과에 따라 달라질 수 있습니다.\n\n"
        )

        if kc.top_cert_organ_names:
            md += f"**주요 인증기관**: {', '.join(kc.top_cert_organ_names)}\n\n"

        if kc.representative_models:
            # 입력 제품 키워드(원문 단어 + 품목 키워드 그룹)와 실제로 관련 있는
            # 모델이 있는지 확인 — kc_certification_service의 정렬 기준과 동일 로직 사용
            _input_text = " ".join(filter(None, [
                str(response.input_summary.get("product_name") or ""),
                str(response.input_summary.get("user_query") or ""),
                str(response.input_summary.get("material_text") or ""),
            ]))
            _has_match = has_relevant_representative_model(_input_text, kc.representative_models)
            if _has_match:
                md += (
                    "**동일 품목군 대표 인증사례 (참고용, 입력 제품명과 유사한 키워드가 "
                    "포함된 사례 우선 표시)**\n\n"
                )
            else:
                md += "**동일 품목군 내 대표 인증사례 (참고용)**\n\n"
                md += (
                    f"> 입력 제품과 직접 연관된 KC 인증 모델이 없을 수 있습니다. "
                    f"「{category_label}」 품목군 전체 기준 사례를 표시합니다.\n\n"
                )
            for model in kc.representative_models:
                md += f"- {model}\n"
            md += "\n"
    else:
        # 0건이거나 매칭 없음: note만 표시
        md += f"> {kc.note}\n\n"

    # ── 7. 출시 전 확인 체크리스트 ───────────────────────────────────────
    md += "## 7. 출시 전 확인 체크리스트\n\n"

    if checklist:
        # 예방 포인트와 중복 제거 → 입력 제품 특성으로 우선순위 정렬
        deduped = _dedup_checklist(checklist, recall.prevention_points)
        base_items = deduped if deduped else checklist
        items_to_show = _prioritize_checklist(
            base_items, response.input_summary, top_legal_name
        )
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
    # RAG 근거 chunk가 사용된 경우에만 근거 출처를 명시 (내부 ID는 노출하지 않음)
    if response.used_rag_chunk_ids:
        md += (
            "> 본 결과는 기준 데이터, 국내 리콜·KC 공공데이터, 그리고 관련 안전기준·"
            "인증 근거 자료(RAG 검색)를 바탕으로 한 사전 진단 참고자료이며, "
            "최종 법적 판단은 관계기관 또는 전문가 확인이 필요합니다.\n"
        )
    else:
        md += (
            "> 본 결과는 공공데이터와 기준 데이터 기반의 사전 진단 참고자료이며, "
            "최종 법적 판단은 관계기관 또는 전문가 확인이 필요합니다.\n"
        )
    md += "\n---\n"
    md += f"*{response.disclaimer}*\n"

    return md
