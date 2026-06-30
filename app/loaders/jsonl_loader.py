import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

def load_jsonl(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Loads a JSONL file and returns a list of dictionaries."""
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return []
    
    data = []
    bad_lines = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data.append(json.loads(line))
                except json.JSONDecodeError as e:
                    # 깨진 라인은 건너뛰고 계속 (서버가 죽지 않도록)
                    bad_lines += 1
                    logger.warning(
                        f"Skipping malformed JSONL line {lineno} in {path}: {e}"
                    )
        if bad_lines:
            logger.warning(f"{path}: {bad_lines} malformed line(s) skipped, {len(data)} loaded")
        return data
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        return data  # 부분 로드분이라도 반환
