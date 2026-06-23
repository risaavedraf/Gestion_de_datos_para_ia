import json
import math
from datetime import datetime
from decimal import Decimal
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

from backend.config.logging_config import setup_logging
from backend.config.settings import GOLD_DIR, REJECTED_DIR
from backend.src.db import get_engine
from backend.src.utils import generate_run_id

logger = setup_logging("loader")

_REJECTED_DEDUP_INDEX = "uq_rejected_records_dedup"


def _replace_incompatible_rejected_dedup_index(conn) -> None:
    """Drop a legacy same-named index so canonical DDL can recreate it."""
    from sqlalchemy import text

    compatible = conn.execute(
        text("""
        SELECT
            index_meta.indisunique
            AND index_meta.indisvalid
            AND index_meta.indisready
            AND NOT index_meta.indisexclusion
            AND index_meta.indimmediate
            AND index_meta.indnkeyatts = 4
            AND index_meta.indnatts = 4
            AND index_meta.indpred IS NULL
            AND index_meta.indexprs IS NOT NULL
            AND access_method.amname = 'btree'
            AND ARRAY(
                SELECT pg_get_indexdef(index_meta.indexrelid, position, true)
                FROM generate_series(1, index_meta.indnkeyatts) AS position
                ORDER BY position
            ) = ARRAY[
                'COALESCE(trans_num, ''''::character varying)',
                'COALESCE(rejection_reason, ''''::text)',
                'COALESCE(stage, ''''::character varying)',
                'md5(COALESCE(original_data::text, ''null''::text))'
            ]::text[]
            AND index_meta.indoption = '0 0 0 0'::int2vector
        FROM pg_catalog.pg_class AS index_class
        JOIN pg_catalog.pg_namespace AS index_namespace
          ON index_namespace.oid = index_class.relnamespace
        JOIN pg_catalog.pg_index AS index_meta
          ON index_meta.indexrelid = index_class.oid
        JOIN pg_catalog.pg_class AS table_class
          ON table_class.oid = index_meta.indrelid
        JOIN pg_catalog.pg_am AS access_method
          ON access_method.oid = index_class.relam
        WHERE index_namespace.nspname = current_schema()
          AND table_class.relname = 'rejected_records'
          AND index_class.relname = :index_name
          AND index_class.relkind = 'i'
        """),
        {"index_name": _REJECTED_DEDUP_INDEX},
    ).scalar()
    if compatible is False:
        conn.execute(text(f"DROP INDEX IF EXISTS {_REJECTED_DEDUP_INDEX}"))


def create_tables(engine=None):
    """Create all required tables if they don't exist"""
    if engine is None:
        engine = get_engine()

    from sqlalchemy import text

    schema_path = Path(__file__).resolve().parents[1] / "config" / "schema.sql"
    ddl = schema_path.read_text(encoding="utf-8")

    with engine.connect() as conn:
        for statement in ddl.split(";"):
            statement = statement.strip()
            if statement:
                if f"CREATE UNIQUE INDEX IF NOT EXISTS {_REJECTED_DEDUP_INDEX}" in statement:
                    _replace_incompatible_rejected_dedup_index(conn)
                conn.execute(text(statement))
        conn.commit()

    logger.info("Database tables created/verified")


