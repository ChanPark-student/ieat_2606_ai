from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    PROJECT_NAME: str = "Compliance Assistant AI MVP"
    VERSION: str = "1.0.0"
    
    # Hugging Face Settings
    HF_TOKEN: str = ""
    HF_MODEL_NAME: str = "kakaocorp/kanana-1.5-2.1b-instruct-2505"
    
    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    
    MASTER_JSON_DIR: Path = DATA_DIR / "master_json"
    SAFETY_JSON_DIR: Path = DATA_DIR / "safety_json"
    RAG_JSONL_DIR: Path = DATA_DIR / "rag_jsonl"

    class Config:
        env_file = ".env"

settings = Settings()
