# Tasks: Comprehensive Data Pipeline Overhaul

**Change ID**: `fix-data-pipeline-sql-endpoints`
**Date**: 2026-05-24
**Status**: `tasks-defined`
**PR count**: 3

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 1,080 total (~350 + ~380 + ~350) |
| 400-line budget risk | Low (each PR is within budget) |
| Chained PRs recommended | Yes |
| Suggested split | PR-1 → PR-2 → PR-3 (stacked) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low

---

## Task Summary

| PR | Branch | Files | Tasks | ~Lines |
|----|--------|-------|-------|--------|
| PR-1 | `fix/seed-and-sql-reads` | 7 (1 new) | 21 | ~350 |
| PR-2 | `feat/scaling-and-features` | 8 (2 new) | 22 | ~380 |
| PR-3 | `feat/frontend-and-advanced-sql` | 8 (0 new) | 25 | ~350 |
| **Total** | | **23** | **68** | **~1,080** |

---

# PR-1: `fix/seed-and-sql-reads` (21 tasks)

**Branch**: `fix/seed-and-sql-reads` (from `main`)
**Estimated**: ~350 changed lines
**Dependencies**: None — can start immediately

---

## Phase 1: Setup (tasks 1.1–1.3)

### Task 1.1 — Create feature branch and verify environment

**Work**:
1. From `main`, create branch: `git checkout -b fix/seed-and-sql-reads`
2. Verify PostgreSQL is running: `pg_isready`
3. Verify `.env` is configured with `DATABASE_URL`
4. Run existing tests to confirm baseline: `pytest backend/tests/ -v --timeout=60`

**Acceptance**: All existing tests pass. Branch exists. DB reachable.

**Files**: None (env check only)

---

### Task 1.2 — Add settings constants for PR-1

**Work**:
In `backend/config/settings.py`, add 3 constants after the existing `MODEL_DECISION_THRESHOLD` line:

```python
# --- PR-1: Seed gate, DB pool ---
ALLOW_SYNTHETIC_SEED_DEFAULT = os.getenv("ALLOW_SYNTHETIC_SEED", "")
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
```

**Acceptance**: `from backend.config.settings import DB_POOL_SIZE` works. Default values: 5, 10.

**Files**: `backend/config/settings.py` (~+8 lines)

---

### Task 1.3 — Create shared DB engine factory

**Work**:
Create `backend/src/db.py` with `get_engine()`:

```python
from sqlalchemy import create_engine

from backend.config.settings import DATABASE_URL, DB_POOL_SIZE, DB_MAX_OVERFLOW

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine
```

**Acceptance**: `from backend.src.db import get_engine; e = get_engine(); e.connect()` succeeds.

**Files**: `backend/src/db.py` (~+20 lines, NEW)

---

## Phase 2: Seed data gating (tasks 1.4–1.5)

### Task 1.4 — Gate synthetic seed generation in `seed.py`

**Work**:
1. In `backend/seed.py`, replace the module-level `SAMPLE_SIZE = 500` with an import-based approach using `DATA_SAMPLE_SIZE` from settings.
2. Modify `seed_data()` to add a 3-way gate:
   - If `RAW_CSV` exists → print "skipping" and continue
   - If `RAW_CSV` missing AND `ALLOW_SYNTHETIC_SEED=true` → generate `DATA_SAMPLE_SIZE` rows
   - Otherwise → print error and `sys.exit(1)`
3. Remove the `SAMPLE_SIZE = 500` constant.
4. In the pipeline-run section, call `ingest(sample_size=None)`, `clean(sample_size=None)`, `validate(sample_size=None)` to use full data (not the old `SAMPLE_SIZE`).

**Acceptance**:
- Real CSV exists → `python backend/seed.py` prints "skipping generation" and runs pipeline on full data.
- Real CSV missing + `ALLOW_SYNTHETIC_SEED=true` → generates `DATA_SAMPLE_SIZE` (default 10,000) rows.
- Real CSV missing + no env var → exits with error code 1.

**Files**: `backend/seed.py` (~+30 / -10 lines)

---

### Task 1.5 — Improve ingestion error message

**Work**:
In `backend/src/ingestion.py`, in the `ingest()` function, when the source CSV is missing, change the error result to include the full resolved path:

```python
return {"status": "error", "error": f"Raw CSV not found at {resolved_path}"}
```

**Acceptance**: `ingest()` returns `{"status": "error", "error": "Raw CSV not found at /path/to/csv"}` when file is absent.

**Files**: `backend/src/ingestion.py` (~+3 / -2 lines)

---

## Phase 3: Loader improvements (tasks 1.6–1.9)

### Task 1.6 — Add `pipeline_load_state` table to DDL

**Work**:
In `backend/src/loader.py`, inside `create_tables()`, add the DDL for `pipeline_load_state` before the final `commit()`:

