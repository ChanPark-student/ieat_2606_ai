from app.schemas.response import DiagnosisResponse
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

def generate_llm_report(response: DiagnosisResponse) -> str:
    """
    LLM API를 호출하여 최종 보고서를 생성합니다.
    현재는 NotImplementedError를 발생시켜 항상 예외가 발생하도록 합니다.
    """
    if not settings.HF_TOKEN:
        raise ValueError("HF_TOKEN is not configured in environment variables.")
        
    logger.info(f"Generating LLM report using model: {settings.HF_MODEL_NAME}")
    
    # TODO: Implement actual LLM call here using huggingface_hub or transformers
    raise NotImplementedError("LLM report generation is not yet implemented.")
