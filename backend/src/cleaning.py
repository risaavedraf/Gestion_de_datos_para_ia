import json
from datetime import datetime
from pathlib import Path
import pandas as pd

from backend.config.logging_config import setup_logging
from backend.config.settings import BRONZE_DIR, SILVER_DIR
from backend.src.utils import (
    calculate_age_at_transaction,
    generate_run_id,
    haversine,
    mask_pii,
)

logger = setup_logging("silver")


def clean(input_path: str | None = None, sample_size: int | None = None) -> dict:
    """
    Clean and transform Bronze data to Silver layer.

    Args:
        input_path: Path to Bronze parquet. Defaults to BRONZE_DIR/fraud_bronze.parquet.
        sample_size: If set, only process first N rows.

    Returns:
        dict with run_id, rows_in, rows_out, transformations, status, duration
    """
    run_id = generate_run_id()
    start_time = datetime.now()

    # Load Bronze data
    source = Path(input_path) if input_path else BRONZE_DIR / "fraud_bronze.parquet"
    if not source.exists():
        logger.error(f"Bronze data not found: {source}")
        return {"run_id": run_id, "status": "error", "error": "Bronze data not found"}

    df = pd.read_parquet(source)
    rows_in = len(df)
    logger.info(f"Loaded {rows_in} rows from Bronze")

    if sample_size and sample_size < len(df):
        df = df.head(sample_size)

    transformations = []

    # 1. Drop technical index column
    if "Unnamed: 0" in df.columns:
        df = df.drop(columns=["Unnamed: 0"])
        transformations.append("Dropped 'Unnamed: 0' column")

    # 2. Cast cc_num to string
    df["cc_num"] = df["cc_num"].astype(str)
    transformations.append("Cast cc_num to string")

    # 3. Parse dates
    df["trans_date_trans_time"] = pd.to_datetime(df["trans_date_trans_time"])
    df["dob"] = pd.to_datetime(df["dob"]).dt.date
    transformations.append("Parsed trans_date_trans_time and dob to datetime")

    # 4. Cast numeric types
    df["amt"] = df["amt"].astype(float)
    df["is_fraud"] = df["is_fraud"].astype(int)
    transformations.append("Cast amt to float, is_fraud to int")

    # 5. Normalize strings
    df["category"] = df["category"].str.lower().str.strip()
    df["gender"] = df["gender"].str.upper().str.strip()
    df["state"] = df["state"].str.upper().str.strip()
    df["merchant"] = df["merchant"].str.strip()
    df["city"] = df["city"].str.strip()
    transformations.append("Normalized category (lower), gender/state (upper)")

    # 6. Create derived columns
    df["trans_hour"] = df["trans_date_trans_time"].dt.hour
    df["trans_day_of_week"] = df["trans_date_trans_time"].dt.dayofweek
    df["trans_month"] = df["trans_date_trans_time"].dt.month
    transformations.append("Created trans_hour, trans_day_of_week, trans_month")

    # 7. Calculate age at transaction
    df["age_at_transaction"] = df.apply(
        lambda row: calculate_age_at_transaction(
            row["dob"], row["trans_date_trans_time"]
        ),
        axis=1,
    )
    transformations.append("Calculated age_at_transaction")

    # 8. Calculate distance (haversine)
    df["distance_km"] = df.apply(
        lambda row: haversine(
            row["lat"], row["long"], row["merch_lat"], row["merch_long"]
        ),
        axis=1,
    ).round(2)
    transformations.append("Calculated distance_km (haversine)")

    # 9. Mask PII
    df["cc_num_masked"] = df["cc_num"].apply(mask_pii)
    transformations.append("Created cc_num_masked (SHA256)")

    # 10. Drop sensitive columns from output (keep only masked card identifier)
    df = df.drop(columns=["cc_num", "first", "last", "street", "dob"])
    transformations.append("Dropped PII columns: cc_num, first, last, street, dob")

    # Save to Silver
    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SILVER_DIR / "fraud_silver.parquet"
    df.to_parquet(output_path, index=False)

    csv_path = SILVER_DIR / "fraud_silver.csv"
    df.to_csv(csv_path, index=False)

    duration = (datetime.now() - start_time).total_seconds()

    result = {
        "run_id": run_id,
        "status": "success",
        "rows_in": rows_in,
        "rows_out": len(df),
        "columns": list(df.columns),
        "transformations": transformations,
        "output_path": str(output_path),
        "duration_seconds": round(duration, 2),
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(
        f"Cleaning complete: {rows_in} → {len(df)} rows, {len(transformations)} transformations"
    )

    meta_path = SILVER_DIR / "cleaning_metadata.json"
    with open(meta_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    return result


def get_silver_stats() -> dict:
    """Return stats about Silver layer data"""
    parquet_path = SILVER_DIR / "fraud_silver.parquet"
    if not parquet_path.exists():
        return {"status": "no_data", "message": "Silver layer is empty"}

    df = pd.read_parquet(parquet_path)
    return {
        "status": "available",
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
        "derived_columns": [
            "trans_hour",
            "trans_day_of_week",
            "trans_month",
            "age_at_transaction",
            "distance_km",
            "cc_num_masked",
        ],
    }
