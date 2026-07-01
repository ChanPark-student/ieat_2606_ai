#!/usr/bin/env python3
"""
scripts/generate_llm_reports.py

오프라인 LLM 보고서 생성기.
docs/demo_outputs/*.md의 final_report_markdown(§8)을 읽어
LLM으로 7섹션 형식 보고서를 생성하고 docs/llm_outputs/에 저장합니다.

사용법:
  python scripts/generate_llm_reports.py           # 3개 케이스 전체
  python scripts/generate_llm_reports.py --cases 2 # 장난감 자동차만
  python scripts/generate_llm_reports.py --cases 1 3

요구사항:
  pip install "transformers>=4.45.0,<5.0.0" accelerate torch
  .env에 HF_MODEL_NAME 설정 (기본: Qwen/Qwen2.5-1.5B-Instruct)

주의:
  /diagnose API 동작은 이 스크립트와 독립적입니다 — API 기본값(ENABLE_LLM=false)은 변경되지 않습니다.
  이 스크립트는 오프라인 배치 참고자료 생성 전용입니다.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 프로젝트 루트를 sys.path에 추가 ──────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
os.chdir(str(_PROJECT_ROOT))

# ── .env 로드 (python-dotenv) ─────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass  # dotenv 없어도 환경변수 직접 설정으로 동작

# ── settings 읽기 (HF_MODEL_NAME, HF_TOKEN) ───────────────────────────────────
try:
    from app.core.config import settings
    MODEL_NAME: str = settings.HF_MODEL_NAME
    HF_TOKEN: str | None = settings.HF_TOKEN or None
    HF_CACHE: str = str(_PROJECT_ROOT / "hf_cache")
except Exception as _e:
    print(f"[warn] app.core.config 로드 실패 ({_e}), 환경변수에서 직접 읽습니다")
    MODEL_NAME = os.environ.get("HF_MODEL_NAME", "Qwen/Qwen2.5-1.5B-Instruct")
    HF_TOKEN = os.environ.get("HF_TOKEN") or None
    HF_CACHE = str(_PROJECT_ROOT / "hf_cache")

# ── 금지 표현 목록 ────────────────────────────────────────────────────────────
_FORBIDDEN: list[str] = [
    "반드시 인증됩니다",
    "인증이 보장됩니다",
    "보장됩니다",
    "100% 안전합니다",
    "리콜되지 않습니다",
    "문제 없습니다",
    "확정됩니다",
    "인증됩니다",
]

# ── 7섹션 헤더 패턴 ───────────────────────────────────────────────────────────
_SECTION_PATTERNS: list[str] = [
    r"## 1\.",
    r"## 2\.",
    r"## 3\.",
    r"## 4\.",
    r"## 5\.",
    r"## 6\.",
    r"## 7\.",
]

# ── 케이스 정의 ──────────────────────────────────────────────────────────────
CASES: list[dict] = [
    {
        "case_no": 1,
        "demo_file": "docs/demo_outputs/01_bag_demo.md",
        "out_file": "docs/llm_outputs/01_bag_llm_report.md",
        "title": "어린이용 책가방",
    },
    {
        "case_no": 2,
        "demo_file": "docs/demo_outputs/02_toy_car_demo.md",
        "out_file": "docs/llm_outputs/02_toy_car_llm_report.md",
        "title": "장난감 자동차",
    },
    {
        "case_no": 3,
        "demo_file": "docs/demo_outputs/03_unknown_demo.md",
        "out_file": "docs/llm_outputs/03_unknown_llm_report.md",
        "title": "정체불명 어린이용 물건",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 유틸 함수
# ─────────────────────────────────────────────────────────────────────────────

def extract_input_json(demo_path: Path) -> dict:
    """demo_output Markdown의 §1 입력 JSON 블록 추출. 실패 시 빈 dict."""
    import json as _json
    text = demo_path.read_text(encoding="utf-8")
    m = re.search(r"## 1\. 입력 JSON\s*\n+```json\s*(.*?)\s*```", text, re.DOTALL)
    if not m:
        return {}
    try:
        return _json.loads(m.group(1))
    except Exception:
        return {}


def extract_legal_name(template_text: str) -> str:
    """final_report_markdown §2에서 최상위 법정 품목명(백틱 안 텍스트) 추출."""
    m = re.search(r"###\s+.+\(`(.+?)`\)", template_text)
    return m.group(1) if m else ""


# 소재가 섬유/가죽 계열이 아니면 제외할 항목 키워드 (report_service.py의 depriority_kw와 동일 원칙)
_FABRIC_ONLY_KEYWORDS: list[str] = ["섬유", "가죽", "아릴아민", "아조염료", "폼알데하이드"]
_FABRIC_MATERIAL_TOKENS: list[str] = ["섬유", "면", "폴리에스터", "폴리", "나일론", "가죽", "원단", "모직"]

# 발열/온열 기능이 아니면 제외할 항목 키워드
_HEAT_ONLY_KEYWORDS: list[str] = ["온열"]
_HEAT_SIGNAL_TOKENS: list[str] = ["온열", "발열", "히터", "전열"]

# 소재 신호 → 우선 노출 키워드 (해당 소재 토큰이 있으면 매칭 항목을 상위로)
_MATERIAL_PRIORITY_RULES: list[tuple[list[str], list[str]]] = [
    (["플라스틱", "합성수지", "pvc", "abs"], ["가소제", "프탈레이트"]),
    (["금속", "나사", "철", "알루미늄"], ["납", "카드뮴", "중금속", "유해원소"]),
]
_BATTERY_TOKENS: list[str] = ["건전지", "배터리", "충전"]
_BATTERY_PRIORITY_KEYWORDS: list[str] = ["배터리", "전지", "충전", "전원"]
_TOY_PRIORITY_KEYWORDS: list[str] = ["작은 부품", "날카로운", "자석", "작동"]


def filter_relevant_checklist(
    items: list[str], material_text: str, power_type: str, legal_name: str,
) -> list[str]:
    """소재·전원과 무관한 항목을 하드 제외 (LLM이 아니라 코드가 결정).

    LLM은 판단자가 아니므로, '관련 없는 항목을 빼라'는 조건부 지시를 LLM에게
    맡기지 않고 여기서 미리 제거한다 — 약한 모델이 원본 체크리스트를 그대로
    복사하는 실패 모드를 원천적으로 막는다.
    """
    material = (material_text or "").lower()
    power = (power_type or "").lower()
    legal = legal_name or ""

    is_fabric_relevant = (
        any(tok in material for tok in _FABRIC_MATERIAL_TOKENS)
        or "섬유" in legal or "의류" in legal
    )
    is_heat_relevant = any(tok in material or tok in power for tok in _HEAT_SIGNAL_TOKENS)

    result = []
    for item in items:
        if not is_fabric_relevant and any(kw in item for kw in _FABRIC_ONLY_KEYWORDS):
            continue
        if not is_heat_relevant and any(kw in item for kw in _HEAT_ONLY_KEYWORDS):
            continue
        result.append(item)
    return result


def rank_checklist_items(
    items: list[str], material_text: str, power_type: str,
    battery_included: bool, product_name: str, legal_name: str,
) -> list[str]:
    """소재·전원·품목 신호 기준 우선순위 정렬 (report_service._prioritize_checklist와 동일 원칙).

    필터링된 목록을 관련도 높은 순으로 정렬해두면, 약한 모델도 "상위 N개"만
    선택하면 되므로 조건부 판단 부담이 줄어든다.
    """
    material = (material_text or "").lower()
    power = (power_type or "").lower()
    name = (product_name or "").lower()

    priority_kw: list[str] = []
    if battery_included or any(t in power for t in _BATTERY_TOKENS):
        priority_kw += _BATTERY_PRIORITY_KEYWORDS
    for material_tokens, item_keywords in _MATERIAL_PRIORITY_RULES:
        if any(t in material for t in material_tokens):
            priority_kw += item_keywords
    if "완구" in legal_name or "장난감" in name:
        priority_kw += _TOY_PRIORITY_KEYWORDS

    def _score(item: str) -> int:
        return sum(10 for kw in priority_kw if kw in item)

    return sorted(items, key=lambda x: -_score(x))


def extract_template_report(demo_path: Path) -> str:
    """demo_output Markdown의 §8 final_report_markdown 블록 추출."""
    text = demo_path.read_text(encoding="utf-8")
    # ` ```markdown ... ``` ` 블록을 §8 이후에서 찾음
    m = re.search(
        r"## 8\. final_report_markdown[^\n]*\n+```markdown\s+(.*?)\s+```",
        text,
        re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    # fallback: 파일 전체에서 첫 번째 ```markdown 블록
    m2 = re.search(r"```markdown\s+(.*?)\s+```", text, re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return ""


def _extract_template_checklist(template_text: str) -> list[str]:
    """템플릿 보고서에서 - [ ] 형식 체크리스트 항목 전체 추출."""
    items: list[str] = []
    for line in template_text.split("\n"):
        m = re.match(r"\s*-\s+\[\s*\]\s+(.+)", line)
        if m:
            item = m.group(1).strip()
            if item and item not in items:
                items.append(item)
    return items


def _mask_raw_launch_checklist(template_text: str) -> str:
    """템플릿 '## 7. 출시 전 확인 체크리스트' 섹션의 원본 - [ ] 목록을 안내 문구로 대체.

    모델에게 프롬프트를 그대로 두면, 필터링된 [선택 가능한 항목] 지시와
    별개로 템플릿 본문에 남아있는 미필터 원본 체크리스트를 그대로 베낄 수
    있다(실제로 관찰된 실패 모드). 원본 bullet을 안내 문구로 치환해
    모델이 참고할 수 있는 체크리스트 소스를 [선택 가능한 항목] 하나로 한정한다.
    """
    return re.sub(
        r"(## 7\. 출시 전 확인 체크리스트\n\n)(?:- \[ \] .+\n?)+",
        r"\1(이 섹션의 항목은 위 [체크리스트 작성 규칙]의 [선택 가능한 항목] 번호 목록을 사용하세요.)\n\n",
        template_text,
    )


def build_prompt(template_text: str, input_data: dict) -> tuple[str, str]:
    """(system_prompt, user_prompt) 반환.

    input_data: demo 파일 §1 입력 JSON (material_text, power_type,
        battery_included, product_name). 체크리스트 관련성 필터·정렬에 사용.
    """
    system_prompt = (
        "당신은 제품안전 인증 보조 AI입니다. "
        "주어진 템플릿 보고서의 데이터만 사용해 7섹션 형식의 읽기 쉬운 보고서로 재작성합니다.\n\n"
        "[금지 사항]\n"
        "1. 새로운 인증유형·법정 품목명·KC 인증번호·리콜 사유·시험기관을 절대 추가하지 마세요.\n"
        "2. '반드시 인증됩니다', '보장됩니다', '리콜되지 않습니다', '100% 안전합니다' 같은 확정 표현을 쓰지 마세요.\n"
        "3. source_refs, JSON 원문, case_id 등 내부 식별자를 노출하지 마세요.\n"
        "4. 각 섹션은 3~5줄 이내로 간결하게 작성하세요.\n"
        "5. 체크리스트는 반드시 [선택 가능한 항목] 번호 목록 중에서만 고르세요. "
        "목록에 없는 항목을 새로 만들지 마세요."
    )

    legal_name = extract_legal_name(template_text)
    material_text = input_data.get("material_text") or ""
    power_type = input_data.get("power_type") or ""
    battery_included = bool(input_data.get("battery_included") or False)
    product_name = input_data.get("product_name") or ""

    # 체크리스트 항목 추출 → 소재·전원 무관 항목은 코드에서 미리 제외(하드 필터) →
    # 관련도 순 정렬. LLM에게 "관련 없는 항목을 판단해서 빼라"는 부담을 주지 않고,
    # 이미 정제된 목록에서 "상위 N개만 그대로 선택"하도록 만든다.
    checklist_items = _extract_template_checklist(template_text)
    if checklist_items:
        relevant_items = filter_relevant_checklist(
            checklist_items, material_text, power_type, legal_name
        )
        if not relevant_items:
            # 전부 제외되면(예상치 못한 데이터) 원본 유지 — 빈 체크리스트 방지
            relevant_items = checklist_items
        ranked_items = rank_checklist_items(
            relevant_items, material_text, power_type,
            battery_included, product_name, legal_name,
        )
        items_str = "\n".join(f"  {i + 1}. {item}" for i, item in enumerate(ranked_items))
        checklist_instruction = (
            "[체크리스트 작성 규칙]\n"
            "§6 출시 전 체크리스트는 아래 번호 목록 중에서만 고르세요. 목록 밖 항목을 새로 만들지 마세요.\n"
            "이 목록은 이미 제품 소재·전원과의 관련성이 높은 순으로 정렬되어 있습니다. "
            "번호 순서 그대로 위에서부터 5~7개를 선택하세요 (순서를 바꾸거나 건너뛰지 마세요).\n"
            "[선택 가능한 항목]\n"
            + items_str
        )
        checklist_output_hint = "(위 번호 목록의 상위 5~7개, - [ ] 체크박스 형식)"
    else:
        checklist_instruction = (
            "[체크리스트 작성 규칙]\n"
            "§6은 템플릿 §7 항목 중에서만 선택하세요. 새 항목을 만들지 마세요.\n"
            "§1 소재·전원과 무관한 항목(섬유·가죽·아조염료·온열 등)은 제외하세요."
        )
        checklist_output_hint = "(§1 소재·전원 관련 5~7개, - [ ] 체크박스 형식)"

    # 템플릿 본문의 원본(미필터) §7 체크리스트 bullet은 안내 문구로 대체 —
    # 모델이 [선택 가능한 항목] 대신 템플릿 원문을 그대로 베끼는 것을 방지.
    masked_template_text = _mask_raw_launch_checklist(template_text)

    user_prompt = (
        # ── 지시사항 (템플릿 앞에 배치 → 출력에 포함되지 않음) ──
        checklist_instruction
        + "\n\n"
        "아래 템플릿 보고서를 7섹션 형식으로 재작성하세요. "
        "데이터는 반드시 템플릿 보고서의 내용만 사용하세요.\n\n"
        "---\n"
        "[템플릿 보고서]\n\n"
        f"{masked_template_text}\n\n"
        "---\n"
        # ── 출력 형식 (LLM이 따라 출력할 구조) ──
        "[출력 형식 — 반드시 아래 구조대로만 출력하세요]\n\n"
        "# 신제품 출시 전 인증·리콜 리스크 AI 진단 보고서\n\n"
        "## 1. 제품 요약\n"
        "(제품명, 사용 대상 연령, 소재, 전원/배터리, 수입/제조 구분)\n\n"
        "## 2. AI가 판단한 핵심 결과\n"
        "(법정 품목명 후보와 신뢰도, 예상 인증유형, 주요 적용 안전기준)\n\n"
        "## 3. 인증 및 시험기관 안내\n"
        "(인증 종류, 시험기관 필요 여부, 시험기관명)\n\n"
        "## 4. 국내 리콜 데이터 기반 위험 포인트\n"
        "(주요 리콜 사유, 출시 전 확인이 필요한 이유)\n\n"
        "## 5. KC 인증정보 참고\n"
        "(KC 인증사례 건수·기관, KC 사례는 최종 인증 가능성을 보장하지 않음 명시)\n\n"
        "## 6. 출시 전 체크리스트\n"
        f"{checklist_output_hint}\n\n"
        "## 7. 최종 안내\n"
        "(사전 진단 참고자료임을 명시, 최종 판단은 관계기관·전문가 확인 필요)\n\n"
        "지금 바로 보고서를 작성하세요:"
    )
    return system_prompt, user_prompt


def validate_output(
    text: str, material_text: str = "", power_type: str = "", legal_name: str = "",
) -> list[str]:
    """문제 목록 반환. 빈 리스트이면 유효.

    체크리스트의 섬유/온열 관련 키워드는 material_text·power_type·legal_name
    기준으로 실제 무관한 경우에만 경고한다 (섬유제품엔 정당한 항목이므로).
    """
    issues: list[str] = []
    if len(text.strip()) < 300:
        issues.append(f"출력이 너무 짧음 ({len(text.strip())}자 < 300자)")
    for pat in _SECTION_PATTERNS:
        if not re.search(pat, text):
            issues.append(f"섹션 누락: {pat}")
    for expr in _FORBIDDEN:
        if expr in text:
            issues.append(f"금지 표현 포함: '{expr}'")
    # 체크리스트 섹션에 소재·전원과 무관한 항목이 포함되었는지 경고
    checklist_m = re.search(r"## 6\..*?(?=## 7\.|\Z)", text, re.DOTALL)
    if checklist_m:
        cl_text = checklist_m.group(0)
        material = (material_text or "").lower()
        power = (power_type or "").lower()
        is_fabric_relevant = (
            any(tok in material for tok in _FABRIC_MATERIAL_TOKENS)
            or "섬유" in legal_name or "의류" in legal_name
        )
        is_heat_relevant = any(tok in material or tok in power for tok in _HEAT_SIGNAL_TOKENS)
        if not is_fabric_relevant:
            for kw in _FABRIC_ONLY_KEYWORDS:
                if kw in cl_text:
                    issues.append(f"체크리스트에 관련성 낮은 키워드 포함: '{kw}'")
        if not is_heat_relevant:
            for kw in _HEAT_ONLY_KEYWORDS:
                if kw in cl_text:
                    issues.append(f"체크리스트에 관련성 낮은 키워드 포함: '{kw}'")
    return issues


def save_report(out_path: Path, case: dict, text: str, model_name: str,
                elapsed: float, issues: list[str]) -> None:
    """llm_outputs/ 에 보고서 저장."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header_lines = [
        f"<!-- LLM 생성 보고서 — {now} -->",
        f"<!-- 모델: {model_name} | 생성 시간: {elapsed:.1f}초 -->",
        f"<!-- 원본: {case['demo_file']} -->",
        "<!-- 이 파일은 참고자료입니다. /diagnose API 기본 동작(template)과 독립적입니다. -->",
        "",
    ]
    if issues:
        header_lines += [
            "<!-- [경고] LLM 출력 검증 실패 — 아래 내용은 불완전할 수 있습니다. -->",
            f"<!-- 검증 오류: {' | '.join(issues)} -->",
            "",
        ]
    content = "\n".join(header_lines) + text.strip() + "\n"
    out_path.write_text(content, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# LLM 파이프라인 로딩
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text(raw: Any) -> str:
    """pipeline 출력에서 생성된 텍스트 추출 (app/llm/llm_service.py와 동일 로직)."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        for item in reversed(raw):
            if isinstance(item, dict) and item.get("role") == "assistant":
                return item.get("content", "").strip()
        if raw and isinstance(raw[-1], str):
            return raw[-1].strip()
    if isinstance(raw, dict):
        return raw.get("content", raw.get("generated_text", "")).strip()
    return ""


def load_pipeline():
    """transformers text-generation 파이프라인 반환.

    참고: cache_dir는 model_kwargs로 전달해야 함.
    직접 pipeline() 인자로 전달하면 generate() 호출 시 unknown kwarg 에러 발생 (transformers 4.48.x 버그).
    """
    try:
        import torch
        from transformers import pipeline as hf_pipeline
    except ImportError as e:
        print(f"[error] 패키지 미설치: {e}")
        print("  pip install \"transformers>=4.45.0,<5.0.0\" accelerate torch")
        sys.exit(1)

    import transformers
    print(f"  transformers {transformers.__version__} / torch {torch.__version__}")
    device = 0 if torch.cuda.is_available() else -1
    device_label = "GPU" if device == 0 else "CPU"
    print(f"  실행 장치: {device_label}")
    print(f"  모델: {MODEL_NAME}")
    print(f"  캐시: {HF_CACHE}")
    print()

    t0 = time.time()
    pipe = hf_pipeline(
        "text-generation",
        model=MODEL_NAME,
        device=device,
        token=HF_TOKEN or None,
        model_kwargs={"cache_dir": HF_CACHE},
    )
    print(f"  모델 로딩 완료 ({time.time() - t0:.1f}초)\n")
    return pipe


# ─────────────────────────────────────────────────────────────────────────────
# 케이스 처리
# ─────────────────────────────────────────────────────────────────────────────

def process_case(case: dict, pipe) -> bool:
    """단일 케이스 처리. 성공 여부 반환."""
    case_no = case["case_no"]
    demo_path = _PROJECT_ROOT / case["demo_file"]
    out_path = _PROJECT_ROOT / case["out_file"]

    print(f"── Case {case_no}: {case['title']} ──────────────────────────────")

    # 템플릿 추출
    if not demo_path.exists():
        print(f"  [skip] 데모 파일 없음: {demo_path}")
        return False
    template_text = extract_template_report(demo_path)
    if not template_text:
        print(f"  [skip] final_report_markdown 섹션을 찾을 수 없음")
        return False
    print(f"  템플릿 길이: {len(template_text)}자")

    input_data = extract_input_json(demo_path)
    legal_name = extract_legal_name(template_text)

    # 프롬프트 구성
    system_prompt, user_prompt = build_prompt(template_text, input_data)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    print(f"  시스템 프롬프트: {len(system_prompt)}자 / 유저 프롬프트: {len(user_prompt)}자")
    print(f"  LLM 생성 중 (max_new_tokens=1400, temperature=0.2) ...")

    t0 = time.time()
    try:
        result = pipe(
            messages,
            max_new_tokens=1400,
            temperature=0.2,
            do_sample=True,
            repetition_penalty=1.1,
            return_full_text=False,
        )
    except Exception as e:
        print(f"  [error] LLM 실행 실패: {e}")
        return False
    elapsed = time.time() - t0

    # 출력 추출 (app/llm/llm_service._extract_text와 동일 로직)
    raw = result[0].get("generated_text", "") if result else ""
    generated = _extract_text(raw)
    print(f"  생성 완료: {len(generated)}자 ({elapsed:.1f}초)")

    # 검증
    issues = validate_output(
        generated,
        material_text=input_data.get("material_text") or "",
        power_type=input_data.get("power_type") or "",
        legal_name=legal_name,
    )
    if issues:
        print(f"  [warn] 검증 경고 {len(issues)}건:")
        for iss in issues:
            print(f"    - {iss}")
    else:
        print(f"  [ok] 검증 통과")

    # 저장
    save_report(out_path, case, generated, MODEL_NAME, elapsed, issues)
    print(f"  저장: {out_path.relative_to(_PROJECT_ROOT)}\n")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="docs/demo_outputs/*.md → LLM 7섹션 보고서 생성 → docs/llm_outputs/",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        type=int,
        choices=[1, 2, 3],
        default=[1, 2, 3],
        metavar="N",
        help="처리할 케이스 번호 (1=책가방, 2=장난감 자동차, 3=정체불명). 기본: 전체",
    )
    args = parser.parse_args()

    selected = [c for c in CASES if c["case_no"] in args.cases]
    print("=" * 60)
    print("LLM 오프라인 보고서 생성기")
    print(f"  모델: {MODEL_NAME}")
    print(f"  처리 케이스: {[c['title'] for c in selected]}")
    print(f"  출력 폴더: docs/llm_outputs/")
    print("=" * 60)
    print()

    # 파이프라인 로드
    print("[1/2] LLM 파이프라인 로딩 ...")
    pipe = load_pipeline()

    # 케이스 순차 처리
    print("[2/2] 보고서 생성 시작\n")
    success = 0
    for case in selected:
        ok = process_case(case, pipe)
        if ok:
            success += 1

    print("=" * 60)
    print(f"완료: {success}/{len(selected)} 케이스 성공")
    if success < len(selected):
        print("  실패한 케이스의 출력 파일을 확인하세요.")
    print("=" * 60)


if __name__ == "__main__":
    main()
