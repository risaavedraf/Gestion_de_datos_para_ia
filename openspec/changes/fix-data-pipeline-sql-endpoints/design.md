# Technical Design: Comprehensive Data Pipeline Overhaul

**Change ID**: `fix-data-pipeline-sql-endpoints`  
**Date**: 2026-05-24  
**Status**: `designed`  
**Review Budget**: 400 lines per PR  
**Strategy**: 3 chained PRs

---

## Architecture Overview (Post-Change Target State)

```
┌──────────────────────────────────────────────────────────────┐
│                        FRONTEND (SPA)                         │
│  Dataset Tab ──toggle──▶ Parquet endpoints or SQL endpoints  │
│  Demo Tab ──▶ /api/model/predict + explanation panel         │
│  Pipeline Tab ──▶ /api/pipeline/status + SQL row counts       │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│                      FASTAPI (app.py)                         │
│  ┌──────────────────┐  ┌──────────────────────────────────┐ │
│  │ Parquet endpoints │  │ SQL endpoints (NEW)               │ │
│  │ /api/dataset/*    │  │ GET /api/sql/transactions          │ │
│  │ /api/kpis         │  │ GET /api/sql/transactions/{id}    │ │
│  │ /api/model/*      │  │ GET /api/sql/stats                │ │
│  │                   │  │ GET /api/sql/kpis                 │ │
│  └──────────────────┘  └──────────────┬───────────────────┘ │
└───────────────────────────────────────┼──────────────────────┘
                                        │ sqlalchemy engine
                          ┌─────────────▼──────────────────┐
                          │  backend/src/db.py (NEW)        │
                          │  get_engine() → shared pool    │
                          │  pool_size=5, max_overflow=10   │
                          └─────────────┬──────────────────┘
                                        │
                          ┌─────────────▼──────────────────┐
                          │         PostgreSQL              │
                          │  transactions, customers,       │
                          │  merchants, pipeline_logs,      │
                          │  rejected_records,              │
                          │  model_predictions,             │
                          │  model_training_runs,           │
                          │  pipeline_load_state (NEW)      │
                          └────────────────────────────────┘

Feature Engineering Flow (PR-2):
  Gold parquet → build_features()
    ├── chronological split (train / test)
    ├── impute + encode (unchanged)
    ├── StandardScaler.fit(train) → scaler.joblib
    ├── add interaction features (amt_per_city_pop, distance_x_amt, etc.)
    ├── scale all numeric (including interactions)
    └── return X_train, X_test, y_train, y_test, feature_names, scaler

Prediction Flow (PR-2):
  Raw tx → prepare_transaction_for_prediction()
    ├── load metadata (category_mapping, gender_mapping, feature_names)
    ├── load scaler from scaler_path in metadata
    ├── build raw feature vector (9 base features)
    ├── compute interaction features
    └── apply scaler → model.predict()
```

---

# PR-1: `fix/seed-and-sql-reads`

**Branch**: `fix/seed-and-sql-reads`  
**Estimated diff**: ~350 lines  
**Review risk**: Low (additive changes, no existing behavior altered)

## 1. Technical Design

### 1.1 Files to Modify

| File | Function/Class | Change | ~Lines |
|------|---------------|--------|--------|
| `backend/seed.py` | `seed_data()`, `generate_synthetic_data()` | Gate synthetic generation; respect `DATA_SAMPLE_SIZE` | +40 |
| `backend/app.py` | New endpoints only | Add 4 SQL read endpoints + shared engine | +120 |
| `backend/config/settings.py` | Constants | Add `ALLOW_SYNTHETIC_SEED`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | +10 |
| `backend/src/loader.py` | `load()`, `create_tables()` | Add `incremental` param, upserts, `pipeline_load_state` table | +80 |
| `backend/src/ingestion.py` | `ingest()` | Improve error message when source CSV missing | +5 |

### 1.2 New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `backend/src/db.py` | Shared SQLAlchemy engine factory with connection pooling | +35 |
| `backend/tests/test_sql_endpoints.py` | Tests for all 4 SQL endpoints | +60 |

### 1.3 Data Flow

```
POST /api/pipeline/run → ingest() → clean() → validate() → load(incremental=True)
                                                                     │
                                                                     ▼
                                                          ┌─────────────────────┐
                                                          │   loader.py:load()   │
                                                          │                      │
                                                          │ 1. Read Gold parquet │
                                                          │ 2. Query max unix_   │
                                                          │    time from SQL     │
                                                          │ 3. Filter new rows   │
                                                          │ 4. Upsert customers  │
                                                          │    (ON CONFLICT...   │
                                                          │     DO UPDATE)       │
                                                          │ 5. Upsert merchants  │
                                                          │ 6. Insert new txns   │
                                                          │    (ON CONFLICT DO   │
                                                          │     NOTHING)         │
                                                          │ 7. Write pipeline_   │
                                                          │    load_state        │
                                                          └──────────┬──────────┘
                                                                     │
GET /api/sql/transactions?limit=50&offset=0&fraud=1 ─────────────────┤
GET /api/sql/transactions/{trans_num} ────────────────────────────────┤
GET /api/sql/stats ──────────────────────────────────────────────────┤
GET /api/sql/kpis ───────────────────────────────────────────────────┤
                           │                                          │
                           ▼                                          ▼
              ┌──────────────────────┐              ┌───────────────────────┐
              │  app.py SQL routes   │──────────────│  backend/src/db.py     │
              │  using db.get_engine │              │  get_engine() → shared │
              │  + text() queries    │              │  pool (5+10 overflow)  │
              └──────────────────────┘              └───────────────────────┘
```

### 1.4 API Contract — SQL Read Endpoints

#### `GET /api/sql/transactions`

**Query Parameters**:
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max 500 |
| `offset` | int | 0 | |
| `fraud` | int | — | Filter by `is_fraud` (0 or 1) |
| `start_date` | str | — | ISO date, e.g. `2020-01-01` |
| `end_date` | str | — | ISO date |
| `category` | str | — | Filter by category |
| `min_amt` | float | — | Minimum amount |
| `max_amt` | float | — | Maximum amount |

**Response** (200):
```json
{
    "transactions": [
        {
            "trans_num": "abc-123",
            "amt": 150.00,
            "trans_date_trans_time": "2020-06-15T14:30:00",
            "category": "shopping_pos",
            "is_fraud": 0,
            "merchant_name": "fraud_Kohler Inc",
            "city": "Houston",
            "state": "TX",
            "distance_km": 12.5,
            "trans_hour": 14,
            "trans_day_of_week": 3,
            "trans_month": 6,
            "unix_time": 1592231400
        }
    ],
    "meta": {
        "total": 555719,
        "limit": 50,
        "offset": 0,
        "next_offset": 50
    }
}
```

**Errors**: `422` if `limit > 500`, `500` if DB unreachable.

---

#### `GET /api/sql/transactions/{trans_num}`

**Response** (200): Single transaction object (same shape as list item).  
**Response** (404): `{"detail": "Transaction not found: abc-123"}`

---

#### `GET /api/sql/stats`

