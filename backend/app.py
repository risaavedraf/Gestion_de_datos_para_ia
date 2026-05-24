import json
from datetime import datetime

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from backend.config.settings import (
    ADMIN_API_TOKEN,
    BASE_DIR,
    BRONZE_DIR,
    GOLD_DIR,
    MODELS_DIR,
    REJECTED_DIR,
)
from backend.config.logging_config import setup_logging

logger = setup_logging("api")

app = FastAPI(
    title="Pipeline DataOps - Credit Card Fraud Detection",
    description="API for DataOps pipeline with Lakehouse architecture (Bronze/Silver/Gold)",
    version="1.0.0",
)


def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    """Protect mutating/expensive endpoints with bearer-token auth."""
    if not ADMIN_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_API_TOKEN is not configured",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )

    provided_token = authorization.split(" ", 1)[1].strip()
    if provided_token != ADMIN_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin token"
        )


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amt: float
    trans_hour: int = Field(ge=0, le=23)
    trans_day_of_week: int = Field(ge=0, le=6)
    trans_month: int = Field(ge=1, le=12)
    distance_km: float = Field(ge=0)
    city_pop: int = Field(ge=0)
    age_at_transaction: int = Field(ge=0, le=120)
    category: str
    gender: str


class BatchPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions: list[PredictionRequest]


# Serve frontend static files
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/css", StaticFiles(directory=str(frontend_dir / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(frontend_dir / "js")), name="js")
    app.mount(
        "/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets"
    )

# ==================== ROOT ====================


@app.get("/")
async def root():
    """Serve frontend dashboard"""
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Pipeline DataOps API", "docs": "/docs"}


# ==================== HEALTH ====================


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
    }


# ==================== PIPELINE ====================


