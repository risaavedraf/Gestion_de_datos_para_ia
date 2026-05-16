"""
Pipeline DataOps - Credit Card Fraud Detection
Main orchestrator for running the full pipeline
"""
import argparse
import sys
import json
from datetime import datetime
from pathlib import Path

# Add project root and backend/ to path
_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from config.settings import DATA_SAMPLE_SIZE
from config.logging_config import setup_logging

logger = setup_logging("main")


def run_full_pipeline(sample_size: int = None):
    """Run the complete pipeline: Bronze → Silver → Gold → Load"""
    from src.ingestion import ingest
    from src.cleaning import clean
    from src.validation import validate
    from src.loader import load

    start = datetime.now()
    logger.info("=" * 60)
    logger.info("STARTING FULL PIPELINE")
    logger.info("=" * 60)

    # Bronze
    logger.info(">>> STAGE 1: BRONZE (Ingestion)")
    bronze = ingest(sample_size=sample_size)
    print(f"Bronze: {bronze.get('rows', 0)} rows ingested")

    # Silver
    logger.info(">>> STAGE 2: SILVER (Cleaning)")
    silver = clean(sample_size=sample_size)
    print(f"Silver: {silver.get('rows_out', 0)} rows cleaned, {len(silver.get('transformations', []))} transformations")

    # Gold
    logger.info(">>> STAGE 3: GOLD (Validation)")
    gold = validate(sample_size=sample_size)
    print(f"Gold: {gold.get('valid', 0)} valid, {gold.get('rejected', 0)} rejected")

    # Load
    logger.info(">>> STAGE 4: LOAD (PostgreSQL)")
    try:
        loaded = load(sample_size=sample_size)
        print(f"Load: {loaded.get('transactions_attempted', 0)} transactions loaded")
    except Exception as e:
        print(f"Load: SKIPPED (no PostgreSQL available: {e})")
        loaded = {"status": "skipped", "error": str(e)}

    duration = (datetime.now() - start).total_seconds()

    summary = {
        "status": "success",
        "duration_seconds": round(duration, 2),
        "bronze": bronze,
        "silver": silver,
        "gold": gold,
        "load": loaded
    }

    print(f"\nPipeline completed in {duration:.1f}s")
    return summary


def run_train():
    """Train ML models"""
    from src.features import build_features
    from src.model_train import train_models
    from src.model_evaluate import evaluate_model

    print("Building features...")
    feature_data = build_features()

    print(f"Training {3} models with 5-fold CV...")
    train_result = train_models(
        feature_data["X_train"],
        feature_data["y_train"],
        feature_data["feature_names"],
        category_mapping=feature_data.get("category_mapping")
    )

    print(f"Best model: {train_result['best_model_type']} (F1={train_result['best_f1_cv']:.4f})")

    print("Evaluating on test set...")
    eval_result = evaluate_model(
        train_result["best_model"],
        feature_data["X_test"],
        feature_data["y_test"],
        feature_data["feature_names"]
    )

    print(f"\nTest Metrics:")
    print(f"  Accuracy:  {eval_result['accuracy']:.4f}")
    print(f"  Precision: {eval_result['precision']:.4f}")
    print(f"  Recall:    {eval_result['recall']:.4f}")
    print(f"  F1-Score:  {eval_result['f1_score']:.4f}")
    print(f"  ROC-AUC:   {eval_result['roc_auc']:.4f}")
    print(f"\nModel saved to: {train_result['model_path']}")

    return {"train": train_result, "evaluation": eval_result}


def run_predict():
    """Run prediction on sample data"""
    from src.model_predict import load_model, load_metadata, predict_single
    from src.features import build_features
    import pandas as pd

    try:
        model = load_model()
        metadata = load_metadata()
    except FileNotFoundError:
        print("Error: No trained model found. Run --train first.")
        return

    # Load some test data
    feature_data = build_features()
    X_test = feature_data["X_test"]
    y_test = feature_data["y_test"]
    feature_names = feature_data["feature_names"]

    # Predict on first 10 test samples
    print("Predicting on 10 test samples:\n")
    for i in range(min(10, len(X_test))):
        features = dict(zip(feature_names, X_test[i]))
        result = predict_single(features, model)
        actual = int(y_test[i])
        status = "✅" if result["prediction"] == actual else "❌"
        print(f"  {status} Predicted: {result['label']} (prob={result['probability']:.3f}), Actual: {'FRAUD' if actual else 'LEGIT'}")


def run_serve():
    """Start FastAPI server"""
    import uvicorn
    from config.settings import APP_ENV

    port = 8000
    print(f"Starting server on port {port}...")
    print(f"Dashboard: http://localhost:{port}")
    print(f"API Docs: http://localhost:{port}/docs")
    uvicorn.run("backend.app:app", host="0.0.0.0", port=port, reload=APP_ENV == "development")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline DataOps - Credit Card Fraud")
    parser.add_argument("--full", action="store_true", help="Run full pipeline (Bronze→Silver→Gold→Load)")
    parser.add_argument("--train", action="store_true", help="Train ML models")
    parser.add_argument("--predict", action="store_true", help="Run prediction")
    parser.add_argument("--serve", action="store_true", help="Start API server")
    parser.add_argument("--sample", type=int, default=None, help="Sample size for demo")

    args = parser.parse_args()

    if args.serve:
        run_serve()
    elif args.train:
        run_train()
    elif args.predict:
        run_predict()
    elif args.full:
        run_full_pipeline(sample_size=args.sample)
    else:
        parser.print_help()
