from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    PROJECT_NAME: str = "Compliance Assistant AI MVP"
    VERSION: str = "1.0.0"

    # LLM 활성화 여부 (기본값: 비활성화)
    # true로 설정하면 /diagnose 첫 호출 시 모델을 lazy load합니다.
    ENABLE_LLM: bool = False

    # Hugging Face Settings
    HF_TOKEN: str = ""
    HF_MODEL_NAME: str = "Qwen/Qwen2.5-1.5B-Instruct"
    LLM_MAX_NEW_TOKENS: int = 1200
    LLM_TEMPERATURE: float = 0.2

    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"

    MASTER_JSON_DIR: Path = DATA_DIR / "master_json"
    SAFETY_JSON_DIR: Path = DATA_DIR / "safety_json"
    RAG_JSONL_DIR: Path = DATA_DIR / "rag_jsonl"

    class Config:
        env_file = ".env"

settings = Settings()
