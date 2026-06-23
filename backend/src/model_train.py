"""
Model Training Module
Trains multiple models and selects the best by F2-Score (recall-heavy).
Threshold is tuned on a held-out validation set (never on test).
"""

import json
import time
from datetime import datetime

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, fbeta_score, precision_recall_curve
from sklearn.model_selection import StratifiedKFold, cross_val_score

from backend.config.logging_config import setup_logging
from backend.config.settings import MODEL_DECISION_THRESHOLD, MODELS_DIR

logger = setup_logging("model_train")


def _tune_threshold_on_val(model, X_val, y_val, beta: float = 2.0):
    """
    Find the F-beta-maximizing threshold on a held-out validation set.

    For fraud detection, beta > 1 penalizes missed fraud (false negatives)
    more than false alarms (false positives).  beta=2 weights recall 2x
    more than precision.
    """
    y_prob = (
        model.predict_proba(X_val)[:, 1]
        if hasattr(model, "predict_proba")
        else model.predict(X_val).astype(float)
    )
    precisions, recalls, thresholds = precision_recall_curve(y_val, y_prob)
    if len(thresholds) == 0:
        return MODEL_DECISION_THRESHOLD, 0.0
    beta_sq = beta ** 2
    f_betas = (
        (1 + beta_sq)
        * (precisions[:-1] * recalls[:-1])
        / (beta_sq * precisions[:-1] + recalls[:-1] + 1e-10)
    )
    best_idx = int(np.argmax(f_betas))
    return float(thresholds[best_idx]), float(f_betas[best_idx])


