import logging
import json
from datetime import datetime
from pathlib import Path
from backend.config.settings import LOGS_DIR, LOG_LEVEL

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "stage": getattr(record, "stage", "unknown"),
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data"):
            log_entry.update(record.extra_data)
        return json.dumps(log_entry)

def setup_logging(stage: str = "main") -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"pipeline.{stage}")
    logger.setLevel(getattr(logging, LOG_LEVEL))
    
    # File handler
    log_file = LOGS_DIR / f"{stage}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    fh = logging.FileHandler(log_file)
    fh.setFormatter(JSONFormatter())
    logger.addHandler(fh)
    
    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(ch)
    
    return logger