```sql
CREATE TABLE IF NOT EXISTS pipeline_load_state (
    id SERIAL PRIMARY KEY,
    source_table VARCHAR(64) NOT NULL UNIQUE,
    last_loaded_timestamp BIGINT,
    rows_loaded INTEGER,
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Acceptance**: After `create_tables()`, `SELECT * FROM pipeline_load_state` returns empty result (no error).

**Files**: `backend/src/loader.py` (~+10 lines)

---

### Task 1.7 — Add `incremental` parameter and cutoff logic to `load()`

**Work**:
1. Change `load()` signature: `def load(sample_size: int | None = None, incremental: bool = False) -> dict:`
2. After `df = pd.read_parquet(gold_path)`, add the incremental filter block:
   - Query `MAX(unix_time)` from `transactions` table
   - Query `last_loaded_timestamp` from `pipeline_load_state`
   - Use `max()` of both as cutoff
   - Filter: `df = df[df["unix_time"] > cutoff]` if `cutoff > 0`
   - Log how many new rows found
3. If `len(df) == 0`, return early with `{"status": "success", "rows_inserted": 0}`

**Acceptance**:
- `load(incremental=True)` on empty DB → inserts all rows.
- `load(incremental=True)` twice with no new data → second call returns `rows_inserted: 0`.

**Files**: `backend/src/loader.py` (~+35 lines)

---

### Task 1.8 — Write `pipeline_load_state` row after successful load

**Work**:
At the end of `load()`, after transaction insert succeeds:
1. Get `last_ts = int(df["unix_time"].max())`
2. Execute upsert into `pipeline_load_state` via `ON CONFLICT (source_table) DO UPDATE SET`
3. Add `"rows_inserted"` and `"last_loaded_timestamp"` to the result dict

**Acceptance**: After `load(incremental=True)`, `SELECT * FROM pipeline_load_state` shows one row with correct `last_loaded_timestamp` and `rows_loaded`.

**Files**: `backend/src/loader.py` (~+20 lines)

---

### Task 1.9 — Change customer insert from DO NOTHING to DO UPDATE (upsert)

**Work**:
In the customer insert SQL in `load()`, change `ON CONFLICT (customer_id) DO NOTHING` to:

```sql
ON CONFLICT (customer_id) DO UPDATE SET
    gender = EXCLUDED.gender,
    city = EXCLUDED.city,
    state = EXCLUDED.state,
    zip = EXCLUDED.zip,
    city_pop = EXCLUDED.city_pop,
    job = EXCLUDED.job,
    age_at_transaction = EXCLUDED.age_at_transaction
```

**Acceptance**: Manually change `city_pop` in Gold parquet for an existing customer, re-run `load()`, verify `SELECT city_pop FROM customers WHERE customer_id = '...'` shows updated value.

**Files**: `backend/src/loader.py` (~+8 / -1 lines)

---

## Phase 4: SQL read endpoints (tasks 1.10–1.15)

### Task 1.10 — Add `GET /api/sql/transactions` endpoint

**Work**:
In `backend/app.py`, add a new route:

```python
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
```

Build dynamic `WHERE` clauses from query params. Execute `COUNT(*)` for `meta.total`, then `SELECT ... FROM transactions t LEFT JOIN merchants m ON t.merchant_id = m.merchant_id` with `ORDER BY t.trans_date_trans_time DESC LIMIT :limit OFFSET :offset`.

Return `{"transactions": [...], "meta": {"total": N, "limit": L, "offset": O, "next_offset": next}}`.

Handle `get_engine()` failure → `503`.

**Acceptance**:
- `curl "/api/sql/transactions?limit=5"` → 5 rows with correct `meta.total`.
- `curl "/api/sql/transactions?fraud=1"` → only fraud rows.
- `limit=600` → 422 error.

**Files**: `backend/app.py` (~+60 lines)

---

### Task 1.11 — Add `GET /api/sql/transactions/{trans_num}` endpoint

**Work**:
Add route with path param `trans_num`. Query single row. Return 200 if found, 404 with detail message if not.

```python
@app.get("/api/sql/transactions/{trans_num}")
async def sql_transaction_by_id(trans_num: str):
```

**Acceptance**:
- `curl "/api/sql/transactions/<existing-trans-num>"` → 200 with full transaction object.
- `curl "/api/sql/transactions/nonexistent"` → 404 `{"detail": "Transaction not found: nonexistent"}`.

**Files**: `backend/app.py` (~+20 lines)

---

### Task 1.12 — Add `GET /api/sql/stats` endpoint

**Work**:
Add route that executes SQL aggregates:
- `COUNT(*)` → total_count
- `SUM(CASE WHEN is_fraud=1 THEN 1 ELSE 0 END)` → fraud_count
- `AVG(amt)`, `MAX(amt)`, `MIN(amt)`, `STDDEV(amt)`
- `COUNT(*) GROUP BY category` → by_category
- `100.0 * COUNT(*) / (SELECT COUNT(*) FROM transactions)` per category for fraud_pct

**Acceptance**: Compare values with `SELECT COUNT(*)...` in psql — within 0.01% tolerance.

**Files**: `backend/app.py` (~+25 lines)

---

### Task 1.13 — Add `GET /api/sql/kpis` endpoint

**Work**:
Add route that mirrors `/api/kpis` but sourced from SQL. Include `source: "postgresql"` in response.

Fields: `total_records`, `fraud_count`, `legit_count`, `fraud_pct`, `amt_mean`, `amt_median` (use `PERCENTILE_CONT(0.5)`), `amt_max`, `completeness_pct`, `status: "available"`, `timestamp`.

**Acceptance**:
- `curl /api/sql/kpis` returns all fields.
- `fraud_pct` matches parquet KPIs within 1% tolerance.

**Files**: `backend/app.py` (~+25 lines)

---

### Task 1.14 — Add `GET /api/sql/stats` completeness calculation

**Work**:
Enhance `GET /api/sql/stats` with completeness: calculate `100.0 * COUNT(trans_num) / COUNT(*)` for critical columns (`amt`, `category`, `trans_date_trans_time`, `city`, `state`, `merchant_id`). Return average completeness as `completeness_pct`. Add `date_min` and `date_max` from `MIN/MAX(trans_date_trans_time)`.

**Acceptance**: `completeness_pct` in response is > 90% with real data. `date_min` and `date_max` are ISO dates.

**Files**: `backend/app.py` (~+10 lines in Task 1.12 function)

---

### Task 1.15 — Add 503 error handling to all SQL endpoints

**Work**:
Wrap every SQL endpoint's `get_engine()` call in try/except `RuntimeError`. On failure, raise `HTTPException(status_code=503, detail="Database connection failed. Verify DATABASE_URL and that PostgreSQL is running.")`.

**Acceptance**: Stop PostgreSQL, hit `/api/sql/transactions` → 503 with message.

**Files**: `backend/app.py` (~+5 lines)

---

## Phase 5: Tests (tasks 1.16–1.19)

### Task 1.16 — Create `backend/tests/test_sql_endpoints.py` skeleton

**Work**:
Create test file with `pytest` fixtures:
- `client`: FastAPI `TestClient(app)`
- `populated_db`: Runs `seed_data()` with synthetic data, yields client connected to test DB.

Add `pytest` markers. Use `pytest.mark.integration` for tests needing real DB.

**Acceptance**: File imports cleanly. Fixtures run without error.

**Files**: `backend/tests/test_sql_endpoints.py` (~+20 lines, NEW)

---

### Task 1.17 — Write SQL endpoint read tests

**Work**:
Add 5 test functions:
1. `test_sql_transactions_pagination` — `limit=10` returns 10 rows + correct meta.
2. `test_sql_transactions_fraud_filter` — `fraud=1` returns only `is_fraud=1` rows.
3. `test_sql_transaction_by_id_found` — existing `trans_num` returns 200.
4. `test_sql_transaction_by_id_not_found` — nonexistent returns 404.
5. `test_sql_stats_and_kpis` — both endpoints return expected keys and numeric values.

**Acceptance**: `pytest backend/tests/test_sql_endpoints.py -v -k "test_sql"` — all 5 pass.

**Files**: `backend/tests/test_sql_endpoints.py` (~+40 lines)

---

### Task 1.18 — Write loader incremental test

**Work**:
Add to `backend/tests/test_sql_endpoints.py`:
`test_loader_incremental_idempotent` — calls `load(incremental=True)` twice. First call inserts rows; second call returns `rows_inserted: 0`.

**Acceptance**: Test passes on populated DB.

**Files**: `backend/tests/test_sql_endpoints.py` (~+15 lines)

---

### Task 1.19 — Write customer upsert test

**Work**:
Add to `backend/tests/test_sql_endpoints.py`:
`test_customer_upsert_updates_fields` — insert customer with `city_pop=1000`, re-load with `city_pop=5000`, verify DB has 5000.

**Acceptance**: Test passes.

**Files**: `backend/tests/test_sql_endpoints.py` (~+15 lines)

---

## Phase 6: Commit plan & cleanup (tasks 1.20–1.21)

### Task 1.20 — Commit work in logical order

**Conventional commits**:
1. `feat(db): add shared engine factory with connection pooling` — `backend/src/db.py`, `backend/config/settings.py`
2. `fix(seed): gate synthetic generation behind ALLOW_SYNTHETIC_SEED` — `backend/seed.py`
3. `fix(ingestion): return full path in missing-file error` — `backend/src/ingestion.py`
4. `feat(loader): add incremental load mode with pipeline_load_state tracking` — `backend/src/loader.py`
5. `feat(loader): change customer insert to upsert` — `backend/src/loader.py`
6. `feat(api): add 4 SQL read endpoints with pagination and filtering` — `backend/app.py`
7. `test: add SQL endpoint and loader incremental tests` — `backend/tests/test_sql_endpoints.py`

**Acceptance**: 7 commits on `fix/seed-and-sql-reads`. Each commit message follows conventional commit format.

**Files**: Git metadata only

---

### Task 1.21 — Final cleanup and verification for PR-1

**Work**:
1. Run full test suite: `pytest backend/tests/ -v`
2. Run linter: `ruff check backend/` (or `flake8`)
3. Verify `python backend/seed.py` completes with real CSV present
4. Start FastAPI: `uvicorn backend.app:app --reload` and manually test all 4 SQL endpoints
5. Run 20 concurrent requests: `ab -n 100 -c 20 http://localhost:8000/api/sql/transactions?limit=10`
6. Verify zero pool exhaustion errors