**Response** (200):
```json
{
    "total_count": 555719,
    "fraud_count": 11506,
    "legit_count": 544213,
    "fraud_pct": 2.07,
    "amt_mean": 70.50,
    "amt_max": 28948.90,
    "amt_min": 0.0,
    "amt_std": 160.30,
    "by_category": [
        {"category": "gas_transport", "count": 85000, "fraud_count": 1200},
        {"category": "grocery_pos", "count": 72000, "fraud_count": 800}
    ],
    "completeness_pct": 99.8,
    "date_min": "2019-01-01T00:00:00",
    "date_max": "2020-12-31T23:59:59"
}
```

---

#### `GET /api/sql/kpis`

Mirrors `/api/kpis` but sourced from SQL.

**Response** (200):
```json
{
    "total_records": 555719,
    "fraud_count": 11506,
    "legit_count": 544213,
    "fraud_pct": 2.07,
    "amt_mean": 70.50,
    "amt_median": 45.00,
    "amt_max": 28948.90,
    "completeness_pct": 99.8,
    "status": "available",
    "source": "postgresql",
    "timestamp": "2026-05-24T12:00:00"
}
```

### 1.5 Database Schema Changes

**New table**: `pipeline_load_state`

```sql
CREATE TABLE IF NOT EXISTS pipeline_load_state (
    id SERIAL PRIMARY KEY,
    source_table VARCHAR(64) NOT NULL UNIQUE,
    last_loaded_timestamp BIGINT,
    rows_loaded INTEGER,
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Modified customer insert**: Change from `ON CONFLICT (customer_id) DO NOTHING` to:

```sql
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
```

## 2. Implementation Details

### 2.1 `backend/src/db.py` — Shared Engine Factory

```python
# Exact pattern to use
from sqlalchemy import create_engine
from backend.config.settings import DATABASE_URL, DB_POOL_SIZE, DB_MAX_OVERFLOW

_engine = None

def get_engine():
    """Return a shared SQLAlchemy engine with connection pooling."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_pre_ping=True,       # Validate connections before use
            pool_recycle=3600,        # Recycle after 1 hour
        )
    return _engine
```

**Error handling**: If `DATABASE_URL` is unset or unreachable, `get_engine()` should raise a clear `RuntimeError` with guidance. Endpoints should catch this and return `503`.

### 2.2 `backend/seed.py` — Synthetic Gate

**Pattern**:
```python
import os
from backend.config.settings import DATA_SAMPLE_SIZE

def seed_data():
    allow_synthetic = os.getenv("ALLOW_SYNTHETIC_SEED", "").lower() == "true"

    if RAW_CSV.exists():
        print(f"[seed] Raw CSV already exists at {RAW_CSV} — skipping generation.")
    elif allow_synthetic:
        print(f"[seed] Generating {DATA_SAMPLE_SIZE} synthetic rows → {RAW_CSV}")
        rows = generate_synthetic_data(DATA_SAMPLE_SIZE)
        # Write CSV...
    else:
        print("[seed] ERROR: Raw CSV missing and ALLOW_SYNTHETIC_SEED is not 'true'.")
        print("[seed] Place the real CSV at Data/bronze/02_fraudTest.csv or set ALLOW_SYNTHETIC_SEED=true.")
        sys.exit(1)

    # Run pipeline stages — always pass sample_size=None to use full data
    ingest(sample_size=None)
    clean(sample_size=None)
    validate(sample_size=None)
```

Also remove the module-level `SAMPLE_SIZE = 500` constant from `seed.py`.

### 2.3 `backend/src/loader.py` — Incremental Load

**Key changes**:

1. Add `incremental: bool = False` parameter to `load()`.
2. Create `pipeline_load_state` in `create_tables()`.
3. When `incremental=True`:
   - Query `MAX(unix_time)` from `transactions` and `pipeline_load_state`.
   - Use the greater value as cutoff.
   - Filter Gold rows: `df = df[df["unix_time"] > cutoff]`.
   - After successful insert, upsert `pipeline_load_state`.
4. Return `last_loaded_timestamp` and `rows_inserted` in result dict.

```python
def load(sample_size=None, incremental=False) -> dict:
    # ... existing setup ...
    rows_inserted = 0
    last_ts = None

    if incremental:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT MAX(unix_time) FROM transactions"
            )).scalar()
            max_in_db = result or 0
            result = conn.execute(text(
                "SELECT last_loaded_timestamp FROM pipeline_load_state WHERE source_table = 'transactions'"
            )).scalar()
            max_in_state = result or 0
            cutoff = max(max_in_db, max_in_state)

        df = df[df["unix_time"] > cutoff] if cutoff > 0 else df
        logger.info(f"Incremental load: cutoff={cutoff}, new rows={len(df)}")

    if len(df) == 0:
        return {"status": "success", "rows_inserted": 0, "message": "No new data"}

    # ... existing insert logic for customers, merchants, transactions ...
    rows_inserted = len(trans_df)

    # Write load state
    last_ts = int(df["unix_time"].max())
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO pipeline_load_state (source_table, last_loaded_timestamp, rows_loaded, loaded_at)
            VALUES ('transactions', :ts, :rows, CURRENT_TIMESTAMP)
            ON CONFLICT (source_table) DO UPDATE SET
                last_loaded_timestamp = EXCLUDED.last_loaded_timestamp,
                rows_loaded = EXCLUDED.rows_loaded,
                loaded_at = EXCLUDED.loaded_at
        """), {"ts": last_ts, "rows": rows_inserted})
        conn.commit()

    return {
        # ... existing fields ...
        "rows_inserted": rows_inserted,
        "last_loaded_timestamp": last_ts,
    }
```

### 2.4 SQL Endpoints Pattern

Every SQL endpoint follows the same pattern:

```python
from backend.src.db import get_engine
from sqlalchemy import text

@app.get("/api/sql/transactions")
async def sql_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    fraud: int | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    category: str | None = Query(None),
    min_amt: float | None = Query(None),
    max_amt: float | None = Query(None),
):
    try:
        engine = get_engine()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    clauses = ["1=1"]
    params = {}

    if fraud is not None:
        clauses.append("is_fraud = :fraud")
        params["fraud"] = fraud
    # ... build dynamic WHERE clauses ...

    where = " AND ".join(clauses)

    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM transactions WHERE {where}"), params
        ).scalar()

        rows = conn.execute(
            text(f"""
                SELECT t.trans_num, t.amt, t.trans_date_trans_time, t.category,
                       t.is_fraud, t.trans_hour, t.trans_day_of_week, t.trans_month,
                       t.distance_km, t.unix_time, t.city, t.state,
                       m.merchant_name
                FROM transactions t
                LEFT JOIN merchants m ON t.merchant_id = m.merchant_id
                WHERE {where}
                ORDER BY t.trans_date_trans_time DESC
                LIMIT :limit OFFSET :offset
            """),
            {**params, "limit": limit, "offset": offset}
        ).mappings().all()

    return {
        "transactions": [dict(r) for r in rows],
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        },
    }
