# Specification: Comprehensive Data Pipeline Overhaul (Option C)

**Change ID**: `fix-data-pipeline-sql-endpoints`  
**Date**: 2026-05-24  
**Status**: `approved`  
**Scope**: Full DataOps overhaul covering ingestion, feature engineering, model training/inference, SQL read endpoints, loader incremental logic, and frontend SQL integration.

---

## 0. Dependencies & PR Strategy

This specification is designed for **3 chained PRs** to respect the 400-line review budget per PR:

1. **PR-1 — `fix/seed-and-sql-reads`**  
   Ingestion fix, loader upserts/incremental mode, SQL read endpoints (`/api/sql/*`), connection pooling.
2. **PR-2 — `feat/scaling-and-features`**  
   StandardScaler, interaction features, risk encoding, model retraining with scaled features, threshold tuning, scaler persistence.
3. **PR-3 — `feat/frontend-and-advanced-sql`**  
   Frontend SQL view toggle, prediction explanations, pipeline status SQL row counts, advanced filtering.

**Cross-PR Dependencies**
- PR-2 depends on PR-1 (SQL endpoints must exist for frontend integration, but PR-2 itself only needs the database to be populated).
- PR-3 depends on PR-1 and PR-2.
- Feature Engineering MUST be merged before Model Improvements in PR-2.
- Loader Improvements MUST be merged before SQL Read Endpoints in PR-1.

---

## 1. Data Ingestion Fix

### Purpose
Eliminate synthetic-data poisoning and ensure the pipeline consumes the real 555,719-row dataset by default.

### Requirements

#### Requirement: Synthetic data generation MUST be gated

The system MUST NOT generate synthetic data when the real raw CSV is present. Synthetic generation MAY occur only when all of the following are true:
- the real CSV is absent, **and**
- the environment variable `ALLOW_SYNTHETIC_SEED` is set to `"true"`.

When synthetic data is generated, the row count MUST respect `DATA_SAMPLE_SIZE` (default 10,000) instead of a hardcoded constant.

##### Scenario: Real CSV already exists
- GIVEN `Data/bronze/02_fraudTest.csv` exists and contains valid rows
- WHEN `seed.py` runs
- THEN no synthetic rows are written
- AND the existing CSV is left untouched

##### Scenario: Real CSV missing and synthetic allowed
- GIVEN `Data/bronze/02_fraudTest.csv` does not exist
- AND `ALLOW_SYNTHETIC_SEED=true`
- WHEN `seed.py` runs
- THEN it generates exactly `DATA_SAMPLE_SIZE` rows (or the full requested count)
- AND writes them to `Data/bronze/02_fraudTest.csv`

##### Scenario: Real CSV missing and synthetic disallowed
- GIVEN `Data/bronze/02_fraudTest.csv` does not exist
- AND `ALLOW_SYNTHETIC_SEED` is unset or not `"true"`
- WHEN `seed.py` runs
- THEN it logs an error and exits without writing a CSV
- AND the pipeline aborts rather than training on random labels

#### Requirement: Ingestion path MUST be robust

The ingestion module MUST resolve the source CSV path relative to `BRONZE_DIR`. If the file is missing, it MUST return an explicit error status rather than falling back to a different file or silent empty DataFrame.

##### Scenario: Missing source file
- GIVEN the configured `RAW_CSV` path does not exist
- WHEN the ingestion stage is invoked
- THEN it returns `{"status": "error", "error": "Raw CSV not found at <path>"}`

### Acceptance Criteria
- [ ] `seed.py` never overwrites an existing real CSV.
- [ ] `seed.py` uses `DATA_SAMPLE_SIZE` for synthetic row count.
- [ ] Ingestion returns a clear error when the source CSV is absent.
- [ ] Existing end-to-end tests that rely on synthetic data still pass when `ALLOW_SYNTHETIC_SEED=true`.

---

## 2. Feature Engineering

### Purpose
Transform raw Gold-layer data into a model-ready feature matrix that corrects scale mismatches, captures interactions, and encodes risk signals.

### Requirements