**Acceptance**: All tests pass. Linter clean. All endpoints respond correctly. No concurrency errors.

**Files**: None (verification only)

---

# PR-2: `feat/scaling-and-features` (22 tasks)

**Branch**: `feat/scaling-and-features` (from `fix/seed-and-sql-reads`)
**Estimated**: ~380 changed lines
**Dependencies**: PR-1 must be merged. DB should be populated with real data (at least 10K rows).

---

## Phase 1: Setup (tasks 2.1–2.2)

### Task 2.1 — Create feature branch from PR-1 merge base

**Work**:
1. After PR-1 is merged to main, pull main: `git checkout main && git pull`
2. Create branch: `git checkout -b feat/scaling-and-features`
3. Verify PR-1 changes are present: check `backend/src/db.py` exists
4. Run existing tests: `pytest backend/tests/ -v --timeout=60`

**Acceptance**: All PR-1 tests pass. `backend/src/db.py` exists.

**Files**: None (env check only)

---

### Task 2.2 — Add scaler path setting

**Work**:
In `backend/config/settings.py`, add after existing model constants:

```python
SCALER_PATH = MODELS_DIR / "scaler.joblib"
MODEL_MIN_PRECISION = float(os.getenv("MODEL_MIN_PRECISION", "0.0"))
```

**Acceptance**: `from backend.config.settings import SCALER_PATH; print(SCALER_PATH)` outputs `models/scaler.joblib`.

**Files**: `backend/config/settings.py` (~+5 lines)

---

## Phase 2: Feature engineering — scaling & interactions (tasks 2.3–2.6)

### Task 2.3 — Add interaction features to `build_features()`

**Work**:
In `backend/src/features.py`, after encoding is complete and before building `X_train`/`X_test` arrays, add:

1. `amt_per_city_pop = amt / max(city_pop, 1)` for train and test
2. `distance_x_amt = distance_km * amt` for train and test
3. `hour_is_night = 1 if trans_hour in {0,1,2,3,4,5,22,23} else 0` for train and test
4. Compute `category_fraud_rate_map` from `train_df.groupby("category")["is_fraud"].mean()`
5. Compute `global_fraud_rate` from `train_df["is_fraud"].mean()`
6. Apply mapping to train and test (test receives train rates; unknown → global_fraud_rate)