```

### 2.5 Error Handling Strategy

| Scenario | HTTP Code | Detail |
|----------|-----------|--------|
| DB unreachable | 503 | `Database connection failed. Verify DATABASE_URL and that PostgreSQL is running.` |
| Table not populated | 200 | Returns empty list + `meta.total = 0` |
| Invalid `limit` (>500) | 422 | `limit must be between 1 and 500` |
| `trans_num` not found | 404 | `Transaction not found: {id}` |
| Malformed date | 422 | `Invalid date format. Use YYYY-MM-DD.` |

### 2.6 Testing Approach

**`backend/tests/test_sql_endpoints.py`**:
- Use FastAPI `TestClient` with a test PostgreSQL instance (or mock the engine).
- **Test 1**: `GET /api/sql/transactions?limit=10` returns 10 rows and `meta.total`.
- **Test 2**: `GET /api/sql/transactions?fraud=1` returns only fraud rows.
- **Test 3**: `GET /api/sql/transactions/{id}` returns 200 for existing, 404 for missing.
- **Test 4**: `GET /api/sql/stats` returns aggregated counts and category breakdown.
- **Test 5**: `GET /api/sql/kpis` returns fraud_pct, completeness, etc.
- **Test 6**: `load(incremental=True)` twice → second run returns `rows_inserted: 0`.
- **Test 7**: Customer upsert updates `city_pop` on conflict.
- **Test 8**: Concurrent requests (20 simultaneous) — verify no pool exhaustion.

**Fixtures**: Use `pytest` fixtures to populate test DB before each test class.

### 2.7 Migration / Rollback

- **`pipeline_load_state` table**: Created by `create_tables()` automatically. If PR is reverted, the table can remain (no impact on existing code). No migration scripts needed.
- **Customer upsert change**: If reverted, future loads will stop updating mutable fields. No data corruption — just a semantic rollback.
- **SQL endpoints**: Reverting removes the new routes. Existing parquet endpoints untouched.
- **`backend/src/db.py`**: Reverting removes the file. The existing `get_engine()` in `loader.py` is a local function — keep it or replace with `from db import` in this PR (recommended: import from `db.py` to avoid duplication).

## 3. Dependencies

- PR-1 has **no dependencies** on PR-2 or PR-3.
- PR-1 is the foundation PR — must be merged first.
- PR-2 needs the DB populated (which PR-1's loader fixes), but can run independently if DB is already seeded.

## 4. Acceptance Criteria

| ID | Criteria | Verification |
|----|----------|-------------|
| AC-1.1 | `seed.py` with real CSV present: skips, doesn't overwrite | Run `python backend/seed.py` with `Data/bronze/02_fraudTest.csv` present → prints "skipping generation" |
| AC-1.2 | `seed.py` with real CSV missing + `ALLOW_SYNTHETIC_SEED=true`: generates `DATA_SAMPLE_SIZE` rows | Run with env var → CSV created with 10,000 rows |
| AC-1.3 | `seed.py` with real CSV missing + no env var: exits with error | Run → prints error, exits with code 1 |
| AC-1.4 | `GET /api/sql/transactions` returns paginated data | `curl localhost:8000/api/sql/transactions?limit=5` → 5 rows + correct `meta` |
| AC-1.5 | `GET /api/sql/transactions/{id}` returns 200/404 correctly | `curl` with known/unknown trans_num |
| AC-1.6 | `GET /api/sql/stats` returns aggregated stats | Compare to `SELECT COUNT(*)...` directly in psql |
| AC-1.7 | `GET /api/sql/kpis` mirrors parquet KPIs within 1% tolerance | Run both endpoints, compare numeric values |
| AC-1.8 | `load(incremental=True)` is idempotent | Run twice, second run → `rows_inserted: 0` |
| AC-1.9 | Customer upsert updates fields | Manually change a customer's `city_pop` in Gold, re-run load, verify in psql |
| AC-1.10 | `pipeline_load_state` has correct timestamp after load | `SELECT * FROM pipeline_load_state` → one row with correct values |
| AC-1.11 | 20 concurrent requests succeed | `ab -n 100 -c 20 http://localhost:8000/api/sql/transactions?limit=10` → 100% success |

---

# PR-2: `feat/scaling-and-features`

**Branch**: `feat/scaling-and-features` (based on `fix/seed-and-sql-reads`)  
**Estimated diff**: ~380 lines  
**Review risk**: Medium (changes model training/inference path; backward-compatible)

## 1. Technical Design

### 1.1 Files to Modify

| File | Function/Class | Change | ~Lines |
|------|---------------|--------|--------|
| `backend/src/features.py` | `build_features()` | Add StandardScaler, interaction features, scaler persistence | +100 |
| `backend/src/model_train.py` | `train_models()` | Save scaler_path in metadata; threshold tuning | +55 |
| `backend/src/model_predict.py` | `predict_single()`, `predict_batch()`, `prepare_transaction_for_prediction()` | Load scaler, apply to feature vectors | +70 |
| `backend/src/model_evaluate.py` | `evaluate_model()` | Add threshold tuning (precision-recall curve analysis) | +40 |
| `backend/config/settings.py` | Constants | Add `SCALER_PATH` default, `MODEL_DECISION_THRESHOLD` stays | +5 |
| `backend/app.py` | `/api/model/predict` endpoint | Include explanations in response (optional) | +20 |
| Tests | Multiple files | Scaler round-trip, interaction feature tests, threshold tests | +90 |

### 1.2 New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `backend/tests/test_features_scaling.py` | Test scaler fit/transform/save/load round-trip | +50 |
| `backend/tests/test_model_predict_scaled.py` | Test prediction with scaled features | +40 |

### 1.3 Data Flow — Feature Engineering (PR-2)

```
build_features() call
│
├── 1. Load Gold parquet (unchanged)
├── 2. Chronological split (unchanged)
├── 3. Impute numeric features (unchanged)
├── 4. Encode categorical (unchanged)
│
├── 5. [NEW] Compute interaction features ON TRAIN:
│   ├── amt_per_city_pop = amt / max(city_pop, 1)
│   ├── distance_x_amt     = distance_km * amt
│   ├── hour_is_night      = 1 if trans_hour in {0-5, 22-23} else 0
│   └── category_fraud_rate = fraud_rate per category from TRAIN ONLY
│       └── test receives same mapping; unknown → global train fraud rate
│
├── 6. [NEW] Scale all numeric features:
│   ├── Select columns: NUMERIC_FEATURES + interaction columns
│   ├── scaler = StandardScaler().fit(train_df[all_numeric])
│   ├── train_scaled = scaler.transform(train_df)
│   ├── test_scaled = scaler.transform(test_df)
│   └── joblib.dump(scaler, models/scaler.joblib)
│
├── 7. Build final feature_names list (includes interaction features)
├── 8. Return extended result dict with scaler object
```

### 1.4 Data Flow — Prediction with Scaler