def train_models(
    X_train,
    y_train,
    feature_names=None,
    category_mapping=None,
    gender_mapping=None,
    scaler_path=None,
    category_fraud_rate_map=None,
    global_fraud_rate=None,
    X_val=None,
    y_val=None,
):
    """
    Train 3 models with stratified cross-validation, select best by F2,
    then tune decision threshold on the validation set (F-beta, beta=2).

    Args:
        X_train: Training features
        y_train: Training labels
        feature_names: List of feature names for metadata
        category_mapping: Dict mapping category names to encoded integers
        X_val: Validation features (for threshold tuning)
        y_val: Validation labels (for threshold tuning)

    Returns:
        dict with best_model, best_model_type, best_f1, all_results,
        tuned_threshold, duration
    """
    start_time = time.time()

    # Calculate class weight ratio for imbalanced data
    n_neg = np.sum(y_train == 0)
    n_pos = np.sum(y_train == 1)
    scale_pos = n_neg / n_pos if n_pos > 0 else 1

    logger.info(f"Class balance: {n_neg} legit, {n_pos} fraud, ratio: {scale_pos:.1f}")

    # Define models
    models = {
        "LogisticRegression": LogisticRegression(
            C=1.0, class_weight="balanced", max_iter=1000, random_state=42
        ),
        "RandomForest": RandomForestClassifier(
            n_estimators=200,
            max_depth=20,
            min_samples_split=5,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        ),
        "XGBClassifier": None,  # Set below if xgboost available
    }

    # Try to add XGBoost
    try:
        from xgboost import XGBClassifier

        models["XGBClassifier"] = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            scale_pos_weight=scale_pos,
            eval_metric="logloss",
            random_state=42,
            use_label_encoder=False,
        )
    except ImportError:
        logger.warning("XGBoost not available, skipping")
        del models["XGBClassifier"]

    def threshold_fbeta(estimator, X, y, beta=2.0):
        """Score with F-beta (beta=2) using the decision threshold."""
        if hasattr(estimator, "predict_proba"):
            probabilities = estimator.predict_proba(X)[:, 1]
            predictions = (probabilities >= MODEL_DECISION_THRESHOLD).astype(int)
        else:
            predictions = estimator.predict(X)
        return fbeta_score(y, predictions, beta=beta)

    # Stratified K-Fold for stable class distribution across folds
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    # Train and evaluate each model
    all_results = {}
    best_model = None
    best_model_type = None
    best_f1 = -1

    for name, model in models.items():
        if model is None:
            continue

        logger.info(f"Training {name}...")
        model_start = time.time()

        # Stratified cross-validation
        cv_scores = cross_val_score(
            model,
            X_train,
            y_train,
            cv=skf,
            scoring=threshold_fbeta,
            n_jobs=-1,
        )

        mean_f1 = cv_scores.mean()
        std_f1 = cv_scores.std()

        # Fit on full training set
        model.fit(X_train, y_train)

        model_duration = time.time() - model_start

        result = {
            "model_type": name,
            "cv_f1_mean": round(float(mean_f1), 4),
            "cv_f1_std": round(float(std_f1), 4),
            "cv_scores": [round(float(s), 4) for s in cv_scores],
            "training_duration_seconds": round(model_duration, 2),
        }

        all_results[name] = result
        logger.info(
            f"{name}: F2={mean_f1:.4f} (+/- {std_f1:.4f}), time={model_duration:.1f}s"
        )

        if mean_f1 > best_f1:
            best_f1 = mean_f1
            best_model = model
            best_model_type = name

    if best_model is None:
        raise ValueError("No model was trained successfully")

    # --- Tune threshold on validation set (not test) ---
    tuned_threshold = MODEL_DECISION_THRESHOLD
    tuned_f1 = 0.0
    if X_val is not None and y_val is not None and len(y_val) > 0:
        tuned_threshold, tuned_f1 = _tune_threshold_on_val(best_model, X_val, y_val)
        logger.info(
            "Threshold tuned on validation: %.4f (F2=%.4f, default was %.4f)",
            tuned_threshold,
            tuned_f1,
            MODEL_DECISION_THRESHOLD,
        )
    else:
        logger.info("No validation data provided, using default threshold %.4f", MODEL_DECISION_THRESHOLD)

    # Save best model
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / "best_model.joblib"
    joblib.dump(best_model, model_path)

    # Save metadata (only clean, relevant hyperparameters)
    raw_params = best_model.get_params()
    # Filter out None values and internal sklearn params for readability
    clean_params = {
        k: v for k, v in raw_params.items()
        if v is not None and not k.startswith("_")
    }

    metadata = {
        "best_model_type": best_model_type,
        "best_f1_cv": round(float(best_f1), 4),  # backward compat (now F2)
        "best_f2_cv": round(float(best_f1), 4),
        "scoring": "f2",
        "models_compared": list(all_results.keys()),
        "all_results": all_results,
        "feature_names": feature_names,
        "n_features": len(feature_names) if feature_names else 0,
        "n_train_samples": len(X_train),
        "class_distribution": {
            int(k): int(v)
            for k, v in zip(*np.unique(y_train, return_counts=True), strict=False)
        },
        "hyperparameters": clean_params,
        "model_path": str(model_path),
        "trained_at": datetime.now().isoformat(),
        "decision_threshold": round(tuned_threshold, 4),
        "default_threshold": MODEL_DECISION_THRESHOLD,
        "tuned_f1": round(tuned_f1, 4),  # backward compat (now F2)
        "tuned_f2": round(tuned_f1, 4),
        "scaler_path": scaler_path,
        "category_fraud_rate_map": category_fraud_rate_map or {},
        "global_fraud_rate": global_fraud_rate,
        "category_mapping": category_mapping if category_mapping is not None else {},
        "gender_mapping": gender_mapping
        if gender_mapping is not None
        else {"M": 0, "F": 1},
    }

    metadata_path = MODELS_DIR / "model_metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

    duration = time.time() - start_time

    logger.info(
        f"Best model: {best_model_type} (F2={best_f1:.4f}), "
        f"threshold={tuned_threshold:.4f}, saved to {model_path}"
    )

    return {
        "best_model": best_model,
        "best_model_type": best_model_type,
        "best_f1_cv": round(float(best_f1), 4),  # backward compat (now F2)
        "best_f2_cv": round(float(best_f1), 4),
        "all_results": all_results,
        "model_path": str(model_path),
        "metadata": metadata,
        "tuned_threshold": round(tuned_threshold, 4),
        "duration_seconds": round(duration, 2),
    }


def load_model(model_path: str | None = None):
    """Load saved model"""
    from pathlib import Path

    path = Path(model_path) if model_path else MODELS_DIR / "best_model.joblib"
    if not path.exists():
        raise FileNotFoundError(f"Model not found: {path}")

    return joblib.load(path)


def load_metadata():
    """Load model metadata"""
    metadata_path = MODELS_DIR / "model_metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    with open(metadata_path) as f:
        return json.load(f)