def _normalize_json_value(value):
    """Convert pandas/Python values into strict JSON-compatible values."""
    if isinstance(value, dict):
        return {str(key): _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    return value


def serialize_rejected_original_data(data: dict) -> str:
    """Serialize rejected row data as standards-compliant JSON."""
    normalized = _normalize_json_value(data)

    def encode(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            members = (
                f"{json.dumps(key)}:{encode(item)}" for key, item in value.items()
            )
            return "{" + ",".join(members) + "}"
        if isinstance(value, list):
            return "[" + ",".join(encode(item) for item in value) + "]"
        return json.dumps(value, allow_nan=False, default=str)

    return encode(normalized)


def _filter_incremental_rows(df: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    """Keep the cutoff timestamp as overlap; transaction PKs remove replays safely."""
    return df[df["unix_time"] >= cutoff]


def _insert_transaction_records(engine, records: list[dict]) -> int:
    """Insert transactions and return only rows confirmed inserted by PostgreSQL."""
    if not records:
        return 0

    from sqlalchemy import text

    insert_stmt = text("""
        INSERT INTO transactions (trans_num, customer_id, merchant_id, amt, trans_date_trans_time,
            trans_hour, trans_day_of_week, trans_month, distance_km, is_fraud,
            unix_time, merch_lat, merch_long, category, city, state)
        VALUES (:trans_num, :customer_id, :merchant_id, :amt, :trans_date_trans_time,
            :trans_hour, :trans_day_of_week, :trans_month, :distance_km, :is_fraud,
            :unix_time, :merch_lat, :merch_long, :category, :city, :state)
        ON CONFLICT (trans_num) DO NOTHING
    """)
    with engine.connect() as conn:
        result = conn.execute(insert_stmt, records)
        conn.commit()
    return max(result.rowcount or 0, 0)


def _load_rejected_records(*, engine, run_id: str, sample_size: int | None) -> int:
    """Load rejected rows independently so transaction progress cannot skip them."""
    from sqlalchemy import text

    rejected_path = REJECTED_DIR / "fraud_rejected.parquet"
    if not rejected_path.exists():
        return 0

    rejected_df = pd.read_parquet(rejected_path)
    if sample_size and sample_size < len(rejected_df):
        rejected_df = rejected_df.head(sample_size)

    exclude_cols = {"rejection_reason", "run_id"}
    rejected_records = []
    for row in rejected_df.to_dict(orient="records"):
        trans_num = row.get("trans_num")
        reason = row.get("rejection_reason")
        original_data = {
            key: value for key, value in row.items() if key not in exclude_cols
        }
        rejected_records.append(
            {
                "run_id": run_id,
                "trans_num": str(trans_num) if pd.notna(trans_num) else "unknown",
                "original_data": serialize_rejected_original_data(original_data),
                "rejection_reason": str(reason) if pd.notna(reason) else "unknown",
            }
        )

    if not rejected_records:
        return 0

    with engine.connect() as conn:
        result = conn.execute(
            text("""
            INSERT INTO rejected_records (
                run_id, trans_num, original_data, rejection_reason, stage
            )
            VALUES (
                :run_id, :trans_num, CAST(:original_data AS jsonb),
                :rejection_reason, 'validation'
            )
            ON CONFLICT DO NOTHING
            """),
            rejected_records,
        )
        conn.commit()
    return max(result.rowcount or 0, 0)


def load(
    sample_size: int | None = None,
    incremental: bool = False,
) -> dict:
    """
    Load Gold data into PostgreSQL with deduplication.

    Args:
        sample_size: If set, only load first N rows.
        incremental: If True, only load rows newer than the latest
            timestamp already present in the database. Idempotent.

    Returns:
        dict with run_id, customers, merchants, transactions, rejected,
        rows_inserted, last_loaded_timestamp, status, duration
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

    # --- Incremental load: compute cutoff timestamp ---
    if incremental and "unix_time" in df.columns:
        from sqlalchemy import text

        with engine.connect() as conn:
            max_in_db = conn.execute(
                text("SELECT MAX(unix_time) FROM transactions")
            ).scalar()
            max_in_state = conn.execute(
                text(
                    "SELECT last_loaded_timestamp FROM pipeline_load_state "
                    "WHERE source_table = 'transactions'"
                )
            ).scalar()

        cutoff = max(max_in_db or 0, max_in_state or 0)
        if cutoff > 0:
            before = len(df)
            df = _filter_incremental_rows(df, cutoff)
            logger.info(
                "Incremental load: cutoff=%s, new rows=%s (filtered from %s)",
                cutoff,
                len(df),
                before,
            )
        else:
            logger.info("Incremental load: cutoff=0, loading all %s rows", len(df))

    if len(df) == 0:
        rejected_count = _load_rejected_records(
            engine=engine, run_id=run_id, sample_size=sample_size
        )
        return {
            "run_id": run_id,
            "status": "success",
            "rows_inserted": 0,
            "rejected_inserted": rejected_count,
            "message": "No new data to load",
            "duration_seconds": round((datetime.now() - start_time).total_seconds(), 2),
            "timestamp": datetime.now().isoformat(),
        }

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
    customers_df = df[customer_cols].drop_duplicates(subset="cc_num_masked").copy()
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
                ON CONFLICT (customer_id) DO UPDATE SET
                    gender = EXCLUDED.gender,
                    city = EXCLUDED.city,
                    state = EXCLUDED.state,
                    zip = EXCLUDED.zip,
                    city_pop = EXCLUDED.city_pop,
                    job = EXCLUDED.job,
                    age_at_transaction = EXCLUDED.age_at_transaction
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
        result = conn.execute(
            text("SELECT merchant_id, merchant_name, category FROM merchants")
        )
        for row in result:
            merchant_id_map[(row[1], row[2])] = row[0]

    logger.info(f"Inserted/updated {len(merchants_df)} merchants")

    # 3. Insert transactions
    df["customer_id"] = df["cc_num_masked"]
    df["merchant_id"] = df.apply(
        lambda row: merchant_id_map.get((row["merchant"], row["category"])),
        axis=1,
    )

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

    records = trans_df.to_dict("records")
    rows_inserted = _insert_transaction_records(engine, records)

    logger.info(f"Attempted {len(trans_df)} transaction inserts")

    # 4. Insert rejected records independently from transaction progress.
    rejected_count = _load_rejected_records(
        engine=engine, run_id=run_id, sample_size=sample_size
    )

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
                "records_out": rows_inserted,
                "records_rejected": rejected_count,
                "duration": round((datetime.now() - start_time).total_seconds(), 2),
            },
        )
        conn.commit()

    duration = (datetime.now() - start_time).total_seconds()

    last_ts = None
    if "unix_time" in df.columns and len(df) > 0:
        last_ts = int(df["unix_time"].max())

    # 6. Write pipeline_load_state for incremental tracking
    if last_ts is not None:
        with engine.connect() as conn:
            conn.execute(
                text("""
                INSERT INTO pipeline_load_state (source_table, last_loaded_timestamp, rows_loaded, loaded_at)
                VALUES ('transactions', :ts, :rows, CURRENT_TIMESTAMP)
                ON CONFLICT (source_table) DO UPDATE SET
                    last_loaded_timestamp = EXCLUDED.last_loaded_timestamp,
                    rows_loaded = EXCLUDED.rows_loaded,
                    loaded_at = EXCLUDED.loaded_at
                """),
                {"ts": last_ts, "rows": rows_inserted},
            )
            conn.commit()

        logger.info(
            "Load state written: source_table=transactions, last_ts=%s, rows=%s",
            last_ts,
            rows_inserted,
        )

    result = {
        "run_id": run_id,
        "status": "success",
        "customers_inserted": len(customers_df),
        "merchants_inserted": len(merchants_df),
        "transactions_attempted": len(trans_df),
        "rejected_inserted": rejected_count,
        "rows_inserted": rows_inserted,
        "last_loaded_timestamp": last_ts,
        "duration_seconds": round(duration, 2),
        "timestamp": datetime.now().isoformat(),
    }

    logger.info(
        f"Loading complete: {len(customers_df)} customers, {len(merchants_df)} merchants, {len(trans_df)} transactions"
    )

    return result