```
predict_single(features) or predict_batch(df)
│
├── 1. Load model (unchanged)
├── 2. Load metadata → scaler_path
├── 3. [NEW] Load scaler from scaler_path
│   └── if missing: raise RuntimeError("Scaler artifact missing at {path}")
│
├── 4. [NEW] Compute interaction features from raw features:
│   └── amt_per_city_pop, distance_x_amt, hour_is_night
│   └── Note: category_fraud_rate extracted from metadata (precomputed mapping)
│
├── 5. [NEW] Build full feature vector matching training order:
│   └── feature_vector = [amt, trans_hour, ..., age_at_transaction,
│                          category_encoded, gender_encoded,
│                          amt_per_city_pop, distance_x_amt,
│                          hour_is_night, category_fraud_rate]
│
├── 6. [NEW] Apply scaler: X_scaled = scaler.transform([feature_vector])
├── 7. model.predict_proba(X_scaled) → probability
├── 8. Apply decision_threshold from metadata → prediction
```

### 1.5 Feature Ordering Contract

The `feature_names` list in model metadata must be exactly:
```python
[
    "amt", "trans_hour", "trans_day_of_week", "trans_month",
    "distance_km", "city_pop", "age_at_transaction",
    "category_encoded", "gender_encoded",
    "amt_per_city_pop", "distance_x_amt", "hour_is_night", "category_fraud_rate"
]
```

This is the contract between `build_features()`, `train_models()`, and `predict_single()`. Any mismatch in ordering will silently degrade prediction quality.

### 1.6 Threshold Tuning — Data Flow

```
evaluate_model(model, X_test, y_test)
│
├── Compute y_prob = model.predict_proba(X_test)[:, 1]
├── Compute precision-recall curve: precision, recall, thresholds
├── Compute F1 for each threshold:
│   └── f1_scores = 2 * (precision * recall) / (precision + recall)
│
├── [NEW] Find best threshold: argmax(f1_scores)
│   └── best_threshold = thresholds[best_idx]
│   └── If no improvement over default, keep default
│
├── Save threshold to model_metadata.json:
│   └── metadata["decision_threshold"] = best_threshold
│
├── Return evaluation with best_threshold included
```

**Configurable minimum precision**: If `MODEL_MIN_PRECISION` env var is set (e.g., 0.7), filter candidate thresholds to those meeting the minimum and select the one with highest F1.

### 1.7 API Contract Changes (subtle)

`POST /api/model/predict` response **adds** optional fields:

```json
{
    "prediction": 1,
    "probability": 0.8234,
    "risk_level": "high",
    "label": "FRAUD",
    "decision_threshold": 0.42,      // was already present
    "timestamp": "2026-05-24T...",
    "feature_importance": [           // NEW: top-3 features when available
        {"name": "amt_per_city_pop", "importance": 0.2812},
        {"name": "distance_x_amt", "importance": 0.1923},
        {"name": "category_fraud_rate", "importance": 0.1501}
    ]
}
```

`GET /api/model/feature-importance` now returns **13 features** instead of 9.

## 2. Implementation Details

### 2.1 Scaler Integration in `build_features()`

```python
from sklearn.preprocessing import StandardScaler
import joblib
from backend.config.settings import MODELS_DIR

def build_features(input_path=None, test_size=0.2, random_state=42):
    # ... existing code unchanged through encoding ...

    # --- NEW: Interaction features ---
    train_df["amt_per_city_pop"] = train_df["amt"] / train_df["city_pop"].clip(lower=1)
    test_df["amt_per_city_pop"] = test_df["amt"] / test_df["city_pop"].clip(lower=1)

    train_df["distance_x_amt"] = train_df["distance_km"] * train_df["amt"]
    test_df["distance_x_amt"] = test_df["distance_km"] * test_df["amt"]

    night_hours = {0, 1, 2, 3, 4, 5, 22, 23}
    train_df["hour_is_night"] = train_df["trans_hour"].isin(night_hours).astype(int)
    test_df["hour_is_night"] = test_df["trans_hour"].isin(night_hours).astype(int)

    # Category fraud rate (computed from train only, no leakage)
    train_fraud_rate = train_df.groupby("category")["is_fraud"].mean()
    global_fraud_rate = train_df["is_fraud"].mean()
    train_df["category_fraud_rate"] = train_df["category"].map(train_fraud_rate).fillna(global_fraud_rate)
    test_df["category_fraud_rate"] = test_df["category"].map(train_fraud_rate).fillna(global_fraud_rate)

    # --- NEW: Scaling ---
    numeric_feature_cols = NUMERIC_FEATURES + [
        "amt_per_city_pop", "distance_x_amt", "category_fraud_rate"
    ]
    # Note: hour_is_night is binary (0/1), not scaled

    scaler = StandardScaler()
    train_scaled_values = scaler.fit_transform(train_df[numeric_feature_cols].values)
    test_scaled_values = scaler.transform(test_df[numeric_feature_cols].values)

    # Replace original columns with scaled values
    for i, col in enumerate(numeric_feature_cols):
        train_df[col] = train_scaled_values[:, i]
        test_df[col] = test_scaled_values[:, i]

    # Persist scaler
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    scaler_path = MODELS_DIR / "scaler.joblib"
    joblib.dump(scaler, scaler_path)
    logger.info(f"Scaler saved to {scaler_path}")

    # --- Updated feature names ---
    feature_names = NUMERIC_FEATURES + ["category_encoded", "gender_encoded"] + \
                    ["amt_per_city_pop", "distance_x_amt", "hour_is_night", "category_fraud_rate"]

    # Build final arrays
    X_train = train_df[feature_names].to_numpy()
    X_test = test_df[feature_names].to_numpy()

    result = {
        # ... existing fields ...
        "feature_names": feature_names,
        "n_features": len(feature_names),
        "scaler": scaler,                          # NEW
        "scaler_path": str(scaler_path),            # NEW
    }

    return result
```

**Edge Cases**:
- If `city_pop` is 0 for any row, `amt_per_city_pop` uses `max(city_pop, 1)` to avoid division by zero.
- If a category appears in test but not in train, `category_fraud_rate` gets `global_fraud_rate`.
- StandardScaler handles NaN-free input (imputation is done before scaling).

### 2.2 Scaler in Model Metadata

`train_models()` receives `scaler_path` from the feature builder:

```python
def train_models(X_train, y_train, feature_names=None,
                 category_mapping=None, gender_mapping=None,
                 scaler_path=None):  # NEW parameter

    # ... existing training logic ...

    metadata = {
        # ... existing fields ...
        "scaler_path": scaler_path or str(MODELS_DIR / "scaler.joblib"),
        "feature_names": feature_names,
        # ... rest of metadata ...
    }
```

**Caller update in `app.py`**:
```python
train_result = train_models(
    feature_data["X_train"],
    feature_data["y_train"],
    feature_data["feature_names"],
    category_mapping=feature_data.get("category_mapping"),
    gender_mapping=feature_data.get("gender_mapping"),
    scaler_path=feature_data.get("scaler_path"),          # NEW
)
```

### 2.3 Scaler in Prediction

