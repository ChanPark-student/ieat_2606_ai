"""LLM 보고서 정제 서비스.

ENABLE_LLM=false (기본값):
    generate_llm_report()가 ValueError를 발생 → diagnosis_service가 template fallback 사용.

ENABLE_LLM=true:
    /diagnose 첫 호출 시 transformers pipeline을 lazy load.
    LLM이 템플릿 보고서를 받아 문장만 자연스럽게 정제.
    출력 검증 실패 또는 예외 → RuntimeError → template fallback.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from app.schemas.response import DiagnosisResponse
from app.core.config import settings

logger = logging.getLogger(__name__)

_MIN_REPORT_LENGTH = 300

_FORBIDDEN_EXPRESSIONS = [
    "반드시 인증됩니다",
    "인증이 보장됩니다",
    "인증이 가능합니다",
    "확실히 인증",
    "리콜됩니다",
    "리콜이 발생합니다",
    "문제 없습니다",
    "문제없습니다",
    "100% 안전합니다",
    "보장됩니다",
    "안전이 보장",
]

_pipeline: Optional[Any] = None
_pipeline_lock = threading.Lock()
_pipeline_failed = False


def _get_pipeline() -> Any:
    """transformers text-generation pipeline을 lazy load."""
    global _pipeline, _pipeline_failed

    if _pipeline is not None:
        return _pipeline
    if _pipeline_failed:
        raise RuntimeError("이전 모델 로딩 실패로 재시도하지 않습니다.")

    with _pipeline_lock:
        if _pipeline is not None:
            return _pipeline
        if _pipeline_failed:
            raise RuntimeError("이전 모델 로딩 실패로 재시도하지 않습니다.")

        try:
            import torch
            from transformers import pipeline as hf_pipeline

            model_name = settings.HF_MODEL_NAME
            cache_dir = str(settings.BASE_DIR / "hf_cache")
            token = settings.HF_TOKEN or None
            device = 0 if torch.cuda.is_available() else -1

            logger.info("LLM 모델 로딩 시작: %s (device=%s)", model_name, device)
            _pipeline = hf_pipeline(
                "text-generation",
                model=model_name,
                device=device,
                token=token,
                model_kwargs={"cache_dir": cache_dir},
            )
            logger.info("LLM 모델 로딩 완료: %s", model_name)
        except Exception as exc:
            _pipeline_failed = True
            logger.error("LLM 모델 로딩 실패: %s", exc)
            raise RuntimeError(f"LLM 모델 로딩 실패: {exc}") from exc

    return _pipeline


def _extract_text(raw: Any) -> str:
    """pipeline 출력에서 생성된 텍스트를 추출."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        # chat pipeline: [{"role": "assistant", "content": "..."}]
        for item in reversed(raw):
            if isinstance(item, dict) and item.get("role") == "assistant":
                return item.get("content", "").strip()
        # 마지막 항목이 문자열이면 사용
        if raw and isinstance(raw[-1], str):
            return raw[-1].strip()
    if isinstance(raw, dict):
        return raw.get("content", raw.get("generated_text", "")).strip()
    return ""


def _validate(text: str) -> bool:
    """LLM 출력 안전성·충분성 검증."""
    if len(text.strip()) < _MIN_REPORT_LENGTH:
        logger.warning("LLM 출력 너무 짧음 (%d자)", len(text.strip()))
        return False
    for expr in _FORBIDDEN_EXPRESSIONS:
        if expr in text:
            logger.warning("LLM 출력 금지 표현 포함: '%s'", expr)
            return False
    return True


def generate_llm_report(
    response: DiagnosisResponse,
    retrieved_chunks: Optional[list] = None,
) -> str:
    """LLM으로 보고서 문장 정제.

    retrieved_chunks: RAG retriever가 찾은 근거 chunk (선택). 프롬프트에 참고
        블록으로 포함되며, LLM은 이 범위 밖 사실을 만들면 안 됨.

    반환값: 정제된 Markdown 보고서 문자열.
    실패 시 RuntimeError/ValueError 발생 → 호출자가 template fallback으로 처리.
    """
    if not settings.ENABLE_LLM:
        raise ValueError(
            "LLM 비활성화 (ENABLE_LLM=false). 기존 템플릿 보고서를 사용합니다."
        )

    from app.llm.prompts import (
        SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, build_retrieved_context,
    )
    from app.services.report_service import generate_markdown_report

    template_report = generate_markdown_report(response)
    retrieved_context = build_retrieved_context(retrieved_chunks)

    pipe = _get_pipeline()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
            template_report=template_report,
            retrieved_context=retrieved_context,
        )},
    ]

    logger.info(
        "LLM 보고서 생성 요청 (model=%s, max_new_tokens=%d)",
        settings.HF_MODEL_NAME, settings.LLM_MAX_NEW_TOKENS,
    )

    outputs = pipe(
        messages,
        max_new_tokens=settings.LLM_MAX_NEW_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
        do_sample=settings.LLM_TEMPERATURE > 0,
        return_full_text=False,
    )

    raw = outputs[0].get("generated_text", "") if outputs else ""
    generated = _extract_text(raw)

    if not _validate(generated):
        logger.warning("LLM 출력 검증 실패 → 템플릿 보고서 사용")
        return template_report

    logger.info("LLM 보고서 생성 완료 (%d자)", len(generated))
    return generated
