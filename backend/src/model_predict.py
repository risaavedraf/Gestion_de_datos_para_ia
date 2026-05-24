"""
Model Inference Module
Single and batch predictions with risk classification
"""

from datetime import datetime

import joblib
import numpy as np
import pandas as pd

from backend.config.logging_config import setup_logging
from backend.config.settings import MODEL_DECISION_THRESHOLD, RISK_HIGH, RISK_LOW
from backend.src.model_train import load_metadata, load_model
from backend.src.utils import get_risk_level

logger = setup_logging("model_predict")


def predict_single(features: dict, model=None):
    """
    Predict fraud for a single transaction.

    Args:
        features: dict with keys matching feature_names
                  {amt, trans_hour, trans_day_of_week, trans_month,
                   distance_km, city_pop, age_at_transaction,
                   category_encoded, gender_encoded}
        model: Pre-loaded model (optional, loads from disk if None)

    Returns:
        dict with prediction (0/1), probability, risk_level
    """
    if model is None:
        model = load_model()

    metadata = load_metadata()
    feature_names = metadata.get("feature_names", [])

    if not feature_names:
        raise ValueError("No feature names in model metadata")

    # Build feature vector (strict: no silent defaults)
    missing_features = [fname for fname in feature_names if fname not in features]
    if missing_features:
        raise ValueError(f"Missing feature values: {missing_features}")

    feature_vector = [features[fname] for fname in feature_names]

    X = np.array([feature_vector])

    # Apply scaler if available
    scaler_path = metadata.get("scaler_path")
    if scaler_path:
        scaler = joblib.load(scaler_path)
        X = scaler.transform(X)

    # Predict
    probability = (
        float(model.predict_proba(X)[0][1])
        if hasattr(model, "predict_proba")
        else float(model.predict(X)[0])
    )
    decision_threshold = float(
        metadata.get("decision_threshold", MODEL_DECISION_THRESHOLD)
    )
    prediction = 1 if probability >= decision_threshold else 0
    risk_level = get_risk_level(probability, RISK_LOW, RISK_HIGH)

    result = {
        "prediction": prediction,
        "probability": round(probability, 4),
        "risk_level": risk_level,
        "label": "FRAUD" if prediction == 1 else "LEGIT",
        "decision_threshold": decision_threshold,
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(
        f"Prediction: {result['label']} (prob={probability:.4f}, risk={risk_level})"
    )

    return result


def predict_batch(df: pd.DataFrame, model=None):
    """
    Predict fraud for a batch of transactions.

    Args:
        df: DataFrame with feature columns
        model: Pre-loaded model (optional)

    Returns:
        DataFrame with prediction, probability, risk_level columns added
    """
    if model is None:
        model = load_model()

    metadata = load_metadata()
    feature_names = metadata.get("feature_names", [])

    if not feature_names:
        raise ValueError("No feature names in model metadata")

    # Check missing columns
    missing = [f for f in feature_names if f not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    X = df[feature_names].values

    # Apply scaler if available
    scaler_path = metadata.get("scaler_path")
    if scaler_path:
        scaler = joblib.load(scaler_path)
        X = scaler.transform(X)

    probabilities = (
        model.predict_proba(X)[:, 1]
        if hasattr(model, "predict_proba")
        else model.predict(X).astype(float)
    )
    decision_threshold = float(
        metadata.get("decision_threshold", MODEL_DECISION_THRESHOLD)
    )
    predictions = (probabilities >= decision_threshold).astype(int)

    result_df = df.copy()
    result_df["prediction"] = predictions
    result_df["probability"] = probabilities.round(4)
    result_df["risk_level"] = [
        get_risk_level(p, RISK_LOW, RISK_HIGH) for p in probabilities
    ]
    result_df["label"] = result_df["prediction"].apply(
        lambda value: "FRAUD" if int(value) == 1 else "LEGIT"
    )

    logger.info(
        f"Batch prediction: {len(result_df)} rows, {result_df['prediction'].sum()} fraud detected"
    )

    return result_df


def prepare_transaction_for_prediction(transaction: dict):
    """
    Convert raw transaction data to model features.

    Computes base numeric features, categorical encodings, and derived
    interaction features (amt_per_city_pop, distance_x_amt, hour_is_night,
    category_fraud_rate) to match the training feature vector.

    Args:
        transaction: dict with raw transaction fields (amt, trans_hour, category, gender, etc.)

    Returns:
        dict with encoded features ready for prediction
    """
    metadata = load_metadata()
    category_mapping = metadata.get("category_mapping", {})
    gender_mapping = metadata.get("gender_mapping", {"M": 0, "F": 1})
    category_fraud_rate_map = metadata.get("category_fraud_rate_map", {})
    global_fraud_rate = float(metadata.get("global_fraud_rate", 0.0))

    category_value = str(transaction.get("category", "")).strip().lower()
    gender_value = str(transaction.get("gender", "")).strip().upper()

    if category_value not in category_mapping:
        raise ValueError(f"Unknown category: '{category_value}'")
    if gender_value not in gender_mapping:
        raise ValueError(f"Unknown gender: '{gender_value}'")

    amt = float(transaction.get("amt", 0))
    city_pop = int(transaction.get("city_pop", 0))
    distance_km = float(transaction.get("distance_km", 0))
    trans_hour = int(transaction.get("trans_hour", 12))

    features = {
        "amt": amt,
        "trans_hour": trans_hour,
        "trans_day_of_week": int(transaction.get("trans_day_of_week", 0)),
        "trans_month": int(transaction.get("trans_month", 1)),
        "distance_km": distance_km,
        "city_pop": city_pop,
        "age_at_transaction": int(transaction.get("age_at_transaction", 30)),
        # Interaction features
        "amt_per_city_pop": amt / (city_pop + 1),
        "distance_x_amt": distance_km * amt,
        "hour_is_night": 1 if trans_hour < 6 or trans_hour >= 22 else 0,
        # Category fraud rate (precomputed from training data)
        "category_fraud_rate": float(
            category_fraud_rate_map.get(category_value, global_fraud_rate)
        ),
        # Categorical encodings
        "category_encoded": int(category_mapping[category_value]),
        "gender_encoded": int(gender_mapping[gender_value]),
    }

    return features
