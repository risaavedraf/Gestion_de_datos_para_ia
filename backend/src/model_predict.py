"""
Model Inference Module
Single and batch predictions with risk classification
"""
import numpy as np
import pandas as pd
from datetime import datetime
from config.settings import MODELS_DIR, RISK_LOW, RISK_HIGH
from config.logging_config import setup_logging
from src.utils import get_risk_level
from src.model_train import load_model, load_metadata

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

    # Build feature vector
    feature_vector = []
    for fname in feature_names:
        if fname in features:
            feature_vector.append(features[fname])
        else:
            # Use defaults for missing features
            defaults = {
                "amt": 0.0, "trans_hour": 12, "trans_day_of_week": 0,
                "trans_month": 1, "distance_km": 0.0, "city_pop": 0,
                "age_at_transaction": 30, "category_encoded": 0, "gender_encoded": 0
            }
            feature_vector.append(defaults.get(fname, 0))
            logger.warning(f"Missing feature '{fname}', using default")

    X = np.array([feature_vector])

    # Predict
    probability = float(model.predict_proba(X)[0][1]) if hasattr(model, "predict_proba") else float(model.predict(X)[0])
    # Use 0.3 threshold for fraud detection (better recall on imbalanced data)
    prediction = 1 if probability >= 0.3 else 0
    risk_level = get_risk_level(probability, RISK_LOW, RISK_HIGH)

    result = {
        "prediction": prediction,
        "probability": round(probability, 4),
        "risk_level": risk_level,
        "label": "FRAUD" if prediction == 1 else "LEGIT",
        "timestamp": datetime.now().isoformat()
    }

    logger.info(f"Prediction: {result['label']} (prob={probability:.4f}, risk={risk_level})")

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

    probabilities = model.predict_proba(X)[:, 1] if hasattr(model, "predict_proba") else model.predict(X).astype(float)
    # Use 0.3 threshold for fraud detection (better recall on imbalanced data)
    predictions = (probabilities >= 0.3).astype(int)

    result_df = df.copy()
    result_df["prediction"] = predictions
    result_df["probability"] = probabilities.round(4)
    result_df["risk_level"] = [get_risk_level(p, RISK_LOW, RISK_HIGH) for p in probabilities]
    result_df["label"] = result_df["prediction"].map({1: "FRAUD", 0: "LEGIT"})

    logger.info(f"Batch prediction: {len(result_df)} rows, {result_df['prediction'].sum()} fraud detected")

    return result_df

def prepare_transaction_for_prediction(transaction: dict):
    """
    Convert raw transaction data to model features.

    Args:
        transaction: dict with raw transaction fields (amt, trans_hour, category, gender, etc.)

    Returns:
        dict with encoded features ready for prediction
    """
    # Load mappings from model metadata (ensures consistency with training)
    metadata = load_metadata()
    category_mapping = metadata.get("category_mapping", {})
    gender_mapping = metadata.get("gender_mapping", {"M": 0, "F": 1})

    features = {
        "amt": float(transaction.get("amt", 0)),
        "trans_hour": int(transaction.get("trans_hour", 12)),
        "trans_day_of_week": int(transaction.get("trans_day_of_week", 0)),
        "trans_month": int(transaction.get("trans_month", 1)),
        "distance_km": float(transaction.get("distance_km", 0)),
        "city_pop": int(transaction.get("city_pop", 0)),
        "age_at_transaction": int(transaction.get("age_at_transaction", 30)),
        "category_encoded": category_mapping.get(transaction.get("category", ""), 0),
        "gender_encoded": gender_mapping.get(transaction.get("gender", "M"), 0)
    }

    return features