```python
import joblib

def _load_scaler():
    """Load scaler from path in model metadata. Raise clear error if missing."""
    metadata = load_metadata()
    scaler_path = metadata.get("scaler_path")
    if not scaler_path:
        raise RuntimeError(
            "Model was trained without a scaler. Retrain the model first."
        )
    scaler_file = Path(scaler_path)
    if not scaler_file.exists():
        raise RuntimeError(
            f"Scaler artifact missing at {scaler_path}. "
            "Retrain the model to regenerate the scaler."
        )
    return joblib.load(scaler_file)


def predict_single(features: dict, model=None):
    if model is None:
        model = load_model()

    metadata = load_metadata()
    feature_names = metadata.get("feature_names", [])

    # --- NEW: compute interaction features ---
    amt = float(features.get("amt", 0))
    city_pop = int(features.get("city_pop", 1))
    distance_km = float(features.get("distance_km", 0))
    trans_hour = int(features.get("trans_hour", 12))

    features["amt_per_city_pop"] = amt / max(city_pop, 1)
    features["distance_x_amt"] = distance_km * amt
    features["hour_is_night"] = 1 if trans_hour in {0, 1, 2, 3, 4, 5, 22, 23} else 0
    # category_fraud_rate: look up from metadata or use default
    category = str(features.get("category", "")).strip().lower()
    fraud_rate_map = metadata.get("category_fraud_rate_map", {})
    features["category_fraud_rate"] = fraud_rate_map.get(
        category, metadata.get("global_fraud_rate", 0.05)
    )

    # --- Build and scale ---
    feature_vector = [features.get(f, 0) for f in feature_names]
    X_raw = np.array([feature_vector])

    scaler = _load_scaler()
    # Only scale the columns the scaler was fit on
    numeric_cols = NUMERIC_FEATURES + ["amt_per_city_pop", "distance_x_amt", "category_fraud_rate"]
    numeric_indices = [feature_names.index(c) for c in numeric_cols if c in feature_names]
    X_scaled = X_raw.copy().astype(float)
    X_scaled[:, numeric_indices] = scaler.transform(X_raw[:, numeric_indices])

    # Predict
    probability = float(model.predict_proba(X_scaled)[0][1]) if hasattr(model, "predict_proba") else float(model.predict(X_scaled)[0])
    decision_threshold = float(metadata.get("decision_threshold", MODEL_DECISION_THRESHOLD))
    prediction = 1 if probability >= decision_threshold else 0

    return {
        "prediction": prediction,
        "probability": round(probability, 4),
        "risk_level": get_risk_level(probability, RISK_LOW, RISK_HIGH),
        "label": "FRAUD" if prediction == 1 else "LEGIT",
        "decision_threshold": decision_threshold,
        "timestamp": datetime.now().isoformat(),
    }
```

**Key Design Decision**: `prepare_transaction_for_prediction()` now becomes the single function that computes interaction features + encoding. `predict_single()` and `predict_batch()` both call it. The scaler is applied inside `predict_single()`/`predict_batch()` after the full feature vector is built.

### 2.4 Threshold Tuning Implementation

Added to `evaluate_model()` in `model_evaluate.py`:

```python
def _tune_threshold(y_test, y_prob, min_precision=None):
    """Select best threshold maximizing F1. Optionally enforce min precision."""
    precision, recall, thresholds = precision_recall_curve(y_test, y_prob)
    # thresholds has n elements, precision/recall have n+1 (last is 1.0/0.0)
    # Slice to match
    n_thresholds = len(thresholds)
    f1_scores = 2 * (precision[:n_thresholds] * recall[:n_thresholds]) / \
                (precision[:n_thresholds] + recall[:n_thresholds] + 1e-10)

    if min_precision is not None:
        valid_mask = precision[:n_thresholds] >= min_precision
        if not valid_mask.any():
            logger.warning(f"No threshold meets min_precision={min_precision}. Using best overall.")
        else:
            f1_scores[~valid_mask] = -1

    best_idx = np.argmax(f1_scores)
    return float(thresholds[best_idx]), float(f1_scores[best_idx])
```

Invoked from `evaluate_model()`. Result saved as `decision_threshold` in evaluation report AND updated in `model_metadata.json`.

### 2.5 Category Fraud Rate Mapping in Metadata

Since `category_fraud_rate` is needed at inference, the mapping from category → rate must be persisted:

```python
# In build_features(), after computing:
category_fraud_rate_map = train_df.groupby("category")["is_fraud"].mean().to_dict()
global_fraud_rate = train_df["is_fraud"].mean()

# In train_models(), add to metadata:
metadata["category_fraud_rate_map"] = category_fraud_rate_map
metadata["global_fraud_rate"] = float(global_fraud_rate)
```

### 2.6 Backward Compatibility

**Old model without scaler**: `predict_single()` checks `metadata.get("scaler_path")`. If absent, skips scaling (log warning) and uses raw features. This allows models trained before PR-2 to still serve predictions. The warning nudges the user to retrain.

```python
scaler = None
scaler_path = metadata.get("scaler_path")
if scaler_path and Path(scaler_path).exists():
    scaler = joblib.load(scaler_path)
else:
    logger.warning("No scaler found. Predictions may be suboptimal. Retrain the model.")
```

### 2.7 Testing Approach

| Test | File | Description |
|------|------|-------------|
| `test_scaler_fit_transform` | `test_features_scaling.py` | Fit scaler on train, verify train mean ≈ 0, std ≈ 1 |
| `test_scaler_persistence` | `test_features_scaling.py` | Save/load scaler, verify identical transforms |
| `test_no_leakage_fraud_rate` | `test_features_scaling.py` | Fraud rate computed from train only; test gets train rates |
| `test_interaction_features_present` | `test_features_scaling.py` | 13 features in result, new features have non-zero variance |
| `test_scaled_prediction` | `test_model_predict_scaled.py` | Train model, predict, verify scaler was applied |
| `test_missing_scaler_error` | `test_model_predict_scaled.py` | Remove scaler file, verify clear error message |
| `test_old_model_backward_compat` | `test_model_predict_scaled.py` | Metadata without scaler_path → still predicts (with warning) |
| `test_threshold_tuning` | `test_model_evaluate.py` (new) | Verify tuned threshold ≠ default when data is separable |

### 2.8 Migration / Rollback

- **New scaler.joblib file**: If PR reverted, this file can be deleted. Old `best_model.joblib` continues working if backward-compat logic is kept (see 2.6).
- **Metadata additions**: New keys (`scaler_path`, `category_fraud_rate_map`, `global_fraud_rate`) are additive. Old code that reads metadata via `json.load()` ignores unknown keys.
- **Feature count change**: Old frontend expects 9 features in feature importance chart. PR-2 changes to 13. Chart.js handles this gracefully (horizontal bar chart auto-adjusts).

## 3. Dependencies

- **PR-2 depends on PR-1**: Needs the DB to be populated with real data (model training requires data), and the `backend/src/db.py` module for potential future SQL-based feature extraction.
- **Can PR-2 be parallelized with PR-1?**: Partially. The feature engineering work can be developed and tested independently using parquet files. However, the model training endpoint should be tested against real data from SQL. Recommend sequential merge.

## 4. Acceptance Criteria

