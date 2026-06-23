# SDD Proposal: Fix Data Pipeline & Add SQL Endpoints

## Metadata
- **Change ID**: `fix-data-pipeline-sql-endpoints`
- **Project**: Gestion_de_datos_para_ia (Fraud Detection DataOps Pipeline)
- **Author**: SDD Proposal Executor
- **Date**: 2026-05-24
- **Status**: `proposed`
- **Review Budget**: 400 changed lines per PR
- **Chained PR Strategy**: auto-forecast

## 1. Intent & Problem Statement

### Current Issues
1. **Synthetic data with random labels**: `seed.py` generates 500 rows with `is_fraud` completely uncorrelated from features. When this was the primary data source, model probabilities never exceeded ~60%.
2. **No SQL reads**: `loader.py` writes Gold data to PostgreSQL, but **zero** API endpoints read from it. All reads go to parquet files (Bronze/Silver/Gold).
3. **Weak feature engineering**: Only 7 numeric + 2 categorical features. No scaling (`city_pop` up to 9M vs `trans_hour` 0-23). No interaction features.
4. **Tiny default sample**: `seed.py` hardcodes `SAMPLE_SIZE = 500`. `DATA_SAMPLE_SIZE` env var defaults to 10,000 but seed ignores it. The real dataset has **555,719 rows**.
5. **Feature scaling mismatch**: `build_features()` returns raw numpy arrays with no StandardScaler. Linear models (LogisticRegression) are severely hurt by unscaled features.

### Goals
- Ensure model probabilities are meaningful by using the real dataset + better features.
- Add API endpoints that READ from PostgreSQL (not just parquet).
- Keep changes reviewable within the 400-line budget per PR.

---

## 2. Scope Boundaries

### In Scope
- `backend/seed.py`: Stop generating synthetic data when real CSV exists; respect `DATA_SAMPLE_SIZE`.
- `backend/src/features.py`: Add feature scaling (StandardScaler) and optional interaction features.
- `backend/src/model_predict.py`: Apply scaler at inference time.
- `backend/app.py`: Add SQL-read endpoints (`/api/sql/*`) and optionally migrate some existing read endpoints to prefer SQL when available.
- `backend/src/loader.py`: Minor helper for SQL queries (shared engine/connection helper if missing).
- Tests: Add/update tests for new SQL endpoints and feature scaling.

### Out of Scope
- Rewriting the entire frontend (minor JS updates to call new endpoints are acceptable in Option C only).
- Changing the database schema (existing tables are sufficient for reads).
- Real-time streaming or message queues.
- Model hyperparameter tuning beyond basic scaling fixes.

---

## 3. Options

### Option A — Minimal Fix (SQL Reads + Real Data Guarantee)
**Scope**: 
- Remove synthetic data fallback from `seed.py` when real CSV exists. Make `seed.py` respect `DATA_SAMPLE_SIZE` env var.
- Add 3 SQL-read endpoints in `app.py`:
  - `GET /api/sql/transactions` — paginated transactions from PostgreSQL
  - `GET /api/sql/kpis` — KPIs computed via SQL aggregates
  - `GET /api/sql/fraud-distribution` — fraud distribution from SQL
- Add a thin `backend/src/db.py` helper with `get_engine()` / `get_connection()` for shared SQLAlchemy session management.

**Files to Change**:
- `backend/seed.py` (~40 lines)
- `backend/app.py` (~80 lines)
- `backend/src/db.py` (~30 lines new)
- `backend/tests/test_api.py` or new `test_sql_endpoints.py` (~40 lines)

**Estimated Diff**: ~190 lines  
**Risk Level**: Low  
**Time Estimate**: 2–3 hours  
**Exceeds 400 lines?**: No (single PR)

**Pros**: Quick win, low risk, immediately gives SQL read capability, stops random-label data poisoning.  
**Cons**: Does not fix feature engineering or scaling; model probabilities may still be suboptimal.

---

### Option B — Balanced Fix (SQL Reads + Feature Scaling + Interaction Features)
**Scope**:
- Everything in Option A.
- `backend/src/features.py`:
  - Integrate `StandardScaler` (fit on train, transform train/test, persist scaler to `models/scaler.joblib`).
  - Add 2–3 interaction features: `amt_per_hour`, `distance_x_amt`, `city_pop_log`.
- `backend/src/model_predict.py`:
  - Load and apply `scaler.joblib` before prediction.
  - Ensure encoded features align with scaled numeric features.
- `backend/src/model_train.py`:
  - Save scaler alongside model metadata.
- `backend/seed.py`:
  - Remove `SAMPLE_SIZE = 500` constant; use `DATA_SAMPLE_SIZE` from settings (default 50,000+ for meaningful training).
- Add SQL endpoint:
  - `GET /api/sql/model-predictions` — query persisted predictions (if any exist via loader pipeline).

**Files to Change**:
- `backend/seed.py` (~20 lines)
- `backend/src/features.py` (~80 lines)
- `backend/src/model_train.py` (~30 lines)
- `backend/src/model_predict.py` (~40 lines)
- `backend/app.py` (~80 lines)
- `backend/src/db.py` (~30 lines new)
- Tests (~60 lines)

**Estimated Diff**: ~340 lines  
**Risk Level**: Medium  
**Time Estimate**: 1–2 days  
**Exceeds 400 lines?**: No (single PR, but near limit; some test files may push it slightly over)

**Pros**: Fixes the root cause of poor model probabilities (real data + scaling + richer features). SQL endpoints are actually useful.  
**Cons**: Slightly more regression risk in model training/inference path; needs careful testing of scaler round-trip.