#### Requirement: Numeric features MUST be scaled

The system MUST fit a scaler exclusively on the training split and apply the fitted scaler to both training and test splits. The scaler artifact MUST be persisted to disk so that inference can apply identical scaling.

##### Scenario: Training split scaling
- GIVEN a chronological train/test split exists
- WHEN `build_features()` executes
- THEN the scaler is fit on `train_df[NUMERIC_FEATURES]` only
- AND `train_df` and `test_df` numeric columns are transformed with that scaler
- AND the scaler is saved to `models/scaler.joblib`

##### Scenario: Inference scaling consistency
- GIVEN a trained model and a persisted scaler
- WHEN a single transaction is prepared for prediction
- THEN the same scaling parameters (mean, std) from training are applied
- AND the prediction pipeline raises an error if the scaler artifact is missing

#### Requirement: Interaction and risk features MUST be added

The feature engineering pipeline MUST produce the following additional columns:

| Feature | Definition |
|---------|------------|
| `amt_per_city_pop` | `amt / max(city_pop, 1)` |
| `distance_x_amt` | `distance_km * amt` |
| `hour_is_night` | `1` if `trans_hour` in `{0,1,2,3,4,5,22,23}`, else `0` |
| `category_fraud_rate` | Fraud rate of the category computed **on the training split only** |

These new features MUST appear in `feature_names` returned by `build_features()` and MUST be scaled alongside the original numeric features.

##### Scenario: Interaction features present
- GIVEN the Gold dataset is loaded
- WHEN `build_features()` is called
- THEN the returned `feature_names` list includes `amt_per_city_pop`, `distance_x_amt`, `hour_is_night`, and `category_fraud_rate`
- AND `X_train` / `X_test` contain values for those columns

##### Scenario: Category fraud rate does not leak
- GIVEN a chronological train/test split
- WHEN `category_fraud_rate` is computed
- THEN the fraud rate for each category uses only rows in `train_df`
- AND `test_df` receives the same rate mapping derived from `train_df`
- AND unknown categories in test receive the global training fraud rate

#### Requirement: Customer velocity features SHOULD be supported

The system SHOULD compute customer-level time-windowed aggregations before the chronological split, ensuring that any window extending into the future of a given row is truncated at that row’s timestamp to prevent leakage.

| Feature | Definition |
|---------|------------|
| `txn_count_24h` | Number of transactions by the same customer in the preceding 24 hours |
| `amt_mean_7d` | Mean transaction amount by the same customer in the preceding 7 days |

If these features are omitted in the initial implementation, the system MUST document the omission and reserve the column names for a future update.

##### Scenario: Velocity features without leakage
- GIVEN transactions are sorted by `trans_date_trans_time`
- WHEN `txn_count_24h` is computed
- THEN for each row the count includes only earlier rows for the same `cc_num_masked` within 24 hours
- AND no later rows are included

#### Requirement: Feature importance tracking MUST include new features

Model training MUST persist feature importance (or model-specific coefficients) for every column in `feature_names`, including the scaled and interaction features.

##### Scenario: Importance completeness
- GIVEN a model has been trained on the expanded feature set
- WHEN `/api/model/feature-importance` is queried
- THEN the returned list contains entries for all scaled numeric and interaction features

### Acceptance Criteria
- [ ] `build_features()` returns 11+ features (7 original numeric + 2 encoded + at least 2 new interactions).
- [ ] Scaler artifact is created and loaded successfully during inference.
- [ ] Category fraud rate is computed from training data only.
- [ ] Model metadata records feature names in the exact order used for training.
- [ ] Unit tests verify that scaled numeric columns have mean ≈ 0 and std ≈ 1 on the training set.

---

## 3. SQL Read Endpoints

### Purpose
Expose PostgreSQL as a first-class read source, enabling the frontend and external consumers to query live transactional data without reading parquet files.

### Requirements

#### Requirement: Core SQL read endpoints MUST be implemented

The API MUST expose the following endpoints, all backed by PostgreSQL queries (not parquet):