| ID | Criteria | Verification |
|----|----------|-------------|
| AC-2.1 | `build_features()` returns 13 features | Run `pytest backend/tests/test_features_scaling.py -v` → passes |
| AC-2.2 | `scaler.joblib` created after training | Check `models/scaler.joblib` exists after `POST /api/model/train` |
| AC-2.3 | Scaler round-trip produces identical arrays | Unit test: fit → save → load → transform, assert abs diff < 1e-10 |
| AC-2.4 | `category_fraud_rate` has no leakage | Unit test: all values in test set match train mapping, not test fraud rates |
| AC-2.5 | `model_metadata.json` includes `scaler_path` and `decision_threshold` | Inspect file after training |
| AC-2.6 | Prediction with missing scaler returns clear error | Delete `scaler.joblib`, run predict → 500 with "Scaler artifact missing" |
| AC-2.7 | Old model (no scaler_path) still works | Create metadata without scaler_path, predict → succeeds with warning |
| AC-2.8 | Tuned threshold improves F1 vs default 0.3 | Run on real data → tuned threshold ≠ 0.3, F1 ≥ baseline |
| AC-2.9 | Probability range on real data: min < 0.1, max > 0.9 | Train on 555K rows, evaluate test set probabilities |
| AC-2.10 | Feature importance includes 13 entries | `GET /api/model/feature-importance` → 13 items |

---

# PR-3: `feat/frontend-and-advanced-sql`

**Branch**: `feat/frontend-and-advanced-sql` (based on `feat/scaling-and-features`)  
**Estimated diff**: ~350 lines  
**Review risk**: Low-Medium (frontend changes are visual-only; backend changes are additive)

## 1. Technical Design

### 1.1 Files to Modify

| File | Function/Class | Change | ~Lines |
|------|---------------|--------|--------|
| `frontend/index.html` | Dataset section, Pipeline section, Demo section | Add SQL toggle UI, explanation panel, SQL counts | +60 |
| `frontend/js/app.js` | `loadDataset()`, `loadOverview()` | Add SQL toggle logic, dual-path data loading | +80 |
| `frontend/js/charts.js` | `renderFraudChart()`, etc. | Support SQL-sourced data (same chart shape) | +10 |
| `frontend/js/pipeline.js` | `loadPipelineStatus()` | Add SQL table row counts to status display | +25 |
| `frontend/js/model.js` | `runDemoPrediction()` | Get feature importance, render explanation | +40 |
| `frontend/css/style.css` | New classes | Toggle switch, explanation card, SQL badge styles | +30 |
| `backend/app.py` | `/api/pipeline/status` | Add SQL row counts via `db.py` | +20 |
| `backend/app.py` | `/api/model/predict` | Return top-3 feature importance with prediction | +25 |
| `backend/app.py` | `/api/sql/*` | Add `merchant` filter to transactions endpoint | +15 |
| `backend/src/model_predict.py` | `predict_single()` | Return feature contributions (SHAP-like approximate) | +40 |

### 1.2 New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| (none) | All changes are modifications to existing files | — |

### 1.3 Data Flow — Frontend SQL Toggle

```
User clicks "SQL View" toggle on Dataset tab
│
├── toggle-datasource component flips state (localStorage persisted)
│
├── IF parquet view (default):
│   ├── /api/dataset/stats   → updateDatasetStats()
│   ├── /api/dataset/sample  → updateSampleTable()
│   ├── /api/dataset/fraud-dist → renderFraudChart()
│   └── /api/dataset/category-dist → renderCategoryChart()
│
├── IF sql view:
│   ├── /api/sql/stats       → updateDatasetStats()  (remap keys)
│   ├── /api/sql/transactions?limit=10 → updateSampleTable()
│   ├── /api/sql/kpis        → renderFraudChart()
│   └── /api/sql/stats.by_category → renderCategoryChart()
│
└── Visual indicator: badge/label showing current source
```

### 1.4 Data Flow — Prediction Explanations

```
POST /api/model/predict
│
├── Server computes prediction (including scaler from PR-2)
│
├── [NEW] Server computes approximate feature contributions:
│   ├── If model has feature_importances_ (RandomForest/XGBoost):
│   │   └── Sort by importance, take top 3
│   │   └── Use feature value × importance as contribution proxy
│   ├── If model has coef_ (LogisticRegression):
│   │   └── Compute coef × scaled_feature_value for each feature
│   │   └── Sort by absolute contribution, take top 3
│   └── Include in response:
│       {
│           "predictions": [...],
│           "explanations": [
│               {"feature": "amt_per_city_pop", "contribution": 0.28, "direction": "+"},
│               {"feature": "distance_x_amt", "contribution": 0.19, "direction": "+"},
│               {"feature": "category_fraud_rate", "contribution": 0.15, "direction": "+"}
│           ]
│       }
│
└── Frontend renders explanation panel:
    └── List of features with color-coded bars (green = push toward legit,
        red = push toward fraud)
```

### 1.5 Data Flow — Pipeline SQL Counts

```
GET /api/pipeline/status
│
├── Existing: returns bronze/silver/gold stats from parquet
│
├── [NEW] Also queries PostgreSQL:
│   ├── SELECT COUNT(*) FROM transactions
│   ├── SELECT COUNT(*) FROM customers
│   ├── SELECT COUNT(*) FROM merchants
│   └── SELECT COUNT(*) FROM rejected_records
│
└── Response adds "sql_counts" block:
    {
        "bronze": {...},
        "silver": {...},
        "gold": {...},
        "sql_counts": {                          // NEW
            "transactions": 555719,
            "customers": 2000,
            "merchants": 500,
            "rejected_records": 1234
        }
    }
```

### 1.6 Frontend Component Contract — SQL Toggle

```javascript
// State
let dataSource = localStorage.getItem("fraud-dashboard-datasource") || "parquet";

// Toggle UI
function toggleDataSource() {
    dataSource = dataSource === "parquet" ? "sql" : "parquet";
    localStorage.setItem("fraud-dashboard-datasource", dataSource);
    updateToggleUI();
    loadDataset();  // Reload data from new source
}

// Modified loadDataset()
async function loadDataset() {
    if (dataSource === "sql") {
        const [stats, sample, kpis] = await Promise.all([
            api("/api/sql/stats"),
            api("/api/sql/transactions?limit=10"),
            api("/api/sql/kpis"),
        ]);
        if (stats) updateDatasetStatsFromSQL(stats);
        if (sample) updateSampleTableFromSQL(sample.transactions);
        if (kpis) {
            renderFraudChart({ legit: kpis.legit_count, fraud: kpis.fraud_count });
            // Category chart from stats.by_category
            if (stats.by_category) renderCategoryChart(
                stats.by_category.map(c => ({ category: c.category, count: c.count }))
            );
        }
    } else {
        // ... existing parquet load logic unchanged ...
    }
}
```

### 1.7 API Contract Changes

#### `GET /api/pipeline/status` — Extended Response

Adds `sql_counts` block (see 1.5). SQL counts default to `null` if DB is unreachable (graceful degradation).

#### `POST /api/model/predict` — Extended Response

Adds `explanations` array (see 1.4). Omitted when feature importance data is unavailable.

#### `GET /api/sql/transactions` — New Query Param

