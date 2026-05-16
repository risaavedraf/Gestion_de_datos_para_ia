import sys
from pathlib import Path

# Ensure backend/ is on sys.path so config.* and src.* imports resolve
_backend_dir = str(Path(__file__).resolve().parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from datetime import datetime
import json
import pandas as pd

from config.settings import (
    BASE_DIR, BRONZE_DIR, SILVER_DIR, GOLD_DIR, REJECTED_DIR,
    REPORTS_DIR, MODELS_DIR, DATA_SAMPLE_SIZE
)
from config.logging_config import setup_logging

logger = setup_logging("api")

app = FastAPI(
    title="Pipeline DataOps - Credit Card Fraud Detection",
    description="API for DataOps pipeline with Lakehouse architecture (Bronze/Silver/Gold)",
    version="1.0.0"
)

# Serve frontend static files
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/css", StaticFiles(directory=str(frontend_dir / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(frontend_dir / "js")), name="js")
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

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
        "version": "1.0.0"
    }

# ==================== PIPELINE ====================

@app.post("/api/pipeline/run")
async def run_pipeline(sample_size: int = Query(None, description="Sample size for demo")):
    """Run full pipeline: Bronze → Silver → Gold → Load"""
    from src.ingestion import ingest
    from src.cleaning import clean
    from src.validation import validate
    from src.loader import load

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
                "load": load_result
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pipeline/run/{stage}")
async def run_stage(stage: str, sample_size: int = Query(None)):
    """Run individual pipeline stage"""
    from src.ingestion import ingest
    from src.cleaning import clean
    from src.validation import validate
    from src.loader import load

    stage_map = {
        "bronze": lambda: ingest(sample_size=sample_size),
        "silver": lambda: clean(sample_size=sample_size),
        "gold": lambda: validate(sample_size=sample_size),
        "load": lambda: load(sample_size=sample_size)
    }

    if stage not in stage_map:
        raise HTTPException(status_code=400, detail=f"Invalid stage: {stage}. Valid: {list(stage_map.keys())}")

    try:
        result = stage_map[stage]()
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stage {stage} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get current status of all pipeline layers"""
    from src.ingestion import get_bronze_stats
    from src.cleaning import get_silver_stats
    from src.validation import get_gold_stats

    return {
        "bronze": get_bronze_stats(),
        "silver": get_silver_stats(),
        "gold": get_gold_stats()
    }

@app.get("/api/pipeline/logs")
async def pipeline_logs(limit: int = Query(10, description="Number of log entries")):
    """Get recent pipeline logs"""
    logs_dir = BASE_DIR / "logs"
    if not logs_dir.exists():
        return {"logs": []}

    log_files = sorted(logs_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
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
    from src.validation import get_gold_stats
    stats = get_gold_stats()

    if stats.get("status") == "no_data":
        # Try Bronze
        from src.ingestion import get_bronze_stats
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
    sample = df.head(n).to_dict(orient="records")

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
        "total": len(df)
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
            {"category": cat, "count": int(count)}
            for cat, count in cat_counts.items()
        ]
    }

@app.get("/api/dataset/dictionary")
async def data_dictionary():
    """Return data dictionary"""
    dictionary = {
        "columns": [
            {"name": "trans_date_trans_time", "type": "DATETIME", "description": "Transaction timestamp", "sensitive": False},
            {"name": "cc_num", "type": "TEXT", "description": "Credit card number (raw)", "sensitive": True},
            {"name": "cc_num_masked", "type": "TEXT", "description": "SHA256 hash of cc_num", "sensitive": False},
            {"name": "merchant", "type": "TEXT", "description": "Merchant name", "sensitive": False},
            {"name": "category", "type": "TEXT", "description": "Transaction category", "sensitive": False},
            {"name": "amt", "type": "FLOAT", "description": "Transaction amount in USD", "sensitive": False},
            {"name": "gender", "type": "TEXT", "description": "Cardholder gender (M/F)", "sensitive": True},
            {"name": "city", "type": "TEXT", "description": "Cardholder city", "sensitive": True},
            {"name": "state", "type": "TEXT", "description": "Cardholder state (2-letter code)", "sensitive": True},
            {"name": "zip", "type": "TEXT", "description": "Cardholder ZIP code", "sensitive": True},
            {"name": "lat", "type": "FLOAT", "description": "Cardholder latitude", "sensitive": True},
            {"name": "long", "type": "FLOAT", "description": "Cardholder longitude", "sensitive": True},
            {"name": "city_pop", "type": "INTEGER", "description": "City population", "sensitive": False},
            {"name": "job", "type": "TEXT", "description": "Cardholder occupation", "sensitive": True},
            {"name": "dob", "type": "DATE", "description": "Date of birth", "sensitive": True},
            {"name": "trans_num", "type": "TEXT", "description": "Unique transaction ID", "sensitive": False},
            {"name": "unix_time", "type": "INTEGER", "description": "Unix timestamp", "sensitive": False},
            {"name": "merch_lat", "type": "FLOAT", "description": "Merchant latitude", "sensitive": False},
            {"name": "merch_long", "type": "FLOAT", "description": "Merchant longitude", "sensitive": False},
            {"name": "is_fraud", "type": "INTEGER", "description": "Target: 1=fraud, 0=legit", "sensitive": False},
            {"name": "trans_hour", "type": "INTEGER", "description": "Hour of transaction (0-23)", "sensitive": False},
            {"name": "trans_day_of_week", "type": "INTEGER", "description": "Day of week (0=Mon, 6=Sun)", "sensitive": False},
            {"name": "trans_month", "type": "INTEGER", "description": "Month (1-12)", "sensitive": False},
            {"name": "age_at_transaction", "type": "INTEGER", "description": "Approximate age at transaction", "sensitive": False},
            {"name": "distance_km", "type": "FLOAT", "description": "Distance between customer and merchant (km)", "sensitive": False}
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
        kpis["completeness_pct"] = round(len(complete) / total_valid * 100, 2) if total_valid > 0 else 0

        # Duplicate rate
        duplicates = df["trans_num"].duplicated().sum()
        kpis["duplicate_rate_pct"] = round(duplicates / total_valid * 100, 4) if total_valid > 0 else 0

        # Fraud distribution
        fraud_dist = df["is_fraud"].value_counts().to_dict()
        kpis["fraud_count"] = fraud_dist.get(1, 0)
        kpis["legit_count"] = fraud_dist.get(0, 0)
        kpis["fraud_pct"] = round(fraud_dist.get(1, 0) / total_valid * 100, 4) if total_valid > 0 else 0

        # Amount stats
        kpis["amt_mean"] = round(float(df["amt"].mean()), 2)
        kpis["amt_median"] = round(float(df["amt"].median()), 2)
        kpis["amt_max"] = round(float(df["amt"].max()), 2)

        kpis["valid_records"] = total_valid

    if rejected_path.exists():
        rejected_df = pd.read_parquet(rejected_path)
        kpis["rejected_records"] = len(rejected_df)
        total = kpis.get("valid_records", 0) + len(rejected_df)
        kpis["rejection_rate_pct"] = round(len(rejected_df) / total * 100, 4) if total > 0 else 0
        kpis["total_records"] = total
    else:
        kpis["rejected_records"] = 0
        kpis["rejection_rate_pct"] = 0
        kpis["total_records"] = kpis.get("valid_records", 0)

    kpis["status"] = "available" if gold_path.exists() else "no_data"
    kpis["timestamp"] = datetime.now().isoformat()

    return kpis

# ==================== VALIDATION ====================

@app.get("/api/validation/report")
async def validation_report():
    """Get validation report"""
    report_path = GOLD_DIR / "validation_report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Validation report not available. Run pipeline first.")

    with open(report_path) as f:
        return json.load(f)

@app.get("/api/validation/rejected")
async def rejected_records(limit: int = Query(10)):
    """Get rejected records"""
    import pandas as pd

    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    if not rejected_path.exists():
        raise HTTPException(status_code=404, detail="No rejected records available")

    df = pd.read_parquet(rejected_path)
    sample = df.head(limit).to_dict(orient="records")

    return {"rejected": sample, "total": len(df)}

# ==================== MODEL ====================

@app.post("/api/model/train")
async def train_model_endpoint():
    """Train ML models and return results"""
    from src.features import build_features
    from src.model_train import train_models
    from src.model_evaluate import evaluate_model

    try:
        # Build features
        feature_data = build_features()

        # Train models
        train_result = train_models(
            feature_data["X_train"],
            feature_data["y_train"],
            feature_data["feature_names"],
            category_mapping=feature_data.get("category_mapping")
        )

        # Evaluate best model
        eval_result = evaluate_model(
            train_result["best_model"],
            feature_data["X_test"],
            feature_data["y_test"],
            feature_data["feature_names"]
        )

        return {
            "status": "success",
            "best_model": train_result["best_model_type"],
            "best_f1": train_result["best_f1_cv"],
            "models_compared": list(train_result["all_results"].keys()),
            "all_results": train_result["all_results"],
            "metrics": eval_result,
            "duration_seconds": train_result["duration_seconds"]
        }
    except Exception as e:
        logger.error(f"Training failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/model/metrics")
async def model_metrics():
    """Get model evaluation metrics"""
    from src.model_evaluate import load_evaluation_report

    try:
        return load_evaluation_report()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Model not trained yet")

@app.post("/api/model/predict")
async def predict_transaction(data: dict):
    """Predict fraud for single transaction"""
    from src.model_predict import predict_single, prepare_transaction_for_prediction

    try:
        features = prepare_transaction_for_prediction(data)
        result = predict_single(features)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Model not trained yet")
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/model/predict-batch")
async def predict_batch_endpoint(data: dict):
    """Predict fraud for batch of transactions"""
    from src.model_predict import predict_batch, prepare_transaction_for_prediction

    try:
        transactions = data.get("transactions", [])
        if not transactions:
            raise HTTPException(status_code=400, detail="No transactions provided")

        # Prepare features for each transaction
        features_list = [prepare_transaction_for_prediction(t) for t in transactions]
        df = pd.DataFrame(features_list)

        result_df = predict_batch(df)

        predictions = result_df[["prediction", "probability", "risk_level", "label"]].to_dict(orient="records")
        return {"predictions": predictions, "total": len(predictions)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Model not trained yet")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/model/feature-importance")
async def feature_importance():
    """Get feature importance ranking"""
    from src.model_evaluate import load_evaluation_report

    try:
        report = load_evaluation_report()
        return {
            "features": report.get("feature_importance", []),
            "model_type": report.get("model_type", "unknown")
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Model not trained yet")
