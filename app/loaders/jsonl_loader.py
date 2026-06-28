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
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data.append(json.loads(line))
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSONL from {path}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        return []