**Acceptance**: After `build_features()`, `feature_names` includes the 4 new columns. No leakage: test `category_fraud_rate` values match train mapping, not test's own fraud rates.

**Files**: `backend/src/features.py` (~+30 lines)

---

### Task 2.4 — Add StandardScaler to `build_features()`

**Work**:
After interaction features are computed, add:

1. Define `numeric_feature_cols = NUMERIC_FEATURES + ["amt_per_city_pop", "distance_x_amt", "category_fraud_rate"]` (exclude `hour_is_night` — it's binary)
2. `scaler = StandardScaler().fit(train_df[numeric_feature_cols])`
3. Transform train and test with scaler
4. Save scaler: `joblib.dump(scaler, SCALER_PATH)`
5. Add `"scaler"` and `"scaler_path"` keys to result dict

**Acceptance**: `scaler.joblib` created at `models/scaler.joblib`. Train numeric columns have mean ≈ 0, std ≈ 1.

**Files**: `backend/src/features.py` (~+30 lines)

---

### Task 2.5 — Update `feature_names` list to include all 13 features

**Work**:
Update `feature_names` in `build_features()`:
```python
feature_names = NUMERIC_FEATURES + ["category_encoded", "gender_encoded"] + \
                ["amt_per_city_pop", "distance_x_amt", "hour_is_night", "category_fraud_rate"]
```
Add `"feature_names"`, `"n_features"`, `"category_fraud_rate_map"`, `"global_fraud_rate"` to result dict.

**Acceptance**: `result["feature_names"]` has 13 entries in exact order above. `result["n_features"]` == 13.

**Files**: `backend/src/features.py` (~+15 lines)

---

### Task 2.6 — Edge case handling in `build_features()`

**Work**:
- Handle `city_pop == 0` → use `max(city_pop, 1)` for division.
- Handle categories in test not in train → `category_fraud_rate = global_fraud_rate`.
- Verify scaler handles NaN-free input (imputation already done before scaling).
- Add a logger.info message: "Scaler saved to {scaler_path}".

**Acceptance**: Unit test with `city_pop=0` row → no division by zero. Unknown category in test → receives global rate.

**Files**: `backend/src/features.py` (~+10 lines, edits within existing additions)

---

## Phase 3: Model training updates (tasks 2.7–2.9)

### Task 2.7 — Pass scaler and fraud rate data through `train_models()`

**Work**:
1. Add `scaler_path: str | None = None` parameter to `train_models()`.
2. Add `category_fraud_rate_map: dict | None = None` and `global_fraud_rate: float | None = None` parameters.
3. In metadata dict, add:
   ```python
   "scaler_path": scaler_path or str(SCALER_PATH),
   "category_fraud_rate_map": category_fraud_rate_map or {},
   "global_fraud_rate": float(global_fraud_rate) if global_fraud_rate is not None else 0.05,
   ```
4. Keep `decision_threshold` in metadata (already present).

**Acceptance**: After training, `model_metadata.json` contains `scaler_path`, `category_fraud_rate_map`, `global_fraud_rate`.

**Files**: `backend/src/model_train.py` (~+25 lines)

---

### Task 2.8 — Update training caller in `app.py`

**Work**:
In `backend/app.py`, in the `/api/model/train` handler, pass new params:
```python
train_result = train_models(
    feature_data["X_train"],
    feature_data["y_train"],
    feature_data["feature_names"],
    category_mapping=feature_data.get("category_mapping"),
    gender_mapping=feature_data.get("gender_mapping"),
    scaler_path=feature_data.get("scaler_path"),
    category_fraud_rate_map=feature_data.get("category_fraud_rate_map"),
    global_fraud_rate=feature_data.get("global_fraud_rate"),
)
```

**Acceptance**: `POST /api/model/train` completes successfully, metadata includes new keys.

**Files**: `backend/app.py` (~+5 lines)

---

### Task 2.9 — Add threshold tuning to `evaluate_model()`

**Work**:
In `backend/src/model_evaluate.py`, add `_tune_threshold()` helper:
1. Compute `precision_recall_curve(y_test, y_prob)`
2. For each threshold, compute F1 = `2 * P * R / (P + R)`
3. Optionally filter by `min_precision` if `MODEL_MIN_PRECISION > 0`
4. Return `(best_threshold, best_f1)`

Call from `evaluate_model()`. Replace the `decision_threshold` in the result with the tuned one. Save tuned threshold to `model_metadata.json` by updating it after evaluation.

**Acceptance**: On real data, tuned threshold differs from default (0.3). F1 ≥ baseline.

**Files**: `backend/src/model_evaluate.py` (~+30 lines)

---

## Phase 4: Prediction with scaler (tasks 2.10–2.12)

### Task 2.10 — Add scaler loading and backward-compat to `predict_single()`

**Work**:
In `backend/src/model_predict.py`:
1. Add `_load_scaler()` helper — reads `scaler_path` from metadata, loads via `joblib`. Raises clear `RuntimeError` if missing.
2. Modify `predict_single()`:
   - After building feature vector, compute interaction features (same as `build_features()`).
   - Load scaler.
   - Apply scaler only to the numeric columns (not `hour_is_night`, not encoded cols).
   - If `scaler_path` is absent from metadata → log warning, skip scaling (backward compat).

**Acceptance**:
- Prediction with real scaler → uses scaled features.
- Prediction with old model (no scaler_path) → logs warning, still produces result.
- Prediction with missing scaler file → 500 with "Scaler artifact missing".

**Files**: `backend/src/model_predict.py` (~+50 lines)

---

### Task 2.11 — Update `predict_batch()` with scaler

**Work**:
In `backend/src/model_predict.py`, modify `predict_batch()`:
1. Compute interaction features for all rows.
2. Load and apply scaler to numeric columns.
3. Same backward-compat logic as `predict_single()`.

**Acceptance**: Batch prediction on test data produces same probabilities as single predictions.

**Files**: `backend/src/model_predict.py` (~+20 lines)

---

### Task 2.12 — Update `prepare_transaction_for_prediction()` with interaction features

**Work**:
Add interaction feature computation to `prepare_transaction_for_prediction()`:
1. Load `category_fraud_rate_map` and `global_fraud_rate` from metadata.
2. Compute 4 interaction features from raw transaction dict.
3. Return dict with all 13 features in correct order.

**Acceptance**: Output dict has 13 keys matching `feature_names` order.

**Files**: `backend/src/model_predict.py` (~+25 lines)

---

## Phase 5: API updates for PR-2 (task 2.13)

### Task 2.13 — Add feature importance to `/api/model/feature-importance` response

**Work**:
The existing `/api/model/feature-importance` endpoint reads from `evaluation_report.json`. After PR-2, this report will contain 13 features instead of 9. Verify the endpoint handles this correctly. Add `feature_count: len(features)` to response.

**Acceptance**: `GET /api/model/feature-importance` returns 13 features after training on new feature set.

**Files**: `backend/app.py` (~+5 lines, optional)

---

## Phase 6: Tests (tasks 2.14–2.20)

### Task 2.14 — Create `backend/tests/test_features_scaling.py`

**Work**:
Create test file with fixture that calls `build_features()` on small synthetic Gold parquet.

**Acceptance**: File imports cleanly, fixture works.

**Files**: `backend/tests/test_features_scaling.py` (~+10 lines, NEW)

---

### Task 2.15 — Test scaler fit/transform round-trip

**Work**:
`test_scaler_fit_transform` — verify train numeric columns have mean ≈ 0 and std ≈ 1. Verify test columns transformed with same scaler.

**Acceptance**: `np.abs(train_scaled_mean) < 1e-6`, `np.abs(train_scaled_std - 1.0) < 0.1`.

**Files**: `backend/tests/test_features_scaling.py` (~+12 lines)

---

### Task 2.16 — Test scaler persistence

**Work**:
`test_scaler_persistence` — save scaler via `joblib.dump`, load it, transform same data, verify `allclose(original_transform, loaded_transform)`.

**Acceptance**: `np.allclose` returns True within 1e-10.

**Files**: `backend/tests/test_features_scaling.py` (~+12 lines)

---

### Task 2.17 — Test interaction features and no-leakage

**Work**:
1. `test_interaction_features_present` — verify 4 new columns exist and have non-zero variance.
2. `test_category_fraud_rate_no_leakage` — verify test rows get fraud rates from train mapping, not their own.

**Acceptance**: Both tests pass.

**Files**: `backend/tests/test_features_scaling.py` (~+20 lines)

---

### Task 2.18 — Create `backend/tests/test_model_predict_scaled.py`

**Work**:
Create test file with fixture that trains a minimal model with scaler.

**Acceptance**: File imports cleanly.

**Files**: `backend/tests/test_model_predict_scaled.py` (~+10 lines, NEW)

---

### Task 2.19 — Test scaled prediction and scaler errors

**Work**:
1. `test_scaled_prediction` — train model, predict single transaction, verify scaler was applied (probabilities differ from unscaled).
2. `test_missing_scaler_error` — delete `scaler.joblib`, call predict → verify clear RuntimeError.
3. `test_old_model_backward_compat` — create metadata without `scaler_path` → predict succeeds with warning.

**Acceptance**: All 3 tests pass.

**Files**: `backend/tests/test_model_predict_scaled.py` (~+30 lines)

---

### Task 2.20 — Test threshold tuning

**Work**:
In existing `backend/tests/test_model_evaluate.py` (or create if absent), add `test_threshold_tuning_improves_f1` — on separable data, tuned threshold differs from default 0.3.

**Acceptance**: Test passes.

**Files**: `backend/tests/test_model_evaluate.py` or new test file (~+15 lines)

---

## Phase 7: Commit plan & cleanup (tasks 2.21–2.22)

### Task 2.21 — Commit work in logical order

**Conventional commits**:
1. `feat(features): add 4 interaction features and StandardScaler` — `backend/src/features.py`, `backend/config/settings.py`
2. `feat(train): persist scaler path and fraud rate mapping in metadata` — `backend/src/model_train.py`, `backend/app.py`
3. `feat(evaluate): add threshold tuning via precision-recall curve` — `backend/src/model_evaluate.py`
4. `feat(predict): load scaler and compute interaction features at inference` — `backend/src/model_predict.py`
5. `feat(predict): add backward compatibility for old models without scaler` — `backend/src/model_predict.py`
6. `test: add scaler, interaction features, and prediction tests` — `backend/tests/test_features_scaling.py`, `backend/tests/test_model_predict_scaled.py`

**Acceptance**: 6 commits on `feat/scaling-and-features`.

**Files**: Git metadata only

---

### Task 2.22 — Final cleanup and verification for PR-2

**Work**:
1. Run full test suite: `pytest backend/tests/ -v`
2. Train model on real data: `curl -X POST localhost:8000/api/model/train`
3. Verify `scaler.joblib` exists: `ls -la models/scaler.joblib`
4. Verify metadata has new keys: `cat models/model_metadata.json | python -m json.tool | grep -E "(scaler_path|category_fraud_rate_map|global_fraud_rate)"`
5. Run prediction: `curl -X POST localhost:8000/api/model/predict -H "Content-Type: app/json" -d '{...}'`
6. Verify probability range: check evaluation report for min/max probability on test set

**Acceptance**: All tests pass. Model trains successfully. Prediction produces probabilities spanning 0-1 range on real data.

**Files**: None (verification only)

---

# PR-3: `feat/frontend-and-advanced-sql` (25 tasks)

**Branch**: `feat/frontend-and-advanced-sql` (from `feat/scaling-and-features`)
**Estimated**: ~350 changed lines
**Dependencies**: PR-1 and PR-2 must be merged. DB populated. Model trained with scaler.

---

## Phase 1: Setup (tasks 3.1–3.2)

### Task 3.1 — Create feature branch from PR-2 merge base

**Work**:
1. After PR-2 is merged, pull main: `git checkout main && git pull`
2. Create branch: `git checkout -b feat/frontend-and-advanced-sql`
3. Verify PR-1 and PR-2 changes are present
4. Run existing tests: `pytest backend/tests/ -v`
5. Start frontend to verify current state loads correctly

**Acceptance**: All existing tests pass. Frontend loads all tabs without errors.

**Files**: None (env check only)

---

### Task 3.2 — Add CSS styles for new components

**Work**:
In `frontend/css/style.css`, append styles for:
- `.datasource-toggle` — flex container for toggle buttons
- `.toggle-btn` / `.toggle-btn.active` — styled toggle buttons (dark theme, blue active)
- `.demo-explanation` / `.explanation-card` / `.explanation-list` / `.explanation-item` — explanation panel styles
- `.contribution-bar.positive` / `.contribution-bar.negative` — color-coded contribution bars
- `.sql-counts-grid` — CSS grid for SQL row counts

**Acceptance**: No existing styles broken. New classes render correctly when elements exist.

**Files**: `frontend/css/style.css` (~+30 lines)

---

## Phase 2: Backend enhancements (tasks 3.3–3.6)

### Task 3.3 — Add `merchant` filter to SQL transactions endpoint

**Work**:
In `backend/app.py`, add `merchant: str | None = Query(None)` to `sql_transactions()`. When provided, append `m.merchant_name ILIKE :merchant` to WHERE clause with `%{merchant}%` pattern.

**Acceptance**: `GET /api/sql/transactions?merchant=kohler` returns only transactions with "kohler" in merchant name.

**Files**: `backend/app.py` (~+10 lines)

---

### Task 3.4 — Add `sql_counts` to `/api/pipeline/status`

**Work**:
In `backend/app.py`, in `pipeline_status()`:
1. Try importing `get_engine` and querying `COUNT(*)` for: transactions, customers, merchants, rejected_records.
2. Wrap in try/except — on failure, log warning and set `sql_counts = {"error": str(e)}`.
3. Add `"sql_counts": sql_counts` to response dict.

**Acceptance**: `GET /api/pipeline/status` returns `sql_counts` block with 4 numeric values. When DB unreachable, returns error string.

**Files**: `backend/app.py` (~+20 lines)

---

### Task 3.5 — Add prediction explanations to `/api/model/predict`

**Work**:
In `backend/src/model_predict.py`, add `_get_top_features()` helper:
1. Read `feature_importance` from model metadata.
2. For each feature, compute `contribution = importance × feature_value`.
3. Sort by absolute contribution, take top 3.
4. Return list of `{"feature": name, "contribution": abs_val, "direction": "+" or "-"}`.

In `backend/app.py`, in the `/api/model/predict` handler, call `_get_top_features()` and append to result as `"explanations"`. Wrap in try/except to gracefully omit on failure.

**Acceptance**: Prediction response includes `explanations` array with top-3 features and directions. When no feature importance available, `explanations` is omitted (not error).

**Files**: `backend/src/model_predict.py` (~+25 lines), `backend/app.py` (~+10 lines)

---

### Task 3.6 — Verify `/api/model/feature-importance` works with 13 features

**Work**:
After training with PR-2's expanded features, verify `GET /api/model/feature-importance` returns 13 entries. If the endpoint only reads from `evaluation_report.json`, verify the report has 13 features. No code changes expected — just verification.

**Acceptance**: Response contains 13 feature objects, not 9.

**Files**: `backend/app.py` (~+0 lines, verification only)

---

## Phase 3: Frontend — SQL toggle (tasks 3.7–3.11)

### Task 3.7 — Add HTML toggle UI to Dataset tab

**Work**:
In `frontend/index.html`, in the Dataset section (`<section id="dataset">`), add after the `<h2>`:

```html
<div class="datasource-toggle">
    <span class="toggle-label">Data Source:</span>
    <button id="toggle-parquet" class="toggle-btn active" onclick="switchDataSource('parquet')">📁 Parquet</button>
    <button id="toggle-sql" class="toggle-btn" onclick="switchDataSource('sql')">🗄️ SQL</button>
</div>
```

**Acceptance**: Toggle buttons render in Dataset tab. Clickable. No layout break.

**Files**: `frontend/index.html` (~+10 lines)

---

### Task 3.8 — Implement `switchDataSource()` and SQL path in `loadDataset()`

**Work**:
In `frontend/js/app.js`:
1. Add global: `let dataSource = localStorage.getItem("fraud-datasource") || "parquet";`
2. Add `switchDataSource(source)` function — updates `dataSource`, persists to `localStorage`, toggles active class on buttons, calls `loadDataset()`.
3. Modify `loadDataset()` to branch on `dataSource` — if `"sql"`, call new `loadDatasetFromSQL()`; else call existing logic (rename existing to `loadDatasetFromParquet()`).
4. Implement `loadDatasetFromSQL()` — fetches `/api/sql/stats`, `/api/sql/transactions?limit=10`, `/api/sql/kpis` in parallel.

**Acceptance**:
- Click "SQL" toggle → data reloads from SQL endpoints.
- Click "Parquet" → data reloads from Parquet endpoints.
- Refresh page → toggle state persists.

**Files**: `frontend/js/app.js` (~+50 lines)

---

### Task 3.9 — Implement `updateDatasetStatsFromSQL()` and SQL-data rendering

**Work**:
In `frontend/js/app.js`:
1. Add `updateDatasetStatsFromSQL(stats)` — maps SQL stats keys to DOM elements (e.g., `totals_count` → `ds-total-rows`, `amt_mean` → `ds-avg-amt`).
2. Reuse existing `updateSampleTable()` — SQL endpoint returns `transactions` array with same shape as parquet sample.
3. In SQL path, call `renderFraudChart({ legit: kpis.legit_count, fraud: kpis.fraud_count })` and `renderCategoryChart(stats.by_category)`.

**Acceptance**: When SQL is selected, all Dataset tab stats, sample table, fraud chart, and category chart render correctly.

**Files**: `frontend/js/app.js` (~+30 lines)

---

### Task 3.10 — Add visual indicator for current data source

**Work**:
In `loadDataset()` / `loadDatasetFromSQL()`, update a visual label or badge showing current source. Use existing `#ds-total-cols` or add a small badge element.

**Acceptance**: Dataset tab clearly shows whether data is sourced from Parquet or SQL.

**Files**: `frontend/js/app.js` (~+5 lines) + `frontend/index.html` (~+2 lines)

---

### Task 3.11 — Graceful degradation when SQL is unavailable

**Work**:
1. Add try/catch around SQL endpoint calls in `loadDatasetFromSQL()`.
2. On failure, show toast/alert: "SQL endpoints unavailable — switching to Parquet."
3. Auto-switch `dataSource` back to `"parquet"`.
4. In `switchDataSource()`, disable SQL button if SQL endpoints are known to be down (optional; keep simple).

**Acceptance**: Stop FastAPI, select SQL toggle → page shows error and falls back to Parquet view.

**Files**: `frontend/js/app.js` (~+10 lines)

---

## Phase 4: Frontend — Prediction explanations (tasks 3.12–3.16)

### Task 3.12 — Add explanation panel HTML to Demo tab

**Work**:
In `frontend/index.html`, in the Demo section, after the prediction result card, add:

```html
<div id="demo-explanation" class="demo-explanation" style="display:none;">
    <div class="explanation-card">
        <h4>🔍 Top Driving Features</h4>
        <ul id="explanation-list" class="explanation-list"></ul>
    </div>
</div>
```

**Acceptance**: Element exists in DOM (hidden by default). No layout break.

**Files**: `frontend/index.html` (~+8 lines)

---

### Task 3.13 — Render explanations in `runDemoPrediction()`

**Work**:
In `frontend/js/model.js`, in `runDemoPrediction()`, after receiving prediction result:
1. Check if `result.explanations` exists and has length > 0.
2. If yes: show `#demo-explanation`, populate `#explanation-list` with `<li>` items showing feature name, contribution bar, and direction badge.
3. Color-code: green bar for direction `+` (pushes toward fraud), red for `-` (pushes toward legit). Or swap based on context.
4. If no: hide `#demo-explanation`.

**Acceptance**: After a prediction, if server returns explanations, panel shows 3 features with colored bars.

**Files**: `frontend/js/model.js` (~+30 lines)

---

### Task 3.14 — Create contribution bar rendering helper

**Work**:
In `frontend/js/model.js`, add helper:
```javascript
function renderContributionBar(contribution, direction) {
    const width = Math.min(100, contribution * 100);
    const colorClass = direction === '+' ? 'positive' : 'negative';
    return `<div class="contribution-bar ${colorClass}" style="width:${width}px;"></div>`;
}
```

**Acceptance**: Bar renders with proportional width and correct color.

**Files**: `frontend/js/model.js` (~+8 lines)

---

### Task 3.15 — Graceful handling when explanations unavailable

**Work**:
1. Ensure `#demo-explanation` is hidden on new prediction if no explanations.
2. Ensure `#demo-explanation` is shown/hidden correctly for sequential predictions.
3. If the `explanations` key is absent or empty, don't break the existing UI.

**Acceptance**: Old model without explanations → demo still shows prediction, no explanation panel visible.

**Files**: `frontend/js/model.js` (~+5 lines)

---

### Task 3.16 — Update demo prediction request to include all needed features

**Work**:
Verify that `runDemoPrediction()` sends all fields needed for interaction features (`city_pop`, `distance_km`, etc. — already present). No code change expected, just verification.

**Acceptance**: Demo prediction works with PR-2 model. Explanations render correctly.

**Files**: `frontend/js/model.js` (~+0 lines, verification)

---

## Phase 5: Frontend — Pipeline SQL counts (tasks 3.17–3.19)

### Task 3.17 — Add SQL counts display area to Pipeline tab

**Work**:
In `frontend/index.html`, in the Pipeline section, after the 3 bronze/silver/gold cards, add:

```html
<div id="pipeline-sql-counts" class="sql-counts-section" style="display:none;">
    <h3>🗄️ SQL Database Tables</h3>
    <div class="sql-counts-grid">
        <div class="stat"><strong>Transactions:</strong> <span id="sql-tx-count">—</span></div>
        <div class="stat"><strong>Customers:</strong> <span id="sql-cust-count">—</span></div>
        <div class="stat"><strong>Merchants:</strong> <span id="sql-merch-count">—</span></div>
        <div class="stat"><strong>Rejected:</strong> <span id="sql-rej-count">—</span></div>
    </div>
</div>
```

**Acceptance**: New section renders in Pipeline tab. Initially hidden or shows "—" placeholders.

**Files**: `frontend/index.html` (~+15 lines)

---

### Task 3.18 — Render SQL counts in `loadPipelineStatus()`

**Work**:
In `frontend/js/pipeline.js`, modify `loadPipelineStatus()`:
1. After `updateLayerCard(...)` calls, add `updateSQLCounts(status.sql_counts)`.
2. Implement `updateSQLCounts(counts)`:
   - If `!counts || counts.error` → hide `#pipeline-sql-counts`.
   - Else → show `#pipeline-sql-counts`, update span texts with `.toLocaleString()` formatted numbers.

**Acceptance**: After running pipeline, Pipeline tab shows SQL table row counts. Values match `SELECT COUNT(*)` in psql.

**Files**: `frontend/js/pipeline.js` (~+18 lines)

---

### Task 3.19 — Add `updateSQLCounts` helper function

**Work**:
```javascript
function updateSQLCounts(counts) {
    const el = document.getElementById('pipeline-sql-counts');
    if (!el || !counts || counts.error) {
        if (el) el.style.display = 'none';
        return;
    }
    el.style.display = 'block';
    setText('sql-tx-count', counts.transactions?.toLocaleString() || '—');
    setText('sql-cust-count', counts.customers?.toLocaleString() || '—');
    setText('sql-merch-count', counts.merchants?.toLocaleString() || '—');
    setText('sql-rej-count', counts.rejected_records?.toLocaleString() || '—');
}
```

**Acceptance**: Counts display correctly. Graceful when DB is down.

**Files**: `frontend/js/pipeline.js` (~+12 lines)

---

## Phase 6: Integration & manual verification (tasks 3.20–3.24)

### Task 3.20 — Verify toggle persistence across page loads

**Work**:
1. Select "SQL" toggle in Dataset tab.
2. Navigate to another tab.
3. Navigate back to Dataset tab — verify "SQL" is still selected.
4. Refresh page — verify "SQL" is still selected.
5. Clear localStorage → verify defaults to "Parquet".

**Acceptance**: Toggle state persists in localStorage key `fraud-datasource`.

**Files**: None (verification)

---

### Task 3.21 — Verify all 9 tabs still work

**Work**:
Manual click-through of all tabs: Overview, Dataset, Architecture, Pipeline, PMBOK, Security, CI/CD, Model, Demo. Verify:
- No JavaScript errors in console.
- All charts render.
- No layout regressions.

**Acceptance**: Zero console errors. All tabs render correctly.

**Files**: None (verification)

---

### Task 3.22 — Cross-browser sanity check

**Work**:
Test frontend in:
1. Chrome (latest)
2. Firefox (latest)
3. Edge (latest)

Verify toggle, charts, explanations, and SQL counts render.

**Acceptance**: No rendering issues across browsers.

**Files**: None (verification)

---

### Task 3.23 — Test SQL `merchant` filter end-to-end

**Work**:
1. Start FastAPI with populated DB.
2. Open frontend, switch to SQL view.
3. Verify transactions list shows data.
4. Verify chart data matches SQL stats.

**Acceptance**: Frontend SQL view functions correctly end-to-end.

**Files**: None (verification)

---

## Phase 7: Commit plan & cleanup (tasks 3.24–3.25)

### Task 3.24 — Commit work in logical order

**Conventional commits**:
1. `style: add CSS for toggle, explanation panel, and SQL counts` — `frontend/css/style.css`
2. `feat(api): add merchant filter to SQL transactions endpoint` — `backend/app.py`
3. `feat(api): add SQL row counts to pipeline status` — `backend/app.py`
4. `feat(api): return top-3 feature contributions in prediction response` — `backend/src/model_predict.py`, `backend/app.py`
5. `feat(frontend): add SQL/Parquet data source toggle to Dataset tab` — `frontend/index.html`, `frontend/js/app.js`
6. `feat(frontend): render prediction explanation panel in Demo tab` — `frontend/index.html`, `frontend/js/model.js`
7. `feat(frontend): display SQL table counts in Pipeline tab` — `frontend/index.html`, `frontend/js/pipeline.js`

**Acceptance**: 7 commits on `feat/frontend-and-advanced-sql`.

**Files**: Git metadata only

---

### Task 3.25 — Final cleanup and verification for PR-3

**Work**:
1. Run full backend test suite: `pytest backend/tests/ -v`
2. Lint all JS files: no `console.log` leftovers, no syntax errors
3. Start full stack: `uvicorn backend.app:app` + serve frontend
4. Manual walkthrough of all acceptance criteria (AC-3.1 through AC-3.9)
5. Check DevTools console — zero errors

**Acceptance**: All PR-3 acceptance criteria pass. All tests green. No console errors.

**Files**: None (verification only)

---

## Cross-PR Verification Checklist

Before marking the overall change as complete, verify:

- [ ] PR-1 merged → DB populated with 555K+ rows via `seed.py` or manual ingestion
- [ ] PR-1 merged → All 4 SQL endpoints return data
- [ ] PR-1 merged → `pipeline_load_state` has correct timestamp
- [ ] PR-2 merged → Model trained on full dataset
- [ ] PR-2 merged → `scaler.joblib` created
- [ ] PR-2 merged → Probabilities span 0.0–1.0 range
- [ ] PR-2 merged → Feature importance shows 13 features
- [ ] PR-3 merged → SQL toggle works and persists
- [ ] PR-3 merged → Explanation panel renders top-3 features
- [ ] PR-3 merged → Pipeline tab shows SQL row counts
- [ ] PR-3 merged → All 9 tabs functional, zero console errors
- [ ] All existing parquet endpoints still work (no regression)
- [ ] All test suites pass (`pytest backend/tests/ -v`)

---

## Rollback Procedure (per PR)

| PR | Rollback steps | Impact |
|----|---------------|--------|
| PR-3 | Revert `feat/frontend-and-advanced-sql` merge. Frontend defaults to parquet view (existing behavior). | Minimal — SQL endpoints remain in backend. |
| PR-2 | Revert `feat/scaling-and-features` merge. Old `best_model.joblib` still works (backward-compat). Delete `scaler.joblib`. | Model uses old features but still predicts. |
| PR-1 | Revert `fix/seed-and-sql-reads` merge. SQL endpoints gone. `pipeline_load_state` table remains (harmless). | DB stays populated but unused. |
