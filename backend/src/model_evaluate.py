"""
Model Evaluation Module
Computes metrics, confusion matrix, ROC curve, feature importance
"""

import json
from datetime import datetime

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)

from backend.config.logging_config import setup_logging
from backend.config.settings import MODEL_DECISION_THRESHOLD, REPORTS_DIR

logger = setup_logging("model_evaluate")


def evaluate_model(
    model, X_test, y_test, feature_names=None, threshold: float | None = None
):
    """
    Evaluate model on test set.

    Args:
        model: Trained sklearn-compatible model
        X_test: Test features
        y_test: Test labels
        feature_names: Feature names for importance

    Returns:
        dict with all evaluation metrics
    """
    # Predictions
    decision_threshold = (
        threshold if threshold is not None else MODEL_DECISION_THRESHOLD
    )
    y_prob = (
        model.predict_proba(X_test)[:, 1]
        if hasattr(model, "predict_proba")
        else model.predict(X_test).astype(float)
    )
    y_pred = (y_prob >= decision_threshold).astype(int)

    # Basic metrics
    accuracy = float(accuracy_score(y_test, y_pred))
    precision = float(precision_score(y_test, y_pred))
    recall = float(recall_score(y_test, y_pred))
    f1 = float(f1_score(y_test, y_pred))
    roc_auc = float(roc_auc_score(y_test, y_prob))

    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred)
    tn, fp, fn, tp = cm.ravel()

    # ROC curve
    fpr, tpr, roc_thresholds = roc_curve(y_test, y_prob)

    # Precision-Recall curve
    pr_precision, pr_recall, pr_thresholds = precision_recall_curve(y_test, y_prob)

    # Feature importance
    feature_importance = []
    if hasattr(model, "feature_importances_") and feature_names:
        importances = model.feature_importances_
        feature_importance = [
            {"name": name, "importance": round(float(imp), 4)}
            for name, imp in zip(feature_names, importances, strict=False)
        ]
        feature_importance.sort(key=lambda x: x["importance"], reverse=True)
    elif hasattr(model, "coef_") and feature_names:
        importances = np.abs(model.coef_[0])
        feature_importance = [
            {"name": name, "importance": round(float(imp), 4)}
            for name, imp in zip(feature_names, importances, strict=False)
        ]
        feature_importance.sort(key=lambda x: x["importance"], reverse=True)

    # Sample ROC points (for frontend chart, limit to 100 points)
    n_points = min(100, len(fpr))
    indices = np.linspace(0, len(fpr) - 1, n_points, dtype=int)

    result = {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "roc_auc": round(roc_auc, 4),
        "decision_threshold": decision_threshold,
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
        "roc_curve": {
            "fpr": [round(float(fpr[i]), 4) for i in indices],
            "tpr": [round(float(tpr[i]), 4) for i in indices],
            "auc": round(roc_auc, 4),
        },
        "pr_curve": {
            "precision": [
                round(float(pr_precision[i]), 4)
                for i in indices[: min(n_points, len(pr_precision))]
            ],
            "recall": [
                round(float(pr_recall[i]), 4)
                for i in indices[: min(n_points, len(pr_recall))]
            ],
        },
        "feature_importance": feature_importance,
        "n_test_samples": len(y_test),
        "evaluated_at": datetime.now().isoformat(),
    }

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / "evaluation_report.json"
    with open(report_path, "w") as f:
        json.dump(result, f, indent=2)

    logger.info(
        f"Evaluation: accuracy={accuracy:.4f}, precision={precision:.4f}, recall={recall:.4f}, f1={f1:.4f}, roc_auc={roc_auc:.4f}"
    )

    return result


def load_evaluation_report():
    """Load saved evaluation report"""
    report_path = REPORTS_DIR / "evaluation_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"Evaluation report not found: {report_path}")

    with open(report_path) as f:
        return json.load(f)
