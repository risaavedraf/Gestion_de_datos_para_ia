import pandas as pd
import json
from datetime import datetime
from pathlib import Path
from config.settings import GOLD_DIR, REJECTED_DIR, DATABASE_URL
from config.logging_config import setup_logging
from src.utils import generate_run_id, mask_pii

logger = setup_logging("loader")


def get_engine():
    """Create SQLAlchemy engine"""
    from sqlalchemy import create_engine

    return create_engine(DATABASE_URL)


def create_tables(engine=None):
    """Create all required tables if they don't exist"""
    if engine is None:
        engine = get_engine()

    from sqlalchemy import text

    ddl = """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id VARCHAR(64) PRIMARY KEY,
        gender VARCHAR(1),
        city VARCHAR(100),
        state VARCHAR(2),
        zip VARCHAR(10),
        city_pop INTEGER,
        job VARCHAR(200),
        age_at_transaction INTEGER
    );

    CREATE TABLE IF NOT EXISTS merchants (
        merchant_id SERIAL PRIMARY KEY,
        merchant_name VARCHAR(200) NOT NULL,
        category VARCHAR(50) NOT NULL,
        UNIQUE(merchant_name, category)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        trans_num VARCHAR(64) PRIMARY KEY,
        customer_id VARCHAR(64) REFERENCES customers(customer_id),
        merchant_id INTEGER REFERENCES merchants(merchant_id),
        amt DECIMAL(12,2),
        trans_date_trans_time TIMESTAMP,
        trans_hour INTEGER,
        trans_day_of_week INTEGER,
        trans_month INTEGER,
        distance_km DECIMAL(10,2),
        is_fraud INTEGER,
        unix_time BIGINT,
        merch_lat DECIMAL(10,6),
        merch_long DECIMAL(10,6),
        category VARCHAR(50),
        city VARCHAR(100),
        state VARCHAR(2)
    );

    CREATE TABLE IF NOT EXISTS pipeline_logs (
        log_id SERIAL PRIMARY KEY,
        run_id VARCHAR(36),
        stage VARCHAR(20),
        status VARCHAR(20),
        records_in INTEGER,
        records_out INTEGER,
        records_rejected INTEGER,
        duration_seconds DECIMAL(10,2),
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS rejected_records (
        reject_id SERIAL PRIMARY KEY,
        run_id VARCHAR(36),
        trans_num VARCHAR(64),
        original_data JSONB,
        rejection_reason TEXT,
        stage VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS model_predictions (
        prediction_id SERIAL PRIMARY KEY,
        trans_num VARCHAR(64) REFERENCES transactions(trans_num),
        model_version VARCHAR(32),
        model_type VARCHAR(32),
        prediction INTEGER NOT NULL,
        probability DECIMAL(6,4),
        risk_level VARCHAR(16),
        features_used JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS model_training_runs (
        training_run_id SERIAL PRIMARY KEY,
        model_type VARCHAR(32),
        hyperparameters JSONB,
        cv_scores JSONB,
        test_metrics JSONB,
        feature_importance JSONB,
        model_path VARCHAR(256),
        training_duration_seconds DECIMAL(10,2),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """

    with engine.connect() as conn:
        for statement in ddl.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.commit()

    logger.info("Database tables created/verified")


