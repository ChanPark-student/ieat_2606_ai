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


def build_prompt(template_text: str) -> tuple[str, str]:
    """(system_prompt, user_prompt) 반환."""
    system_prompt = (
        "당신은 제품안전 인증 보조 AI입니다. "
        "주어진 템플릿 보고서의 데이터만 사용해 7섹션 형식의 읽기 쉬운 보고서로 재작성합니다.\n\n"
        "[금지 사항]\n"
        "- 새로운 인증유형·법정 품목명·KC 인증번호·리콜 사유·시험기관을 절대 추가하지 마세요.\n"
        "- '반드시 인증됩니다', '보장됩니다', '리콜되지 않습니다', '100% 안전합니다' 같은 확정 표현을 쓰지 마세요.\n"
        "- source_refs, JSON 원문, case_id 등 내부 식별자를 노출하지 마세요.\n"
        "- 각 섹션은 3~5줄 이내로 간결하게 작성하세요."
    )

    user_prompt = (
        "아래 템플릿 보고서를 7섹션 형식으로 재작성하세요. "
        "데이터는 반드시 템플릿 보고서의 내용만 사용하세요.\n\n"
        "---\n"
        "[템플릿 보고서]\n\n"
        f"{template_text}\n\n"
        "---\n"
        "[출력 형식 — 반드시 아래 구조를 유지하세요]\n\n"
        "# 신제품 출시 전 인증·리콜 리스크 AI 진단 보고서\n\n"
        "## 1. 제품 요약\n"
        "(제품명, 사용 대상 연령, 소재, 전원/배터리, 수입/제조 구분)\n\n"
        "## 2. AI가 판단한 핵심 결과\n"
        "(법정 품목명 후보와 신뢰도, 예상 인증유형, 주요 적용 안전기준)\n\n"
        "## 3. 인증 및 시험기관 안내\n"
        "(인증 종류, 시험기관 필요 여부, 시험기관명 또는 자가시험 여부)\n\n"
        "## 4. 국내 리콜 데이터 기반 위험 포인트\n"
        "(주요 리콜 사유, 출시 전 확인이 필요한 이유)\n\n"
        "## 5. KC 인증정보 참고\n"
        "(동일 품목군 KC 인증사례 건수·기관 또는 사례가 없는 이유, "
        "KC 사례는 최종 인증 가능성을 보장하지 않음을 명시)\n\n"
        "## 6. 출시 전 체크리스트\n"
        "(핵심 5~8개, - [ ] 체크박스 형식)\n\n"
        "## 7. 최종 안내\n"
        "(사전 진단 참고자료임을 명시, 최종 판단은 관계기관·전문가 확인 필요)\n\n"
        "지금 바로 보고서를 작성하세요:"
    )
    return system_prompt, user_prompt


def validate_output(text: str) -> list[str]:
    """문제 목록 반환. 빈 리스트이면 유효."""
    issues: list[str] = []
    if len(text.strip()) < 300:
        issues.append(f"출력이 너무 짧음 ({len(text.strip())}자 < 300자)")
    for pat in _SECTION_PATTERNS:
        if not re.search(pat, text):
            issues.append(f"섹션 누락: {pat}")
    for expr in _FORBIDDEN:
        if expr in text:
            issues.append(f"금지 표현 포함: '{expr}'")
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

    # 프롬프트 구성
    system_prompt, user_prompt = build_prompt(template_text)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    print(f"  시스템 프롬프트: {len(system_prompt)}자 / 유저 프롬프트: {len(user_prompt)}자")
    print(f"  LLM 생성 중 (max_new_tokens=1200, temperature=0.2) ...")

    t0 = time.time()
    try:
        result = pipe(
            messages,
            max_new_tokens=1200,
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
    issues = validate_output(generated)
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
