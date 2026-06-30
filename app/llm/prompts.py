"""LLM 보고서 정제용 프롬프트."""

SYSTEM_PROMPT = """\
당신은 제품안전 인증 및 리콜 리스크 사전 검토 보조 AI입니다.
아래 지침을 반드시 준수하세요.

[필수 준수 사항]
1. 제공된 데이터 밖의 새로운 사실을 추가하지 마세요.
   - 새로운 인증유형, 법정 품목명, 리콜 사유, KC 인증사례, 기관명을 만들지 마세요.
2. 인증 가능 여부를 단정하지 마세요.
   - 금지 표현: "반드시 인증됩니다", "인증이 가능합니다", "확실히 인증", "인증이 보장"
3. 리콜 발생 가능성을 단정하지 마세요.
   - 금지 표현: "리콜됩니다", "리콜이 발생합니다"
4. "문제 없습니다", "100% 안전합니다", "보장됩니다" 같은 표현은 사용하지 마세요.
5. KC 유사 인증사례는 반드시 '참고자료'임을 명시하세요.
6. 최종 법적 판단은 '관계기관 또는 전문가 확인이 필요합니다'라는 표현을 유지하세요.
7. CONFIRMED/CANDIDATE/NEEDS_CONFIRMATION 의미와 수준 표현을 바꾸지 마세요.
8. Markdown 섹션 구조(##, ###, - [ ], blockquote >)를 그대로 유지하세요.
9. 수치·품목명·기관명·인증번호·날짜 등 모든 데이터 값을 변경하지 마세요.\
"""

USER_PROMPT_TEMPLATE = """\
아래는 제품안전 사전 진단 보고서 초안입니다.
문장을 더 자연스럽고 읽기 쉽게 다듬어 주세요.
데이터 값, 섹션 구조, 체크리스트 항목은 변경하지 마세요.
{retrieved_context}
---
{template_report}
---

수정된 보고서 전문만 출력하세요. 추가 설명·주석 없이 보고서만 출력하세요.\
"""


def build_retrieved_context(retrieved_chunks) -> str:
    """검색된 근거 chunk를 프롬프트용 참고 블록으로 구성.

    LLM은 이 chunk들을 '근거 확인용'으로만 참고하며, 여기 없는 기준/사례를
    새로 만들면 안 된다. chunk가 없으면 빈 문자열 반환.
    """
    if not retrieved_chunks:
        return ""
    lines = [
        "",
        "[참고 근거 chunk — 아래 내용 범위 안에서만 사실을 확인하세요. "
        "여기 없는 기준·사례·기관·인증유형을 새로 만들지 마세요.]",
    ]
    for c in retrieved_chunks:
        title = c.get("title") or c.get("document_type") or ""
        text = (c.get("chunk_text") or "").replace("\n", " ").strip()
        if len(text) > 300:
            text = text[:300] + "…"
        lines.append(f"- ({c.get('document_type','')}) {title}: {text}")
    lines.append("")
    return "\n".join(lines)