Adds `merchant` filter parameter (fuzzy `ILIKE` match):

```
GET /api/sql/transactions?merchant=kohler&limit=20
```

## 2. Implementation Details

### 2.1 Frontend SQL Toggle Implementation

**HTML addition** (in `index.html`, Dataset section, after the `<h2>`):
```html
<div class="datasource-toggle">
    <span class="toggle-label">Data Source:</span>
    <button id="toggle-parquet" class="toggle-btn active" onclick="switchDataSource('parquet')">
        📁 Parquet
    </button>
    <button id="toggle-sql" class="toggle-btn" onclick="switchDataSource('sql')">
        🗄️ SQL
    </button>
</div>
```

**JS implementation** (in `app.js`):
```javascript
// At top of file
let dataSource = localStorage.getItem("fraud-datasource") || "parquet";

async function switchDataSource(source) {
    dataSource = source;
    localStorage.setItem("fraud-datasource", source);
    document.getElementById("toggle-parquet")?.classList.toggle("active", source === "parquet");
    document.getElementById("toggle-sql")?.classList.toggle("active", source === "sql");
    await loadDataset();
}

// Modified loadDataset to branch on dataSource
async function loadDataset() {
    if (dataSource === "sql") {
        await loadDatasetFromSQL();
    } else {
        await loadDatasetFromParquet();
    }
}

async function loadDatasetFromSQL() {
    const [stats, sample, kpis] = await Promise.all([
        api("/api/sql/stats"),
        api("/api/sql/transactions?limit=10"),
        api("/api/sql/kpis"),
    ]);
    if (stats) updateDatasetStatsFromSQL(stats);
    if (sample?.transactions) updateSampleTable(sample.transactions);
    if (kpis) {
        renderFraudChart({ legit: kpis.legit_count, fraud: kpis.fraud_count });
    }
    if (stats?.by_category) {
        renderCategoryChart(stats.by_category.map(c => ({ category: c.category, count: c.count })));
    }
}

// Rename existing to avoid confusion
async function loadDatasetFromParquet() {
    // ... existing loadDataset() code goes here, unchanged ...
}

function updateDatasetStatsFromSQL(stats) {
    setText("ds-total-rows", stats.total_count?.toLocaleString() || "—");
    setText("ds-total-cols", "17"); // fixed for transactions table
    setText("ds-fraud-count", stats.fraud_count?.toLocaleString() || "—");
    setText("ds-avg-amt", stats.amt_mean ? `$${Number(stats.amt_mean).toFixed(2)}` : "—");
}
```

### 2.2 Prediction Explanations

**Backend** (`model_predict.py`):

```python
def _get_top_features(feature_vector, feature_names, metadata, probability):
    """
    Compute approximate feature contributions for explanation.
    Uses feature_importance from metadata if available.
    Returns top-3 features with contribution scores.
    """
    feature_importance = metadata.get("feature_importance", [])
    if not feature_importance:
        return []

    # Build name→importance map
    imp_map = {f["name"]: f["importance"] for f in feature_importance}

    contributions = []
    for i, name in enumerate(feature_names):
        if name in imp_map and i < len(feature_vector):
            raw_contrib = imp_map[name] * float(feature_vector[i])
            contributions.append({
                "feature": name,
                "contribution": round(abs(raw_contrib), 4),
                "direction": "+" if raw_contrib > 0 else "-",
            })

    contributions.sort(key=lambda x: x["contribution"], reverse=True)
    return contributions[:3]
```

**App endpoint update** (`app.py`):
```python
@app.post("/api/model/predict")
async def predict_transaction(data: PredictionRequest):
    # ... existing code ...
    result = predict_single(features)

    # Add explanations if available
    try:
        metadata = load_metadata()
        feature_names = metadata.get("feature_names", [])
        feature_vector = [features.get(f, 0) for f in feature_names]
        result["explanations"] = _get_top_features(
            feature_vector, feature_names, metadata, result["probability"]
        )
    except Exception:
        pass  # Graceful: omit explanations if anything fails

    return result
```

### 2.3 Pipeline SQL Counts

**Backend** (`app.py`, `pipeline_status` endpoint):

```python
@app.get("/api/pipeline/status")
async def pipeline_status():
    # ... existing parquet status code ...

    # NEW: SQL counts
    sql_counts = None
    try:
        from backend.src.db import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            sql_counts = {
                "transactions": conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar(),
                "customers": conn.execute(text("SELECT COUNT(*) FROM customers")).scalar(),
                "merchants": conn.execute(text("SELECT COUNT(*) FROM merchants")).scalar(),
                "rejected_records": conn.execute(text("SELECT COUNT(*) FROM rejected_records")).scalar(),
            }
    except Exception as e:
        logger.warning(f"SQL counts unavailable: {e}")
        sql_counts = {"error": str(e)}

    return {
        "bronze": get_bronze_stats(),
        "silver": get_silver_stats(),
        "gold": get_gold_stats(),
        "sql_counts": sql_counts,       # NEW
    }
```

**Frontend** (`pipeline.js`, `loadPipelineStatus`):

```javascript
async function loadPipelineStatus() {
    const status = await api('/api/pipeline/status');
    if (!status) return;

    updateLayerCard('bronze', status.bronze);
    updateLayerCard('silver', status.silver);
    updateLayerCard('gold', status.gold);
    updateSQLCounts(status.sql_counts);    // NEW
}

function updateSQLCounts(counts) {
    if (!counts || counts.error) return;
    const el = document.getElementById('pipeline-sql-counts');
    if (!el) return;
    el.innerHTML = `
        <div class="sql-counts-grid">
            <div class="stat"><strong>SQL Transactions:</strong> ${counts.transactions?.toLocaleString() || '—'}</div>
            <div class="stat"><strong>SQL Customers:</strong> ${counts.customers?.toLocaleString() || '—'}</div>
            <div class="stat"><strong>SQL Merchants:</strong> ${counts.merchants?.toLocaleString() || '—'}</div>
            <div class="stat"><strong>SQL Rejected:</strong> ${counts.rejected_records?.toLocaleString() || '—'}</div>
        </div>
    `;
}
```

### 2.4 CSS Additions

```css
/* Data source toggle */
.datasource-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;
}
.toggle-btn {
    padding: 6px 14px;
    border: 1px solid #475569;
    background: #1e293b;
    color: #94a3b8;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
}
.toggle-btn.active {
    background: #3b82f6;
    color: white;
    border-color: #3b82f6;
}

/* Explanation panel */
.demo-explanation {
    margin-top: 16px;
}
.explanation-card {
    background: #1e293b;
    border-radius: 8px;
    padding: 16px;
    border-left: 3px solid #3b82f6;
}
.explanation-card h4 {
    margin-top: 0;
    color: #e2e8f0;
}
.explanation-list {
    list-style: none;
    padding: 0;
}
.explanation-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
}
.contribution-bar {
    height: 8px;
    border-radius: 4px;
    min-width: 4px;
}
.contribution-bar.positive { background: #22c55e; }
.contribution-bar.negative { background: #ef4444; }

/* SQL counts in pipeline */
.sql-counts-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-top: 12px;
    padding: 12px;
    background: #0f172a;
    border-radius: 8px;
}
```

