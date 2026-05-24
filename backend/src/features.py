"""
Feature Engineering for ML Pipeline
Builds feature matrix from Gold layer data
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from backend.config.logging_config import setup_logging
from backend.config.settings import GOLD_DIR, MODELS_DIR

logger = setup_logging("features")

# Base feature columns (loaded from parquet / imputed)
NUMERIC_FEATURES = [
    "amt",
    "trans_hour",
    "trans_day_of_week",
    "trans_month",
    "distance_km",
    "city_pop",
    "age_at_transaction",
]
# Derived interaction features (computed, not loaded)
INTERACTION_FEATURES = [
    "amt_per_city_pop",
    "distance_x_amt",
    "hour_is_night",
]
CATEGORICAL_FEATURES = ["category", "gender"]
TARGET = "is_fraud"


def _split_chronological(
    df: pd.DataFrame, test_size: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split dataset by time order to avoid future-leakage."""
    split_idx = int(len(df) * (1 - test_size))
    split_idx = max(1, min(split_idx, len(df) - 1))
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()
    return train_df, test_df


def build_features(
    input_path: str | None = None,
    test_size: float = 0.2,
    random_state: int = 42,  # kept for API compatibility
):
    """
    Build feature matrix from Gold layer data.

    Returns:
        dict with X_train, X_test, y_train, y_test, feature_names, class_distribution,
        category_mapping, and gender_mapping
    """
    del random_state  # Not used in chronological split; kept for compatibility.

    source = Path(input_path) if input_path else GOLD_DIR / "fraud_gold.parquet"
    if not source.exists():
        logger.error(f"Gold data not found: {source}")
        raise FileNotFoundError(f"Gold data not found: {source}")

    df = pd.read_parquet(source)
    logger.info(f"Loaded {len(df)} rows from Gold")

    required = (
        NUMERIC_FEATURES + CATEGORICAL_FEATURES + [TARGET, "trans_date_trans_time"]
    )
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df = df.copy()
    df["trans_date_trans_time"] = pd.to_datetime(
        df["trans_date_trans_time"], errors="coerce"
    )
    df = (
        df.dropna(subset=["trans_date_trans_time"])
        .sort_values("trans_date_trans_time")
        .reset_index(drop=True)
    )

    if len(df) < 2:
        raise ValueError(
            "Not enough valid rows to create chronological train/test split"
        )

    train_df, test_df = _split_chronological(df, test_size=test_size)

    # Fit imputers on train only
    for col in NUMERIC_FEATURES:
        train_df[col] = pd.to_numeric(train_df[col], errors="coerce")
        test_df[col] = pd.to_numeric(test_df[col], errors="coerce")

        median_val = train_df[col].median()
        train_df[col] = train_df[col].fillna(median_val)
        test_df[col] = test_df[col].fillna(median_val)

    for col in CATEGORICAL_FEATURES:
        train_df[col] = train_df[col].astype(str).str.strip()
        test_df[col] = test_df[col].astype(str).str.strip()

        mode_val = (
            train_df[col].mode().iloc[0] if not train_df[col].mode().empty else ""
        )
        train_df[col] = train_df[col].replace({"": mode_val}).fillna(mode_val)
        test_df[col] = test_df[col].replace({"": mode_val}).fillna(mode_val)

    train_df["category"] = train_df["category"].str.lower()
    test_df["category"] = test_df["category"].str.lower()
    train_df["gender"] = train_df["gender"].str.upper()
    test_df["gender"] = test_df["gender"].str.upper()

    # Fit encoders on train only
    category_mapping = {
        cat: idx for idx, cat in enumerate(sorted(train_df["category"].unique()))
    }
    gender_mapping = {"M": 0, "F": 1}

    train_df["category_encoded"] = (
        train_df["category"]
        .apply(lambda value: category_mapping.get(str(value), -1))
        .astype(int)
    )
    test_df["category_encoded"] = (
        test_df["category"]
        .apply(lambda value: category_mapping.get(str(value), -1))
        .astype(int)
    )
    train_df["gender_encoded"] = (
        train_df["gender"]
        .apply(lambda value: gender_mapping.get(str(value), -1))
        .astype(int)
    )
    test_df["gender_encoded"] = (
        test_df["gender"]
        .apply(lambda value: gender_mapping.get(str(value), -1))
        .astype(int)
    )

    # --- Interaction features (computed per-row, no leakage) ---
    for df_split in (train_df, test_df):
        df_split["amt_per_city_pop"] = df_split["amt"] / (df_split["city_pop"] + 1)
        df_split["distance_x_amt"] = df_split["distance_km"] * df_split["amt"]
        df_split["hour_is_night"] = (
            (df_split["trans_hour"] < 6) | (df_split["trans_hour"] >= 22)
        ).astype(int)

    # --- Category fraud rate (TRAIN ONLY to avoid leakage) ---
    cat_fraud = train_df.groupby("category")[TARGET].mean()
    category_fraud_rate_map = {
        cat: float(rate) for cat, rate in cat_fraud.to_dict().items()
    }
    global_fraud_rate = float(train_df[TARGET].mean())
    for df_split in (train_df, test_df):
        df_split["category_fraud_rate"] = (
            df_split["category"]
            .map(category_fraud_rate_map)
            .fillna(global_fraud_rate)
        )

    feature_names = NUMERIC_FEATURES + INTERACTION_FEATURES + [
        "category_fraud_rate",
        "category_encoded",
        "gender_encoded",
    ]

    X_train = train_df[feature_names].to_numpy()
    X_test = test_df[feature_names].to_numpy()
    y_train = (
        pd.to_numeric(train_df[TARGET], errors="coerce")
        .fillna(0)
        .astype(int)
        .to_numpy()
    )
    y_test = (
        pd.to_numeric(test_df[TARGET], errors="coerce").fillna(0).astype(int).to_numpy()
    )

    # --- StandardScaler (fit on train only) ---
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    scaler_path = MODELS_DIR / "scaler.joblib"
    joblib.dump(scaler, scaler_path)
    logger.info("Scaler saved to %s (fit on %d train rows)", scaler_path, len(X_train))

    unique, counts = np.unique(y_train, return_counts=True)
    class_dist = dict(zip(unique.astype(int), counts.astype(int), strict=False))

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
        "category_mapping": category_mapping,
        "gender_mapping": gender_mapping,
        "scaler": scaler,
        "scaler_path": str(scaler_path),
        "category_fraud_rate_map": category_fraud_rate_map,
        "global_fraud_rate": global_fraud_rate,
    }

    logger.info(
        "Features built: %s features, %s train, %s test (chronological split)",
        len(feature_names),
        len(X_train),
        len(X_test),
    )

    return result