@app.post("/api/pipeline/run")
async def run_pipeline(
    sample_size: int | None = Query(default=None, description="Sample size for demo"),
    _: None = Depends(require_admin_token),
):
    """Run full pipeline: Bronze → Silver → Gold → Load"""
    from backend.src.cleaning import clean
    from backend.src.ingestion import ingest
    from backend.src.loader import load
    from backend.src.validation import validate

    try:
        # Bronze
        bronze_result = ingest(sample_size=sample_size)
        if bronze_result.get("status") == "error":
            raise HTTPException(status_code=500, detail=bronze_result)

        # Silver
        silver_result = clean(sample_size=sample_size)
        if silver_result.get("status") == "error":
            raise HTTPException(status_code=500, detail=silver_result)

        # Gold
        gold_result = validate(sample_size=sample_size)
        if gold_result.get("status") == "error":
            raise HTTPException(status_code=500, detail=gold_result)

        # Load
        load_result = load(sample_size=sample_size)
        if load_result.get("status") == "error":
            raise HTTPException(status_code=500, detail=load_result)

        return {
            "status": "success",
            "stages": {
                "bronze": bronze_result,
                "silver": silver_result,
                "gold": gold_result,
                "load": load_result,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/pipeline/run/{stage}")
async def run_stage(
    stage: str,
    sample_size: int | None = Query(default=None),
    _: None = Depends(require_admin_token),
):
    """Run individual pipeline stage"""
    from backend.src.cleaning import clean
    from backend.src.ingestion import ingest
    from backend.src.loader import load
    from backend.src.validation import validate

    stage_map = {
        "bronze": lambda: ingest(sample_size=sample_size),
        "silver": lambda: clean(sample_size=sample_size),
        "gold": lambda: validate(sample_size=sample_size),
        "load": lambda: load(sample_size=sample_size),
    }

    if stage not in stage_map:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid stage: {stage}. Valid: {list(stage_map.keys())}",
        )

    try:
        result = stage_map[stage]()
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stage {stage} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get current status of all pipeline layers"""
    from backend.src.cleaning import get_silver_stats
    from backend.src.ingestion import get_bronze_stats
    from backend.src.validation import get_gold_stats

    result = {
        "bronze": get_bronze_stats(),
        "silver": get_silver_stats(),
        "gold": get_gold_stats(),
    }

    # Try SQL row counts (graceful when DB unavailable)
    try:
        from backend.src.db import get_engine
        from sqlalchemy import text

        engine = get_engine()
        with engine.connect() as conn:
            sql_counts = {}
            for table in [
                "customers",
                "merchants",
                "transactions",
                "pipeline_logs",
                "pipeline_load_state",
            ]:
                row = conn.execute(
                    text(f"SELECT COUNT(*) AS cnt FROM {table}")
                ).fetchone()
                sql_counts[table] = int(row.cnt) if row else 0
            result["sql_counts"] = sql_counts
            logger.info("SQL counts: %s", sql_counts)
    except Exception as exc:
        logger.debug("SQL counts unavailable: %s", exc)
        result["sql_counts"] = None

    return result


@app.get("/api/pipeline/logs")
async def pipeline_logs(limit: int = Query(10, description="Number of log entries")):
    """Get recent pipeline logs"""
    logs_dir = BASE_DIR / "logs"
    if not logs_dir.exists():
        return {"logs": []}

    log_files = sorted(
        logs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    logs = []
    for f in log_files[:limit]:
        try:
            with open(f) as fh:
                logs.append(json.load(fh))
        except Exception:
            pass

    return {"logs": logs}


# ==================== DATASET ====================


@app.get("/api/dataset/stats")
async def dataset_stats():
    """Get dataset statistics"""
    from backend.src.validation import get_gold_stats

    stats = get_gold_stats()

    if stats.get("status") == "no_data":
        # Try Bronze
        from backend.src.ingestion import get_bronze_stats

        return get_bronze_stats()

    return stats


@app.get("/api/dataset/sample")
async def dataset_sample(n: int = Query(10, description="Number of sample rows")):
    """Get sample rows from Gold layer"""
    import pandas as pd

    gold_path = GOLD_DIR / "fraud_gold.parquet"
    if not gold_path.exists():
        gold_path = BRONZE_DIR / "fraud_bronze.parquet"

    if not gold_path.exists():
        raise HTTPException(status_code=404, detail="No data available")

    df = pd.read_parquet(gold_path)
    sample = df.head(n).to_dict("records")

    # Mask sensitive fields
    for row in sample:
        if "cc_num" in row:
            row["cc_num"] = "***"
        if "cc_num_masked" in row:
            row["cc_num_masked"] = row["cc_num_masked"][:8] + "..."

    return {"sample": sample, "total_rows": len(df)}


@app.get("/api/dataset/fraud-dist")
async def fraud_distribution():
    """Get fraud distribution"""
    import pandas as pd

    gold_path = GOLD_DIR / "fraud_gold.parquet"
    if not gold_path.exists():
        raise HTTPException(status_code=404, detail="Gold data not available")

    df = pd.read_parquet(gold_path)
    dist = df["is_fraud"].value_counts().to_dict()

    return {
        "legit": dist.get(0, 0),
        "fraud": dist.get(1, 0),
        "fraud_pct": round(dist.get(1, 0) / len(df) * 100, 4),
        "total": len(df),
    }


@app.get("/api/dataset/category-dist")
async def category_distribution():
    """Get transaction count by category"""
    import pandas as pd

    gold_path = GOLD_DIR / "fraud_gold.parquet"
    if not gold_path.exists():
        gold_path = BRONZE_DIR / "fraud_bronze.parquet"
    if not gold_path.exists():
        raise HTTPException(status_code=404, detail="No data available")

    df = pd.read_parquet(gold_path)
    cat_counts = df["category"].value_counts().head(15)

    return {
        "categories": [
            {"category": cat, "count": int(count)} for cat, count in cat_counts.items()
        ]
    }


@app.get("/api/dataset/dictionary")
async def data_dictionary():
    """Return data dictionary"""
    dictionary = {
        "columns": [
            {
                "name": "trans_date_trans_time",
                "type": "DATETIME",
                "description": "Transaction timestamp",
                "sensitive": False,
            },
            {
                "name": "cc_num",
                "type": "TEXT",
                "description": "Credit card number (raw)",
                "sensitive": True,
            },
            {
                "name": "cc_num_masked",
                "type": "TEXT",
                "description": "SHA256 hash of cc_num",
                "sensitive": False,
            },
            {
                "name": "merchant",
                "type": "TEXT",
                "description": "Merchant name",
                "sensitive": False,
            },
            {
                "name": "category",
                "type": "TEXT",
                "description": "Transaction category",
                "sensitive": False,
            },
            {
                "name": "amt",
                "type": "FLOAT",
                "description": "Transaction amount in USD",
                "sensitive": False,
            },
            {
                "name": "gender",
                "type": "TEXT",
                "description": "Cardholder gender (M/F)",
                "sensitive": True,
            },
            {
                "name": "city",
                "type": "TEXT",
                "description": "Cardholder city",
                "sensitive": True,
            },
            {
                "name": "state",
                "type": "TEXT",
                "description": "Cardholder state (2-letter code)",
                "sensitive": True,
            },
            {
                "name": "zip",
                "type": "TEXT",
                "description": "Cardholder ZIP code",
                "sensitive": True,
            },
            {
                "name": "lat",
                "type": "FLOAT",
                "description": "Cardholder latitude",
                "sensitive": True,
            },
            {
                "name": "long",
                "type": "FLOAT",
                "description": "Cardholder longitude",
                "sensitive": True,
            },
            {
                "name": "city_pop",
                "type": "INTEGER",
                "description": "City population",
                "sensitive": False,
            },
            {
                "name": "job",
                "type": "TEXT",
                "description": "Cardholder occupation",
                "sensitive": True,
            },
            {
                "name": "dob",
                "type": "DATE",
                "description": "Date of birth",
                "sensitive": True,
            },
            {
                "name": "trans_num",
                "type": "TEXT",
                "description": "Unique transaction ID",
                "sensitive": False,
            },
            {
                "name": "unix_time",
                "type": "INTEGER",
                "description": "Unix timestamp",
                "sensitive": False,
            },
            {
                "name": "merch_lat",
                "type": "FLOAT",
                "description": "Merchant latitude",
                "sensitive": False,
            },
            {
                "name": "merch_long",
                "type": "FLOAT",
                "description": "Merchant longitude",
                "sensitive": False,
            },
            {
                "name": "is_fraud",
                "type": "INTEGER",
                "description": "Target: 1=fraud, 0=legit",
                "sensitive": False,
            },
            {
                "name": "trans_hour",
                "type": "INTEGER",
                "description": "Hour of transaction (0-23)",
                "sensitive": False,
            },
            {
                "name": "trans_day_of_week",
                "type": "INTEGER",
                "description": "Day of week (0=Mon, 6=Sun)",
                "sensitive": False,
            },
            {
                "name": "trans_month",
                "type": "INTEGER",
                "description": "Month (1-12)",
                "sensitive": False,
            },
            {
                "name": "age_at_transaction",
                "type": "INTEGER",
                "description": "Approximate age at transaction",
                "sensitive": False,
            },
            {
                "name": "distance_km",
                "type": "FLOAT",
                "description": "Distance between customer and merchant (km)",
                "sensitive": False,
            },
        ]
    }
    return dictionary


# ==================== KPIs ====================


@app.get("/api/kpis")
async def get_kpis():
    """Get pipeline KPIs"""
    import pandas as pd

    gold_path = GOLD_DIR / "fraud_gold.parquet"
    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"

    kpis = {}

    if gold_path.exists():
        df = pd.read_parquet(gold_path)
        total_valid = len(df)

        # Completeness (no nulls in critical fields)
        critical = ["trans_num", "amt", "is_fraud", "trans_date_trans_time"]
        complete = df[critical].dropna()
        kpis["completeness_pct"] = (
            round(len(complete) / total_valid * 100, 2) if total_valid > 0 else 0
        )

        # Duplicate rate
        duplicates = df["trans_num"].duplicated().sum()
        kpis["duplicate_rate_pct"] = (
            round(duplicates / total_valid * 100, 4) if total_valid > 0 else 0
        )

        # Fraud distribution
        fraud_dist = df["is_fraud"].value_counts().to_dict()
        kpis["fraud_count"] = fraud_dist.get(1, 0)
        kpis["legit_count"] = fraud_dist.get(0, 0)
        kpis["fraud_pct"] = (
            round(fraud_dist.get(1, 0) / total_valid * 100, 4) if total_valid > 0 else 0
        )

        # Amount stats
        kpis["amt_mean"] = round(float(df["amt"].mean()), 2)
        kpis["amt_median"] = round(float(df["amt"].median()), 2)
        kpis["amt_max"] = round(float(df["amt"].max()), 2)

        kpis["valid_records"] = total_valid

    if rejected_path.exists():
        rejected_df = pd.read_parquet(rejected_path)
        kpis["rejected_records"] = len(rejected_df)
        total = kpis.get("valid_records", 0) + len(rejected_df)
        kpis["rejection_rate_pct"] = (
            round(len(rejected_df) / total * 100, 4) if total > 0 else 0
        )
        kpis["total_records"] = total
    else:
        kpis["rejected_records"] = 0
        kpis["rejection_rate_pct"] = 0
        kpis["total_records"] = kpis.get("valid_records", 0)

    kpis["status"] = "available" if gold_path.exists() else "no_data"
    kpis["timestamp"] = datetime.now().isoformat()

    return kpis


# ==================== SQL READ ENDPOINTS ====================


def _get_sql_engine_or_503():
    """Return the shared SQL engine (with connection verified) or raise 503."""
    try:
        from backend.src.db import get_engine

        engine = get_engine()
        # Verify the connection actually works (also warms a pool connection)
        engine.connect().close()
        return engine
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=(
                "Database connection failed. "
                "Verify DATABASE_URL and that PostgreSQL is running."
            ),
        ) from e


@app.get("/api/sql/transactions")
async def sql_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    fraud: int | None = Query(None, description="Filter by is_fraud (0 or 1)"),
    start_date: str | None = Query(None, description="ISO date, e.g. 2020-01-01"),
    end_date: str | None = Query(None, description="ISO date, e.g. 2020-06-30"),
    category: str | None = Query(None),
    min_amt: float | None = Query(None, ge=0),
    max_amt: float | None = Query(None, ge=0),
):
    """List transactions from PostgreSQL with pagination and filtering."""
    from sqlalchemy import text

    engine = _get_sql_engine_or_503()

    clauses: list[str] = ["1=1"]
    params: dict = {}

    if fraud is not None:
        clauses.append("t.is_fraud = :fraud")
        params["fraud"] = int(fraud)
    if start_date is not None:
        clauses.append("t.trans_date_trans_time >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        clauses.append("t.trans_date_trans_time <= :end_date")
        params["end_date"] = end_date
    if category is not None:
        clauses.append("LOWER(t.category) = :category")
        params["category"] = category.strip().lower()
    if min_amt is not None:
        clauses.append("t.amt >= :min_amt")
        params["min_amt"] = float(min_amt)
    if max_amt is not None:
        clauses.append("t.amt <= :max_amt")
        params["max_amt"] = float(max_amt)

    where_clause = " AND ".join(clauses)

    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM transactions t WHERE {where_clause}"),
            params,
        ).scalar()

        rows = (
            conn.execute(
                text(f"""
                SELECT t.trans_num, t.amt, t.trans_date_trans_time, t.category,
                       t.is_fraud, t.trans_hour, t.trans_day_of_week, t.trans_month,
                       t.distance_km, t.unix_time, t.city, t.state,
                       m.merchant_name
                FROM transactions t
                LEFT JOIN merchants m ON t.merchant_id = m.merchant_id
                WHERE {where_clause}
                ORDER BY t.trans_date_trans_time DESC
                LIMIT :limit OFFSET :offset
            """),
                {**params, "limit": limit, "offset": offset},
            )
            .mappings()
            .all()
        )

    return {
        "transactions": [dict(r) for r in rows],
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        },
    }


@app.get("/api/sql/transactions/{trans_num}")
async def sql_transaction_by_id(trans_num: str):
    """Get a single transaction by its trans_num."""
    from sqlalchemy import text

    engine = _get_sql_engine_or_503()

    with engine.connect() as conn:
        row = (
            conn.execute(
                text("""
                SELECT t.trans_num, t.amt, t.trans_date_trans_time, t.category,
                       t.is_fraud, t.trans_hour, t.trans_day_of_week, t.trans_month,
                       t.distance_km, t.unix_time, t.city, t.state,
                       m.merchant_name
                FROM transactions t
                LEFT JOIN merchants m ON t.merchant_id = m.merchant_id
                WHERE t.trans_num = :trans_num
            """),
                {"trans_num": trans_num},
            )
            .mappings()
            .first()
        )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction not found: {trans_num}",
        )

    return dict(row)


@app.get("/api/sql/stats")
async def sql_stats():
    """Get aggregated statistics from PostgreSQL transactions table."""
    from sqlalchemy import text

    engine = _get_sql_engine_or_503()

    with engine.connect() as conn:
        # Main aggregates
        main_row = (
            conn.execute(
                text("""
            SELECT
                COUNT(*)                                 AS total_count,
                SUM(CASE WHEN is_fraud = 1 THEN 1 ELSE 0 END) AS fraud_count,
                SUM(CASE WHEN is_fraud = 0 THEN 1 ELSE 0 END) AS legit_count,
                ROUND(AVG(amt)::numeric, 2)             AS amt_mean,
                ROUND(MAX(amt)::numeric, 2)             AS amt_max,
                ROUND(MIN(amt)::numeric, 2)             AS amt_min,
                ROUND(STDDEV(amt)::numeric, 2)           AS amt_std,
                MIN(trans_date_trans_time)              AS date_min,
                MAX(trans_date_trans_time)              AS date_max
            FROM transactions
        """)
            )
            .mappings()
            .first()
        )

        # By-category breakdown
        cat_rows = (
            conn.execute(
                text("""
                SELECT
                    category,
                    COUNT(*)                       AS count,
                    SUM(CASE WHEN is_fraud = 1 THEN 1 ELSE 0 END) AS fraud_count
                FROM transactions
                GROUP BY category
                ORDER BY count DESC
            """)
            )
            .mappings()
            .all()
        )

        # Completeness: percentage of rows with non-null critical fields
        completeness = conn.execute(
            text("""
            SELECT
                ROUND(
                    (
                        COUNT(amt)::float +
                        COUNT(category)::float +
                        COUNT(trans_date_trans_time)::float +
                        COUNT(city)::float +
                        COUNT(state)::float +
                        COUNT(merchant_id)::float
                    ) / (COUNT(*)::float * 6.0) * 100.0,
                    2
                ) AS completeness_pct
            FROM transactions
        """)
        ).scalar()

    total = main_row["total_count"] or 0
    fraud = main_row["fraud_count"] or 0

    return {
        "total_count": total,
        "fraud_count": fraud,
        "legit_count": main_row["legit_count"] or 0,
        "fraud_pct": round(fraud / total * 100, 2) if total > 0 else 0.0,
        "amt_mean": main_row["amt_mean"],
        "amt_max": main_row["amt_max"],
        "amt_min": main_row["amt_min"],
        "amt_std": main_row["amt_std"],
        "by_category": [dict(r) for r in cat_rows],
        "completeness_pct": completeness or 0.0,
        "date_min": str(main_row["date_min"]) if main_row["date_min"] else None,
        "date_max": str(main_row["date_max"]) if main_row["date_max"] else None,
    }


@app.get("/api/sql/kpis")
async def sql_kpis():
    """Get pipeline KPIs sourced from PostgreSQL."""
    from sqlalchemy import text

    engine = _get_sql_engine_or_503()

    with engine.connect() as conn:
        kpi_row = (
            conn.execute(
                text("""
            SELECT
                COUNT(*)                                   AS total_records,
                SUM(CASE WHEN is_fraud = 1 THEN 1 ELSE 0 END) AS fraud_count,
                SUM(CASE WHEN is_fraud = 0 THEN 1 ELSE 0 END) AS legit_count,
                ROUND(AVG(amt)::numeric, 2)               AS amt_mean,
                ROUND(MAX(amt)::numeric, 2)               AS amt_max,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amt) AS amt_median
            FROM transactions
        """)
            )
            .mappings()
            .first()
        )

        completeness = conn.execute(
            text("""
            SELECT
                ROUND(
                    (
                        COUNT(amt)::float +
                        COUNT(category)::float +
                        COUNT(trans_date_trans_time)::float +
                        COUNT(city)::float +
                        COUNT(state)::float +
                        COUNT(merchant_id)::float
                    ) / (COUNT(*)::float * 6.0) * 100.0,
                    2
                ) AS completeness_pct
            FROM transactions
        """)
        ).scalar()

    total = kpi_row["total_records"] or 0
    fraud = kpi_row["fraud_count"] or 0

    return {
        "total_records": total,
        "fraud_count": fraud,
        "legit_count": kpi_row["legit_count"] or 0,
        "fraud_pct": round(fraud / total * 100, 2) if total > 0 else 0.0,
        "amt_mean": kpi_row["amt_mean"],
        "amt_median": round(float(kpi_row["amt_median"]), 2)
        if kpi_row["amt_median"] is not None
        else None,
        "amt_max": kpi_row["amt_max"],
        "completeness_pct": completeness or 0.0,
        "status": "available" if total > 0 else "no_data",
        "source": "postgresql",
        "timestamp": datetime.now().isoformat(),
    }


# ==================== VALIDATION ====================


@app.get("/api/validation/report")
async def validation_report():
    """Get validation report"""
    report_path = GOLD_DIR / "validation_report.json"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Validation report not available. Run pipeline first.",
        )

    with open(report_path) as f:
        return json.load(f)


@app.get("/api/validation/rejected")
async def rejected_records(limit: int = Query(10)):
    """Get rejected records with PII-safe payload."""
    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    if not rejected_path.exists():
        raise HTTPException(status_code=404, detail="No rejected records available")

    df = pd.read_parquet(rejected_path)
    sample = df.head(limit).to_dict("records")

    for row in sample:
        if "cc_num" in row:
            row["cc_num"] = "***"
        if "cc_num_masked" in row and isinstance(row["cc_num_masked"], str):
            row["cc_num_masked"] = row["cc_num_masked"][:8] + "..."

    return {"rejected": sample, "total": len(df)}


# ==================== MODEL ====================


@app.post("/api/model/train")
async def train_model_endpoint(_: None = Depends(require_admin_token)):
    """Train ML models and return results"""
    from backend.src.features import build_features
    from backend.src.model_evaluate import evaluate_model
    from backend.src.model_train import train_models

    try:
        # Build features
        feature_data = build_features()

        # Train models
        train_result = train_models(
            feature_data["X_train"],
            feature_data["y_train"],
            feature_data["feature_names"],
            category_mapping=feature_data.get("category_mapping"),
            gender_mapping=feature_data.get("gender_mapping"),
            scaler_path=feature_data.get("scaler_path"),
            category_fraud_rate_map=feature_data.get("category_fraud_rate_map"),
            global_fraud_rate=feature_data.get("global_fraud_rate"),
        )

        # Evaluate best model
        eval_result = evaluate_model(
            train_result["best_model"],
            feature_data["X_test"],
            feature_data["y_test"],
            feature_data["feature_names"],
        )

        # Update metadata with tuned threshold
        metadata_path = MODELS_DIR / "model_metadata.json"
        if metadata_path.exists():
            import json

            with open(metadata_path) as f:
                metadata = json.load(f)
            metadata["decision_threshold"] = eval_result["tuned_threshold"]
            metadata["tuned_f1"] = eval_result["tuned_f1"]
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2, default=str)
            logger.info(
                "Updated metadata with tuned threshold: %.4f",
                eval_result["tuned_threshold"],
            )

        return {
            "status": "success",
            "best_model": train_result["best_model_type"],
            "best_f1": train_result["best_f1_cv"],
            "models_compared": list(train_result["all_results"].keys()),
            "all_results": train_result["all_results"],
            "metrics": eval_result,
            "duration_seconds": train_result["duration_seconds"],
        }
    except Exception as e:
        logger.error(f"Training failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/model/metrics")
async def model_metrics():
    """Get model evaluation metrics"""
    from backend.src.model_evaluate import load_evaluation_report

    try:
        return load_evaluation_report()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Model not trained yet") from e


@app.post("/api/model/predict")
async def predict_transaction(data: PredictionRequest):
    """Predict fraud for single transaction"""
    from backend.src.model_predict import (
        predict_single,
        prepare_transaction_for_prediction,
    )

    try:
        features = prepare_transaction_for_prediction(data.model_dump())
        result = predict_single(features)
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Model not trained yet") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/model/predict-batch")
async def predict_batch_endpoint(
    data: BatchPredictionRequest,
    _: None = Depends(require_admin_token),
):
    """Predict fraud for batch of transactions"""
    from backend.src.model_predict import (
        predict_batch,
        prepare_transaction_for_prediction,
    )

    try:
        transactions = data.transactions
        if not transactions:
            raise HTTPException(status_code=400, detail="No transactions provided")

        # Prepare features for each transaction
        features_list = [
            prepare_transaction_for_prediction(t.model_dump()) for t in transactions
        ]
        df = pd.DataFrame(features_list)

        result_df = predict_batch(df)

        predictions = result_df[
            ["prediction", "probability", "risk_level", "label"]
        ].to_dict("records")
        return {"predictions": predictions, "total": len(predictions)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Model not trained yet") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/model/feature-importance")
async def feature_importance():
    """Get feature importance ranking"""
    from backend.src.model_evaluate import load_evaluation_report

    try:
        report = load_evaluation_report()
        return {
            "features": report.get("feature_importance", []),
            "model_type": report.get("model_type", "unknown"),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Model not trained yet") from e
