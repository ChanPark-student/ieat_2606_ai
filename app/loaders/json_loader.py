import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

def load_json(file_path: Union[str, Path]) -> Union[Dict[str, Any], List[Any]]:
    """Loads a JSON file and returns its content."""
    path = Path(file_path)
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return {}
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading file {path}: {e}")
        return {}
