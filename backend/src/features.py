"""
Feature Engineering for ML Pipeline
Builds feature matrix from Gold layer data
"""
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from config.settings import GOLD_DIR
from config.logging_config import setup_logging

logger = setup_logging("features")

# Feature columns used by the model
NUMERIC_FEATURES = ["amt", "trans_hour", "trans_day_of_week", "trans_month", "distance_km", "city_pop", "age_at_transaction"]
CATEGORICAL_FEATURES = ["category", "gender"]
TARGET = "is_fraud"

def build_features(input_path: str = None, test_size: float = 0.2, random_state: int = 42):
    """
    Build feature matrix from Gold layer data.

    Args:
        input_path: Path to Gold parquet. Defaults to GOLD_DIR/fraud_gold.parquet
        test_size: Proportion for test split
        random_state: Random seed for reproducibility

    Returns:
        dict with X_train, X_test, y_train, y_test, feature_names, class_distribution
    """
    from pathlib import Path

    source = Path(input_path) if input_path else GOLD_DIR / "fraud_gold.parquet"
    if not source.exists():
        logger.error(f"Gold data not found: {source}")
        raise FileNotFoundError(f"Gold data not found: {source}")

    df = pd.read_parquet(source)
    logger.info(f"Loaded {len(df)} rows from Gold")

    # Check required columns exist
    missing = [c for c in NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TARGET] if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # Handle missing values
    for col in NUMERIC_FEATURES:
        if df[col].isnull().any():
            median_val = df[col].median()
            df[col] = df[col].fillna(median_val)
            logger.warning(f"Filled {col} nulls with median: {median_val}")

    for col in CATEGORICAL_FEATURES:
        if df[col].isnull().any():
            mode_val = df[col].mode()[0]
            df[col] = df[col].fillna(mode_val)
            logger.warning(f"Filled {col} nulls with mode: {mode_val}")

    # Encode categorical features
    df_encoded = df.copy()

    # Category: label encoding
    category_mapping = {cat: idx for idx, cat in enumerate(df["category"].unique())}
    df_encoded["category_encoded"] = df_encoded["category"].map(category_mapping)

    # Gender: binary encoding (M=0, F=1)
    df_encoded["gender_encoded"] = (df_encoded["gender"] == "F").astype(int)

    # Build feature list
    feature_names = NUMERIC_FEATURES + ["category_encoded", "gender_encoded"]

    X = df_encoded[feature_names].values
    y = df_encoded[TARGET].values

    # Stratified split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )

    # Class distribution
    unique, counts = np.unique(y_train, return_counts=True)
    class_dist = dict(zip(unique.astype(int), counts.astype(int)))

    result = {
        "X_train": X_train,
        "X_test": X_test,
        "y_train": y_train,
        "y_test": y_test,
        "feature_names": feature_names,
        "n_features": len(feature_names),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "class_distribution": class_dist,
        "category_mapping": category_mapping
    }

    logger.info(f"Features built: {len(feature_names)} features, {len(X_train)} train, {len(X_test)} test")
    logger.info(f"Class distribution (train): {class_dist}")

    return result