---

### Option C — Comprehensive Overhaul (Full DataOps + Advanced Features + Frontend)
**Scope**:
- Everything in Option B.
- `backend/src/features.py`:
  - Additional advanced features: merchant-level fraud rate, customer transaction velocity, time-based aggregations (requires window functions or pre-aggregation).
- `backend/app.py`:
  - Full SQL-read endpoint suite with filtering, sorting, pagination (`/api/sql/transactions?merchant=X&fraud=1&page=2`).
  - `POST /api/sql/predict-and-store` — predict and write result to `model_predictions` table in real time.
- `backend/src/loader.py`:
  - Add incremental load mode (only load new `trans_num` records) to support large datasets.
- Frontend (`frontend/js/`):
  - Update Dataset tab to toggle between "Parquet View" and "SQL View".
  - Update Model tab to show prediction history from SQL.
- Add connection pooling / async SQLAlchemy support for better API performance.

**Files to Change**:
- `backend/seed.py` (~20 lines)
- `backend/src/features.py` (~120 lines)
- `backend/src/model_train.py` (~30 lines)
- `backend/src/model_predict.py` (~60 lines)
- `backend/src/loader.py` (~60 lines)
- `backend/app.py` (~180 lines)
- `backend/src/db.py` (~50 lines new)
- `frontend/js/app.js` or similar (~100+ lines)
- Tests (~100 lines)

**Estimated Diff**: ~720 lines  
**Risk Level**: Medium-High  
**Time Estimate**: 3–5 days  
**Exceeds 400 lines?**: Yes — requires **chained PRs** (minimum 2, likely 3)

**Pros**: Most complete solution; frontend actually uses SQL; pipeline is production-grade with incremental loads.  
**Cons**: Highest regression risk; longest delivery time; frontend changes add cross-stack complexity.

---

## 4. Recommendation

**Choose Option B**.

Rationale:
- Option A leaves the model broken (weak features, no scaling). SQL reads are nice but the primary user complaint was "model probabilities don't make sense."
- Option C is too large for the current pain point. The user asked to "fix data treatment so model probabilities make sense" and "add API endpoints that read from PostgreSQL." Option C adds scope (frontend, incremental loads, advanced aggregations) that is not required to solve the stated problem.
- Option B directly addresses both stated goals within a single reviewable PR (or at most a very small chained sequence). The scaler + interaction features are the minimal set needed to make probabilities meaningful on the real 555K-row dataset.

---

## 5. Rollback Plan

1. **Code rollback**: All changes are additive or parametric. Reverting the PR restores the previous parquet-only behavior and old feature pipeline.
2. **Data rollback**: The scaler artifact (`models/scaler.joblib`) and retrained model are new files; old `best_model.joblib` remains untouched if renamed during training. Proposal: train outputs a versioned filename (`best_model_v2.joblib`) or backup old model before saving.
3. **Database rollback**: SQL read endpoints are read-only by design (no schema changes, no writes). Zero database migration risk.
4. **Feature flag**: If the new scaled feature path has issues, a one-line env var `USE_SCALED_FEATURES=false` can route back to the old `build_features()` path (optional safety hatch, can be added in PR).

---

## 6. Impact on Existing Data Layers

| Layer | Impact | Details |
|-------|--------|---------|
| **Bronze** | None | Ingestion logic unchanged. Real CSV already in place. |
| **Silver** | None | Cleaning logic unchanged. |
| **Gold** | None | Validation logic unchanged. |
| **PostgreSQL** | Read-only additions | New endpoints read from existing tables. No schema changes. |
| **Parquet files** | None | Existing parquet pipeline remains the primary write path. Loader still writes to SQL. |
| **Models** | New artifacts | Scaler saved alongside model. Old model can be retained as fallback. |

---

## 7. Success Criteria

- [ ] `/api/sql/transactions` returns data from PostgreSQL `transactions` table.
- [ ] `/api/sql/kpis` returns aggregate KPIs computed via SQL (not parquet).
- [ ] After running the full pipeline on the real 555K-row dataset, model training produces probabilities that span close to 0.0–1.0 (not capped at ~0.6).
- [ ] `seed.py` no longer overwrites or interferes with the real CSV.
- [ ] All existing tests pass; new tests cover SQL endpoints and scaler persistence.
- [ ] PR diff ≤ 400 lines (Option B target).

---

## 8. Chained PR Forecast (if Option C is chosen later)

If Option C is adopted, recommend this chain:

1. **PR-1**: `fix/seed-and-sql-reads` (~200 lines)
   - `seed.py` fix + `db.py` + basic SQL endpoints (Option A scope).
2. **PR-2**: `feat/scaling-and-features` (~250 lines)
   - `features.py` scaler + interactions + `model_train.py`/`model_predict.py` updates.
3. **PR-3**: `feat/advanced-sql-and-frontend` (~270 lines)
   - Advanced SQL endpoints, incremental loader, frontend SQL view toggle.

---

## 9. Notes

- The synthetic data generation in `seed.py` (`generate_synthetic_data`) produces `is_fraud` via `random.choices([0, 1], weights=[95, 5])` with **no correlation** to `amt`, `category`, `distance_km`, etc. This was the dominant cause of the ~60% probability ceiling when synthetic data was being used.
- The real dataset (`Data/bronze/02_fraudTest.csv`, 555,719 rows) has meaningful fraud labels correlated with amount, merchant, and time features. Simply switching to this dataset + adding scaling should unlock proper probability ranges.
- No new Python dependencies are required for Option B (StandardScaler is in `scikit-learn`, already imported via `sklearn`).