### 2.5 Error Handling (Frontend)

| Scenario | UX Behavior |
|----------|-------------|
| SQL endpoints unavailable | Toggle defaults to parquet; SQL button disabled with tooltip |
| Feature importance missing | Explanation panel hidden (id="demo-explanation" empty) |
| SQL counts query fails | `sql_counts` block hidden; parquet stats still visible |
| Prediction fails | Existing error handling (warning card) preserved |

### 2.6 Testing Approach

| Test | Type | Description |
|------|------|-------------|
| Data source toggle persists in localStorage | Unit | Switch source, reload page, verify toggle state |
| SQL view renders correct KPIs | Integration | Mock `/api/sql/*` responses, verify DOM elements |
| Parquet view still works after toggle | Integration | Toggle to SQL, toggle back to Parquet, verify same data |
| Explanation panel shows top 3 features | Integration | Mock prediction with explanations, verify 3 items rendered |
| Explanation panel hidden when no data | Integration | Mock prediction without explanations, verify panel absent |
| Pipeline status shows SQL counts | Integration | Mock status with `sql_counts`, verify 4 numbers displayed |
| Merchant filter works on SQL endpoint | Unit (backend) | `GET /api/sql/transactions?merchant=kohler` → only matching rows |
| CSS doesn't break existing layout | Visual | Manual inspection of all tabs after changes |

### 2.7 Migration / Rollback

- **Frontend toggle state**: Stored in `localStorage`. If PR reverted, user clears localStorage or the key is ignored.
- **SQL endpoint new params**: Additive; removing the PR removes the `merchant` filter but no breakage.
- **CSS additions**: Removing the CSS rules leaves browsers with no matching styles → elements gracefully invisible.
- **`sql_counts` in `/api/pipeline/status`**: Old frontend ignores unknown JSON keys.

## 3. Dependencies

- **PR-3 depends on PR-1**: Needs `/api/sql/transactions`, `/api/sql/stats`, `/api/sql/kpis` endpoints.
- **PR-3 depends on PR-2**: Needs `feature_importance` metadata from the trained model for explanations.
- **Can PR-3 be parallelized with PR-2?**: The frontend toggle can be developed against mock endpoints independently. However, the prediction explanations require PR-2's metadata changes. Recommend sequential merge.

## 4. Acceptance Criteria

| ID | Criteria | Verification |
|----|----------|-------------|
| AC-3.1 | Dataset tab toggle switches between Parquet/SQL views | Manual: click toggle, observe data source change, KPIs still display |
| AC-3.2 | Toggle state persists after page reload | Toggle to SQL, refresh page → SQL is still selected |
| AC-3.3 | SQL view shows same conceptual data as Parquet view | Side-by-side manual comparison of KPI values |
| AC-3.4 | Model demo shows top-3 driving features when available | Train model on real data, run demo prediction, observe explanation panel |
| AC-3.5 | Model demo gracefully omits explanation when unavailable | Delete feature_importance from metadata, run demo → no explanation panel |
| AC-3.6 | Pipeline status displays SQL table row counts | Run pipeline, check Pipeline tab → "SQL: N transactions \| N customers \| N merchants" |
| AC-3.7 | SQL transactions endpoint supports `merchant` filter | `curl "/api/sql/transactions?merchant=kohler"` → only matching merchants |
| AC-3.8 | All existing tabs still function normally | Manual click-through of all 9 tabs after changes |
| AC-3.9 | No console errors during normal operation | Open DevTools, navigate all tabs → zero errors |

---

## Cross-PR: Global Rollout Strategy

### Merge Order (Strictly Sequential)

```
PR-1 (fix/seed-and-sql-reads)
    │
    ├── Deploy & verify:
    │   ├── Real CSV is loaded (555K rows in DB)
    │   ├── SQL endpoints return data
    │   └── Incremental load works
    │
    ▼
PR-2 (feat/scaling-and-features)
    │
    ├── Deploy & verify:
    │   ├── Train model on 555K rows
    │   ├── Probabilities span 0-1
    │   ├── Scaler persisted correctly
    │   └── Predictions still work
    │
    ▼
PR-3 (feat/frontend-and-advanced-sql)
    │
    └── Deploy & verify:
        ├── SQL toggle works in frontend
        ├── Explanations render
        └── Pipeline counts visible
```

### Risk Mitigation per PR

| PR | Key Risk | Mitigation |
|----|----------|------------|
| PR-1 | DB connection fails under load | Connection pool with `pool_pre_ping`; endpoint returns 503 gracefully |
| PR-1 | Customer upsert corrupts data | Upsert only updates explicitly listed fields; no DROP/CASCADE |
| PR-2 | Scaler mismatch → silent bad predictions | Feature name ordering contract enforced in tests; scaler path validated at load |
| PR-2 | Retrained model worse than previous | Old model preserved; `best_model.joblib` backed up before overwrite |
| PR-3 | Frontend toggle breaks existing views | Toggle defaults to "parquet" (existing behavior); JS errors caught and logged |
| PR-3 | CSS conflicts with existing styles | All new classes prefixed with `toggle-`, `explanation-`, `sql-` to avoid collisions |

### Rollback Procedure (Full)

1. Revert PR-3 first (frontend → least critical).
2. Revert PR-2 (model → old model still works via backward-compat).
3. Revert PR-1 last (data → SQL endpoints removed, but DB remains populated).
4. After revert: DB tables remain (`pipeline_load_state` is harmless). Delete `scaler.joblib` if orphaned.

---

## Summary of Key Design Decisions

### PR-1
- **Shared engine in `db.py`** with `pool_pre_ping` — avoids scattered `create_engine()` calls and ensures connection reuse.
- **`pipeline_load_state` as a separate table** — not appended to `pipeline_logs` because load state is mutable (upsert) while logs are append-only.
- **Customer upsert updates 7 mutable fields** — no schema changes needed, just `ON CONFLICT DO UPDATE SET`.
- **SQL endpoints use raw `text()` queries** — avoids ORM overhead for read-only endpoints; keeps diff small.

### PR-2
- **Scaler persisted by `build_features()`, loaded by `predict_single()`** — clear separation: feature engineering owns the scaler artifact.
- **`category_fraud_rate_map` stored in metadata** — avoids recomputing train-only statistics at inference time.
- **Threshold tuning in `evaluate_model()`** — evaluation is the right place because it has access to test set probabilities.
- **Backward compatibility via `scaler_path` absence check** — old models without scaler still work; warning nudges retraining.

### PR-3
- **Frontend toggle driven by `localStorage`** — simple, no backend state needed; survives page refreshes.
- **SQL counts in existing `/api/pipeline/status`** — avoids creating yet another endpoint; additive JSON key.
- **Feature explanations as `_get_top_features()`** — approximate, uses feature importance × feature value; not full SHAP but good enough for a UI explanation panel.
- **CSS prefixing (`toggle-`, `explanation-`, `sql-`)** — prevents style collisions with existing CSS.