- `GET /api/sql/transactions`
- `GET /api/sql/transactions/{trans_num}`
- `GET /api/sql/stats`
- `GET /api/sql/kpis`

##### Scenario: List transactions with pagination
- GIVEN 10,000 rows exist in the `transactions` table
- WHEN `GET /api/sql/transactions?limit=20&offset=40` is called
- THEN the response contains exactly 20 transaction objects
- AND `meta.offset` equals 40
- AND `meta.total` equals 10,000

##### Scenario: Filter by date range and fraud status
- GIVEN rows span multiple dates and `is_fraud` values
- WHEN `GET /api/sql/transactions?start_date=2020-01-01&end_date=2020-01-31&fraud=1` is called
- THEN only transactions within the date range with `is_fraud = 1` are returned

##### Scenario: Single transaction lookup
- GIVEN a transaction with `trans_num = "abc-123"` exists in SQL
- WHEN `GET /api/sql/transactions/abc-123` is called
- THEN the response contains that exact transaction
- AND a 404 is returned if the transaction does not exist

##### Scenario: SQL stats aggregation
- GIVEN the `transactions` table is populated
- WHEN `GET /api/sql/stats` is called
- THEN the response includes aggregated metrics such as total count, average `amt`, max `amt`, and record counts grouped by `category`

##### Scenario: SQL KPIs mirror parquet KPIs
- GIVEN the `transactions` table is populated
- WHEN `GET /api/sql/kpis` is called
- THEN the response includes `fraud_count`, `legit_count`, `fraud_pct`, `amt_mean`, `total_records`, and `completeness_pct`
- AND the values are computed entirely with SQL aggregates

#### Requirement: Pagination MUST be supported

List endpoints MUST accept `limit` (default 50, max 500) and `offset` (default 0) query parameters and return a `meta` block with `total`, `limit`, `offset`, and `next_offset` (if applicable).

#### Requirement: Connection pooling MUST be configured

The backend MUST use a shared SQLAlchemy `engine` with connection pooling. The pool size SHOULD be at least 5 connections with a max overflow of at least 10, or an equivalent async pool configuration if the endpoints are migrated to `async` SQLAlchemy.

##### Scenario: Concurrent API requests
- GIVEN 20 simultaneous requests to `/api/sql/transactions`
- THEN all requests complete without `ConnectionRefusedError` or pool exhaustion errors

### Acceptance Criteria
- [ ] All four `/api/sql/*` endpoints return data sourced from PostgreSQL.
- [ ] Pagination metadata is present and accurate.
- [ ] Filtering query parameters are documented and functional.
- [ ] Load testing with 20 concurrent requests does not exhaust the connection pool.
- [ ] Existing parquet-based endpoints remain untouched (no regression).

---

## 4. Loader Improvements

### Purpose
Make the PostgreSQL load stage production-grade by supporting incremental loads, true upserts, and load-state tracking.

### Requirements

#### Requirement: Incremental load mode MUST be supported

`loader.py` MUST support an optional `incremental` parameter. When `incremental=True`, the loader MUST query the maximum `unix_time` (or `trans_date_trans_time`) already present in the `transactions` table and load only Gold rows with a timestamp strictly greater than that value.

The last successfully loaded timestamp MUST be persisted in a new `pipeline_load_state` table (or appended to `pipeline_logs`) so that subsequent runs resume correctly.

##### Scenario: First incremental load
- GIVEN the `transactions` table is empty
- WHEN `load(incremental=True)` runs
- THEN all rows from `fraud_gold.parquet` are inserted
- AND the maximum timestamp is recorded in `pipeline_load_state`

##### Scenario: Second incremental load
- GIVEN the previous incremental load recorded `max_ts = 1_600_000_000`
- AND new Gold rows with timestamps greater than `1_600_000_000` exist
- WHEN `load(incremental=True)` runs again
- THEN only the new rows are inserted
- AND previously loaded rows are not duplicated

#### Requirement: Customers and merchants MUST be upserted

Instead of `ON CONFLICT DO NOTHING`, the loader MUST update mutable fields on conflict:

- **Customers**: `gender`, `city`, `state`, `zip`, `city_pop`, `job`, `age_at_transaction`
- **Merchants**: no mutable fields beyond `merchant_name` and `category`; the upsert MUST remain idempotent.

Transactions MAY continue to use `ON CONFLICT (trans_num) DO NOTHING` because `trans_num` is immutable.

##### Scenario: Customer record updated
- GIVEN a customer `C1` already exists with `city_pop = 1000`
- AND a new Gold row for `C1` has `city_pop = 5000`
- WHEN the loader runs
- THEN the `customers` table reflects `city_pop = 5000`

#### Requirement: Load state MUST be observable

The loader MUST return the recorded `last_loaded_timestamp` and `rows_inserted` in its result dictionary.

### Acceptance Criteria
- [ ] Running `load(incremental=True)` twice on the same data produces zero new transaction rows.
- [ ] Customer upsert updates changed fields.
- [ ] `pipeline_load_state` (or equivalent) contains the latest loaded timestamp after each run.
- [ ] Loader result includes `last_loaded_timestamp` and `rows_inserted`.

---

## 5. Frontend Integration

### Purpose
Allow the dashboard to consume the new SQL endpoints and surface richer model insights.

### Requirements

#### Requirement: Dataset tab MUST support SQL view toggle

The Dataset tab MUST provide a user-facing toggle (e.g., “Parquet View” / “SQL View”). When “SQL View” is selected, the tab MUST fetch data from `/api/sql/transactions`, `/api/sql/stats`, and `/api/sql/kpis` instead of the parquet endpoints.

##### Scenario: Toggle to SQL view
- GIVEN the user is on the Dataset tab
- WHEN the user selects “SQL View”
- THEN the sample table loads from `/api/sql/transactions?limit=10`
- AND the KPI cards load from `/api/sql/kpis`
- AND the fraud/category charts load from `/api/sql/stats`

#### Requirement: Model demo SHOULD show prediction explanations

The single-transaction prediction demo SHOULD display the top 3 features that most influenced the prediction, derived from the persisted `feature_importance` metadata. If feature importance is unavailable, the UI MUST gracefully omit the explanation panel.

##### Scenario: Prediction with explanation
- GIVEN a model is trained and feature importance is saved
- WHEN the user submits a transaction in the Demo tab
- THEN the result card shows the predicted label and probability
- AND a small list shows the top 3 driving features (e.g., “amt_per_city_pop”, “distance_x_amt”, “category_fraud_rate”)

#### Requirement: Pipeline status MUST include SQL row counts

The Pipeline Status tab (or the endpoint it consumes) MUST display row counts for the PostgreSQL tables: `transactions`, `customers`, `merchants`, and `rejected_records`. These counts MAY be fetched from a new endpoint or appended to the existing `/api/pipeline/status` response.

##### Scenario: SQL counts visible
- GIVEN the database contains 50,000 transactions, 2,000 customers, and 500 merchants
- WHEN the Pipeline tab loads
- THEN the status panel shows “SQL: 50,000 transactions | 2,000 customers | 500 merchants”

### Acceptance Criteria
- [ ] Dataset tab toggle switches data source without a page reload.
- [ ] SQL view shows the same conceptual information (KPIs, sample rows, distributions) as the parquet view.
- [ ] Model demo surfaces top-3 influential features when metadata is available.
- [ ] Pipeline status displays SQL table counts alongside parquet layer counts.

---

## 6. Model Improvements

### Purpose
Ensure model training and inference leverage the real dataset, scaled features, and an optimized decision threshold so that predicted probabilities span the full 0–1 range.

### Requirements

#### Requirement: Model training MUST use scaled features

The training pipeline MUST invoke the scaled feature path from PR-2. It MUST persist the scaler artifact and record its path in model metadata.

##### Scenario: Scaler saved alongside model
- GIVEN `build_features()` returns scaled matrices and a scaler object
- WHEN `train_models()` is executed
- THEN `models/scaler.joblib` is created (or updated)
- AND `model_metadata.json` contains `scaler_path: "models/scaler.joblib"`

