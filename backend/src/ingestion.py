import pandas as pd
import json
from datetime import datetime
from pathlib import Path
from config.settings import BRONZE_DIR, RAW_CSV, REQUIRED_COLUMNS
from config.logging_config import setup_logging
from src.utils import calculate_checksum, generate_run_id

logger = setup_logging("bronze")


def ingest(source_path: str = None, sample_size: int = None) -> dict:
    """
    Ingest CSV to Bronze layer.

    Args:
        source_path: Path to source CSV. Defaults to RAW_CSV.
        sample_size: If set, only ingest first N rows (for demo).

    Returns:
        dict with run_id, rows, cols, checksum, status, duration
    """
    run_id = generate_run_id()
    start_time = datetime.now()

    source = Path(source_path) if source_path else RAW_CSV

    # Verify file exists
    if not source.exists():
        logger.error(f"Source file not found: {source}")
        return {"run_id": run_id, "status": "error", "error": "File not found"}

    # Read CSV
    logger.info(f"Reading CSV from {source}")
    df = pd.read_csv(source)

    # Apply sample if specified
    if sample_size and sample_size < len(df):
        df = df.head(sample_size)
        logger.info(f"Sampled to {sample_size} rows")

    # Calculate checksum of original file
    checksum = calculate_checksum(str(source))

    # Save to bronze (parquet for efficiency)
    BRONZE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = BRONZE_DIR / "fraud_bronze.parquet"
    df.to_parquet(output_path, index=False)

    # Also keep CSV copy
    csv_path = BRONZE_DIR / "fraud_bronze.csv"
    df.to_csv(csv_path, index=False)

    duration = (datetime.now() - start_time).total_seconds()

    result = {
        "run_id": run_id,
        "status": "success",
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
        "checksum": checksum,
        "source_path": str(source),
        "output_path": str(output_path),
        "duration_seconds": round(duration, 2),
        "timestamp": datetime.now().isoformat(),
    }

    # Log metadata
    logger.info(f"Ingestion complete: {len(df)} rows, {len(df.columns)} cols")

    # Save ingestion metadata
    meta_path = BRONZE_DIR / "ingestion_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def get_bronze_stats() -> dict:
    """Return stats about Bronze layer data"""
    parquet_path = BRONZE_DIR / "fraud_bronze.parquet"
    if not parquet_path.exists():
        return {"status": "no_data", "message": "Bronze layer is empty"}

    df = pd.read_parquet(parquet_path)
    return {
        "status": "available",
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
    }
