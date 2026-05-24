import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from backend.config.logging_config import setup_logging
from backend.config.settings import (
    GOLD_DIR,
    LAT_MAX,
    LAT_MIN,
    LONG_MAX,
    LONG_MIN,
    REJECTED_DIR,
    SILVER_DIR,
    SILVER_REQUIRED_COLUMNS,
    VALID_CATEGORIES,
    VALID_GENDERS,
)
from backend.src.utils import generate_run_id

logger = setup_logging("gold")


def _to_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        numeric_value = float(value)
        if math.isnan(numeric_value):
            return None
        return numeric_value
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        numeric_value = int(value)
        return numeric_value
    except (TypeError, ValueError):
        return None


def validate(input_path: str | None = None, sample_size: int | None = None) -> dict:
    """Validate Silver data and produce Gold + rejected datasets."""
    run_id = generate_run_id()
    start_time = datetime.now()

    source = Path(input_path) if input_path else SILVER_DIR / "fraud_silver.parquet"
    if not source.exists():
        logger.error(f"Silver data not found: {source}")
        return {"run_id": run_id, "status": "error", "error": "Silver data not found"}

    df = pd.read_parquet(source)
    if sample_size and sample_size > 0:
        df = df.head(sample_size)

    total = len(df)
    logger.info(f"Loaded {total} rows from Silver")

    missing_required = [col for col in SILVER_REQUIRED_COLUMNS if col not in df.columns]
    if missing_required:
        logger.error(f"Missing required Silver columns: {missing_required}")
        return {
            "run_id": run_id,
            "status": "error",
            "error": "Missing required columns",
            "missing_columns": missing_required,
        }

    valid_rows: list[dict] = []
    rejected_rows: list[dict] = []
    rejection_breakdown: dict[str, int] = {}
    seen_trans_num: set[str] = set()

    for row in df.to_dict(orient="records"):
        errors: list[str] = []

        amt = _to_float(row.get("amt"))
        if amt is None or amt <= 0:
            errors.append("amt_invalid")

        trans_num = str(row.get("trans_num", ""))
        if trans_num in seen_trans_num:
            errors.append("duplicate_trans_num")
        seen_trans_num.add(trans_num)

        lat = _to_float(row.get("lat"))
        lon = _to_float(row.get("long"))
        if (
            lat is None
            or lon is None
            or lat < LAT_MIN
            or lat > LAT_MAX
            or lon < LONG_MIN
            or lon > LONG_MAX
        ):
            errors.append("invalid_coordinates")

        merch_lat = _to_float(row.get("merch_lat"))
        merch_lon = _to_float(row.get("merch_long"))
        if (
            merch_lat is None
            or merch_lon is None
            or merch_lat < LAT_MIN
            or merch_lat > LAT_MAX
            or merch_lon < LONG_MIN
            or merch_lon > LONG_MAX
        ):
            errors.append("invalid_merch_coordinates")

        fraud_flag = _to_int(row.get("is_fraud"))
        if fraud_flag not in (0, 1):
            errors.append("invalid_is_fraud")

        gender = str(row.get("gender", "")).upper().strip()
        if gender not in VALID_GENDERS:
            errors.append("invalid_gender")

        category = str(row.get("category", "")).lower().strip()
        if category not in VALID_CATEGORIES:
            errors.append("invalid_category")

        trans_date_value = row.get("trans_date_trans_time")
        trans_ts = pd.to_datetime(str(trans_date_value), errors="coerce")
        if pd.isna(trans_ts):
            errors.append("datetime_parse_error")

        row_clean = dict(row)
        row_clean.pop("cc_num", None)

        if errors:
            row_clean["rejection_reason"] = "; ".join(errors)
            row_clean["run_id"] = run_id
            rejected_rows.append(row_clean)
            for error in errors:
                rejection_breakdown[error] = rejection_breakdown.get(error, 0) + 1
        else:
            valid_rows.append(row_clean)

    valid_df = pd.DataFrame(valid_rows)
    rejected_df = pd.DataFrame(rejected_rows)

    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    gold_path = GOLD_DIR / "fraud_gold.parquet"
    valid_df.to_parquet(gold_path, index=False)
    valid_df.to_csv(GOLD_DIR / "fraud_gold.csv", index=False)

    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    rejected_df.to_parquet(rejected_path, index=False)
    rejected_df.to_csv(REJECTED_DIR / "fraud_rejected.csv", index=False)

    duration = (datetime.now() - start_time).total_seconds()

    result = {
        "run_id": run_id,
        "status": "success",
        "total": total,
        "valid": len(valid_rows),
        "rejected": len(rejected_rows),
        "rejection_rate_pct": round(len(rejected_rows) / total * 100, 4)
        if total > 0
        else 0,
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
    with open(report_path, "w", encoding="utf-8") as f:
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
            "fraud_pct": round(fraud_dist.get(1, 0) / len(df) * 100, 4)
            if len(df) > 0
            else 0.0,
        },
        "amt_stats": {
            "min": float(df["amt"].min()) if len(df) > 0 else 0.0,
            "max": float(df["amt"].max()) if len(df) > 0 else 0.0,
            "mean": round(float(df["amt"].mean()), 2) if len(df) > 0 else 0.0,
            "median": round(float(df["amt"].median()), 2) if len(df) > 0 else 0.0,
        },
    }