#### Requirement: Decision threshold SHOULD be tuned

The system SHOULD compute the precision-recall curve on the validation/test set and select a decision threshold that maximizes F1-Score or meets a minimum precision constraint (configurable via `MODEL_DECISION_THRESHOLD`). The chosen threshold MUST be saved in metadata and used during inference.

##### Scenario: Threshold tuning improves F1
- GIVEN the test set probabilities span 0.0–1.0
- WHEN threshold tuning runs
- THEN the selected threshold is different from the default 0.3 if a better F1 exists
- AND the threshold is stored in `model_metadata.json`

##### Scenario: Inference uses tuned threshold
- GIVEN a trained model with a tuned threshold of 0.42
- WHEN `predict_single()` is called
- THEN the prediction uses 0.42 as the cutoff
- AND the metadata-driven threshold overrides the env var default

#### Requirement: Inference MUST apply the saved scaler

`model_predict.py` MUST load the scaler referenced by `scaler_path` in metadata and apply it to incoming raw numeric features before passing the vector to the model. If the scaler is missing, prediction MUST raise a clear error.

##### Scenario: Batch prediction with scaler
- GIVEN a batch prediction request with raw feature values
- WHEN `predict_batch()` executes
- THEN each row’s numeric features are transformed by the persisted scaler
- AND the model receives the scaled matrix

#### Requirement: Probabilities MUST span the full range on real data

After training on the real 555K-row dataset with scaled and interaction features, the model’s predicted probabilities on the test set MUST reach values below 0.1 and above 0.9, demonstrating that the model is no longer capped near 0.6.

### Acceptance Criteria
- [ ] `model_metadata.json` includes `scaler_path` and `decision_threshold`.
- [ ] `predict_single()` and `predict_batch()` load and apply the scaler.
- [ ] Missing scaler causes a 500/422 error with a descriptive message.
- [ ] Post-training evaluation shows probability min < 0.1 and max > 0.9 on the test set.
- [ ] All existing model endpoints remain backward-compatible with respect to request/response schema.

---

## 7. General Constraints & Non-Functional Requirements

### Requirements

#### Requirement: Backward compatibility MUST be preserved

Existing parquet-based endpoints (`/api/dataset/*`, `/api/kpis`, `/api/model/*`) MUST continue to function exactly as before unless explicitly superseded by a new SQL endpoint. Breaking changes to request/response schemas are NOT permitted.

#### Requirement: Tests MUST cover new behavior

Each PR MUST include tests that verify:
- SQL endpoint responses contain expected keys and pagination.
- Scaler round-trip (fit → save → load → transform) produces identical results.
- Incremental loader skips already-present rows.
- Frontend toggle renders SQL data correctly (integration or mocked unit test).

#### Requirement: Performance SHOULD meet baseline

SQL list queries with `limit=50` SHOULD return in < 300 ms on a local PostgreSQL instance with 500K rows. Model prediction (single) SHOULD remain < 100 ms excluding I/O.

---

## 8. Summary of Key Specs & Acceptance Criteria

| Domain | Key Spec | Acceptance Criteria |
|--------|----------|-------------------|
| **Ingestion** | Gate synthetic data; respect `DATA_SAMPLE_SIZE` | No overwrite of real CSV; env var controls synthetic fallback |
| **Features** | StandardScaler + 4+ new interaction/risk features | Scaler artifact exists; new columns in `feature_names`; no leakage |
| **SQL Reads** | 4 endpoints with filtering, pagination, pooling | Data from PostgreSQL; accurate `meta` block; pool survives 20 concurrent reqs |
| **Loader** | Incremental loads; upserts; load-state tracking | Second incremental run inserts 0 dupes; customer fields update on conflict |
| **Frontend** | SQL view toggle; prediction explanations; SQL counts | Toggle works; top-3 features shown; SQL row counts visible |
| **Model** | Scaled training; tuned threshold; scaler at inference | Metadata has `scaler_path` & tuned threshold; probabilities span 0–1 |