def load(sample_size: int = None) -> dict:
    """
    Load Gold data into PostgreSQL with deduplication.

    Args:
        sample_size: If set, only load first N rows.

    Returns:
        dict with run_id, customers, merchants, transactions, rejected, status, duration
    """
    run_id = generate_run_id()
    start_time = datetime.now()

    engine = get_engine()
    create_tables(engine)

    # Load Gold data
    gold_path = GOLD_DIR / "fraud_gold.parquet"
    if not gold_path.exists():
        logger.error("Gold data not found")
        return {"run_id": run_id, "status": "error", "error": "Gold data not found"}

    df = pd.read_parquet(gold_path)
    if sample_size and sample_size < len(df):
        df = df.head(sample_size)

    logger.info(f"Loading {len(df)} records to PostgreSQL")

    # 1. Insert customers (deduplicated by cc_num_hashed)
    customer_cols = [
        "cc_num_masked",
        "gender",
        "city",
        "state",
        "zip",
        "city_pop",
        "job",
        "age_at_transaction",
    ]
    customers_df = df[customer_cols].drop_duplicates(subset=["cc_num_masked"]).copy()
    customers_df = customers_df.rename(columns={"cc_num_masked": "customer_id"})

    from sqlalchemy import text

    with engine.connect() as conn:
        # Bulk insert customers
        customer_records = customers_df.to_dict(orient="records")
        if customer_records:
            conn.execute(
                text("""
                INSERT INTO customers (customer_id, gender, city, state, zip, city_pop, job, age_at_transaction)
                VALUES (:customer_id, :gender, :city, :state, :zip, :city_pop, :job, :age_at_transaction)
                ON CONFLICT (customer_id) DO NOTHING
                """),
                customer_records,
            )
        conn.commit()

    logger.info(f"Inserted {len(customers_df)} customers")

    # 2. Insert merchants (deduplicated by name + category)
    merchants_df = df[["merchant", "category"]].drop_duplicates().copy()
    merchants_df = merchants_df.rename(columns={"merchant": "merchant_name"})

    merchant_id_map = {}
    with engine.connect() as conn:
        # Bulk insert merchants
        merchant_records = merchants_df.to_dict(orient="records")
        if merchant_records:
            conn.execute(
                text("""
                INSERT INTO merchants (merchant_name, category)
                VALUES (:merchant_name, :category)
                ON CONFLICT (merchant_name, category) DO UPDATE SET merchant_name = EXCLUDED.merchant_name
                """),
                merchant_records,
            )
        conn.commit()

        # Query back to build ID map
        result = conn.execute(text("SELECT merchant_id, merchant_name, category FROM merchants"))
        for row in result:
            merchant_id_map[row[1]] = row[0]

    logger.info(f"Inserted/updated {len(merchants_df)} merchants")

    # 3. Insert transactions
    df["customer_id"] = df["cc_num_masked"]
    df["merchant_id"] = df["merchant"].map(merchant_id_map)

    trans_cols = [
        "trans_num",
        "customer_id",
        "merchant_id",
        "amt",
        "trans_date_trans_time",
        "trans_hour",
        "trans_day_of_week",
        "trans_month",
        "distance_km",
        "is_fraud",
        "unix_time",
        "merch_lat",
        "merch_long",
        "category",
        "city",
        "state",
    ]

    trans_df = df[trans_cols].copy()

    with engine.connect() as conn:
        # Bulk insert transactions
        cols = ["trans_num", "customer_id", "merchant_id", "amt", "trans_date_trans_time",
                "trans_hour", "trans_day_of_week", "trans_month", "distance_km", "is_fraud",
                "unix_time", "merch_lat", "merch_long", "category", "city", "state"]

        insert_stmt = text("""
            INSERT INTO transactions (trans_num, customer_id, merchant_id, amt, trans_date_trans_time,
                trans_hour, trans_day_of_week, trans_month, distance_km, is_fraud,
                unix_time, merch_lat, merch_long, category, city, state)
            VALUES (:trans_num, :customer_id, :merchant_id, :amt, :trans_date_trans_time,
                :trans_hour, :trans_day_of_week, :trans_month, :distance_km, :is_fraud,
                :unix_time, :merch_lat, :merch_long, :category, :city, :state)
            ON CONFLICT (trans_num) DO NOTHING
        """)

        records = trans_df[cols].to_dict(orient="records")
        conn.execute(insert_stmt, records)
        conn.commit()

    logger.info(f"Attempted {len(trans_df)} transaction inserts")

    # 4. Insert rejected records
    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    rejected_count = 0
    if rejected_path.exists():
        rejected_df = pd.read_parquet(rejected_path)
        if sample_size and sample_size < len(rejected_df):
            rejected_df = rejected_df.head(sample_size)

        with engine.connect() as conn:
            # Vectorized: build original_data column without iterrows
            exclude_cols = {"rejection_reason", "run_id"}
            original_data_series = rejected_df.apply(
                lambda row: json.dumps(
                    {k: v for k, v in row.items() if k not in exclude_cols},
                    default=str
                ),
                axis=1
            )
            rejected_records = [
                {
                    "run_id": run_id,
                    "trans_num": str(trans_num) if pd.notna(trans_num) else "unknown",
                    "original_data": orig_data,
                    "rejection_reason": str(reason) if pd.notna(reason) else "unknown",
                }
                for trans_num, orig_data, reason in zip(
                    rejected_df.get("trans_num", pd.Series(dtype=str)),
                    original_data_series,
                    rejected_df.get("rejection_reason", pd.Series(dtype=str)),
                )
            ]
            if rejected_records:
                conn.execute(
                    text("""
                    INSERT INTO rejected_records (run_id, trans_num, original_data, rejection_reason, stage)
                    VALUES (:run_id, :trans_num, :original_data, :rejection_reason, 'validation')
                """),
                    rejected_records,
                )
            conn.commit()
        rejected_count = len(rejected_df)

    # 5. Log pipeline run
    with engine.connect() as conn:
        conn.execute(
            text("""
            INSERT INTO pipeline_logs (run_id, stage, status, records_in, records_out, records_rejected, duration_seconds, details)
            VALUES (:run_id, 'loading', 'success', :records_in, :records_out, :records_rejected, :duration, 'Data loaded to PostgreSQL')
        """),
            {
                "run_id": run_id,
                "records_in": len(df),
                "records_out": len(trans_df),
                "records_rejected": rejected_count,
                "duration": round((datetime.now() - start_time).total_seconds(), 2),
            },
        )
        conn.commit()

    duration = (datetime.now() - start_time).total_seconds()

    result = {
        "run_id": run_id,
        "status": "success",
        "customers_inserted": len(customers_df),
        "merchants_inserted": len(merchants_df),
        "transactions_attempted": len(trans_df),
        "rejected_inserted": rejected_count,
        "duration_seconds": round(duration, 2),
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(
        f"Loading complete: {len(customers_df)} customers, {len(merchants_df)} merchants, {len(trans_df)} transactions"
    )

    return result
