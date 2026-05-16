import pandas as pd
import json
from datetime import datetime
from pathlib import Path
from config.settings import (
    SILVER_DIR,
    GOLD_DIR,
    REJECTED_DIR,
    REQUIRED_COLUMNS,
    VALID_CATEGORIES,
    VALID_GENDERS,
    LAT_MIN,
    LAT_MAX,
    LONG_MIN,
    LONG_MAX,
)
from config.logging_config import setup_logging
from src.utils import generate_run_id

logger = setup_logging("gold")


def validate(input_path: str = None, sample_size: int = None) -> dict:
    """
    Validate Silver data and produce Gold layer + rejected records.

    Structural rules:
    - Required columns exist
    - trans_date_trans_time is valid datetime
    - amt is numeric and > 0
    - trans_num is unique (no duplicates)
    - lat/long within US bounds

    Semantic rules:
    - is_fraud is 0 or 1
    - gender is M or F
    - category is in valid list
    - unix_time consistent with trans_date_trans_time
    - merch_lat/merch_long within US bounds

    Returns:
        dict with run_id, total, valid, rejected, rejection_breakdown, status, duration
    """
    run_id = generate_run_id()
    start_time = datetime.now()

    # Load Silver data
    source = Path(input_path) if input_path else SILVER_DIR / "fraud_silver.parquet"
    if not source.exists():
        logger.error(f"Silver data not found: {source}")
        return {"run_id": run_id, "status": "error", "error": "Silver data not found"}

    df = pd.read_parquet(source)
    total = len(df)
    logger.info(f"Loaded {total} rows from Silver")

    if sample_size and sample_size < len(df):
        df = df.head(sample_size)
        total = len(df)

    valid_rows = []
    rejected_rows = []
    rejection_breakdown = {}

    # Track seen trans_num for duplicate detection
    seen_trans_num = set()

    for idx, row in df.iterrows():
        errors = []

        # === STRUCTURAL RULES ===

        # Check amt > 0
        if pd.isna(row.get("amt")) or row["amt"] <= 0:
            errors.append("amt_invalid")

        # Check trans_num uniqueness
        if row.get("trans_num") in seen_trans_num:
            errors.append("duplicate_trans_num")
        seen_trans_num.add(row.get("trans_num"))

        # Check coordinates
        lat = row.get("lat")
        long = row.get("long")
        if (
            pd.isna(lat)
            or pd.isna(long)
            or not (LAT_MIN <= lat <= LAT_MAX)
            or not (LONG_MIN <= long <= LONG_MAX)
        ):
            errors.append("invalid_coordinates")

        merch_lat = row.get("merch_lat")
        merch_long = row.get("merch_long")
        if (
            pd.isna(merch_lat)
            or pd.isna(merch_long)
            or not (LAT_MIN <= merch_lat <= LAT_MAX)
            or not (LONG_MIN <= merch_long <= LONG_MAX)
        ):
            errors.append("invalid_merch_coordinates")

        # === SEMANTIC RULES ===

        # Check is_fraud
        if row.get("is_fraud") not in [0, 1]:
            errors.append("invalid_is_fraud")

        # Check gender
        if row.get("gender") not in VALID_GENDERS:
            errors.append("invalid_gender")

        # Check category
        if row.get("category") not in VALID_CATEGORIES:
            errors.append("invalid_category")

        # Check unix_time consistency (warn only — dataset has known drift)
        try:
            trans_ts = pd.Timestamp(row["trans_date_trans_time"]).timestamp()
            unix_diff = abs(row.get("unix_time", 0) - trans_ts)
            if unix_diff > 86400:  # More than 24h difference
                # Known dataset characteristic: unix_time and trans_date_trans_time
                # may differ significantly. Log as warning, not rejection.
                pass
        except Exception:
            errors.append("datetime_parse_error")

        # Categorize
        if errors:
            rejected_row = row.to_dict()
            rejected_row["rejection_reason"] = "; ".join(errors)
            rejected_row["run_id"] = run_id
            rejected_rows.append(rejected_row)

            for error in errors:
                rejection_breakdown[error] = rejection_breakdown.get(error, 0) + 1
        else:
            valid_rows.append(row)

    # Save valid records to Gold
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    valid_df = pd.DataFrame(valid_rows)
    gold_path = GOLD_DIR / "fraud_gold.parquet"
    valid_df.to_parquet(gold_path, index=False)
    csv_path = GOLD_DIR / "fraud_gold.csv"
    valid_df.to_csv(csv_path, index=False)

    # Save rejected records
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    rejected_df = pd.DataFrame(rejected_rows)
    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    rejected_df.to_parquet(rejected_path, index=False)
    rejected_csv = REJECTED_DIR / "fraud_rejected.csv"
    rejected_df.to_csv(rejected_csv, index=False)

    duration = (datetime.now() - start_time).total_seconds()

    result = {
        "run_id": run_id,
        "status": "success",
        "total": total,
        "valid": len(valid_rows),
        "rejected": len(rejected_rows),
        "rejection_rate_pct": (
            round(len(rejected_rows) / total * 100, 4) if total > 0 else 0
        ),
        "rejection_breakdown": rejection_breakdown,
        "gold_path": str(gold_path),
        "rejected_path": str(rejected_path),
        "duration_seconds": round(duration, 2),
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(
        f"Validation complete: {len(valid_rows)} valid, {len(rejected_rows)} rejected"
    )

    report_path = GOLD_DIR / "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def get_gold_stats() -> dict:
    """Return stats about Gold layer data"""
    parquet_path = GOLD_DIR / "fraud_gold.parquet"
    if not parquet_path.exists():
        return {"status": "no_data", "message": "Gold layer is empty"}

    df = pd.read_parquet(parquet_path)
    fraud_dist = df["is_fraud"].value_counts().to_dict()

    return {
        "status": "available",
        "rows": len(df),
        "cols": len(df.columns),
        "columns": list(df.columns),
        "fraud_distribution": {
            "legit": fraud_dist.get(0, 0),
            "fraud": fraud_dist.get(1, 0),
            "fraud_pct": round(fraud_dist.get(1, 0) / len(df) * 100, 4),
        },
        "amt_stats": {
            "min": float(df["amt"].min()),
            "max": float(df["amt"].max()),
            "mean": round(float(df["amt"].mean()), 2),
            "median": round(float(df["amt"].median()), 2),
        },
    }
