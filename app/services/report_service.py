from app.schemas.response import DiagnosisResponse

def generate_markdown_report(response: DiagnosisResponse) -> str:
    """
    Phase 7: 최종 Markdown 보고서 생성 (Rule-based Baseline)
    """
    md = "# 신제품 출시 전 인증 및 리콜 리스크 사전 검토 결과\n\n"
    
    # 1. 입력 제품 요약
    md += "## 1. 입력 제품 요약\n"
    summary = response.input_summary
    for k, v in summary.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                if sub_v:
                    md += f"- **{sub_k}**: {sub_v}\n"
        elif v:
             md += f"- **{k}**: {v}\n"
             
    # 2. 법정 품목명 및 인증 진단
    md += "\n## 2. 법정 품목명 및 인증 진단\n"
    for cand in response.legal_product_candidates:
        md += f"- **매칭된 품목명**: {cand.display_product_name}\n"
        md += f"- **예상 인증유형**: {cand.certification_type}\n"
        
    md += "\n**적용 안전기준**\n"
    for std in response.certification_diagnosis.applied_standards:
        md += f"- {std}\n"
        
    # 3. 기관 안내
    md += "\n## 3. 기관 및 절차 안내\n"
    md += f"- {response.institution_guidance.summary}\n"
    
    # 4. 리콜 위험 및 예방 체크리스트
    md += "\n## 4. 리콜 위험 및 출시 전 예방 체크리스트\n"
    md += f"유사 국내 리콜 사례 건수: {response.recall_reason_summary.recall_count}건\n\n"
    
    md += "**출시 전 예방 체크리스트 (필독)**\n"
    for pt in response.recall_reason_summary.prevention_points:
        md += f"- [ ] {pt}\n"
        
    md += "\n**주요 리콜 사유**\n"
    for reason in response.recall_reason_summary.top_recall_reasons:
        md += f"- {reason}\n"
        
    # 5. KC 인증 정보
    md += "\n## 5. 유사 KC 인증 정보\n"
    md += f"- 유사 인증사례 건수: {response.kc_certification_summary.similar_cert_count}건\n"
    md += f"- 주요 인증기관: {', '.join(response.kc_certification_summary.top_cert_organ_names)}\n"
    md += f"- {response.kc_certification_summary.note}\n"
    
    md += "\n---\n"
    md += f"*{response.disclaimer}*\n"
    
    return md
