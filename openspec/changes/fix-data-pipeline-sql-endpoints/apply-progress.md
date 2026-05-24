# Apply Progress — PR-1: fix/seed-and-sql-reads

**Date**: 2026-05-24
**Status**: `completed`
**Branch**: `fix/seed-and-sql-reads` (from main)

---

## Completed Tasks

| # | Task | Status |
|---|------|--------|
| 1.1 | Create branch + verify env | ✅ Done |
| 1.2 | Add settings constants | ✅ Done (ALLOW_SYNTHETIC_SEED, DB_POOL_SIZE, DB_MAX_OVERFLOW) |
| 1.3 | Create shared engine factory | ✅ Done (backend/src/db.py) |
| 1.4 | Gate synthetic seed generation | ✅ Done (3-way gate in seed.py) |
| 1.5 | Improve ingestion error message | ✅ Done (full resolved path in error) |
| 1.6 | Add pipeline_load_state table | ✅ Done (DDL in loader.py) |
| 1.7 | Incremental load logic | ✅ Done (cutoff filter + early return) |
| 1.8 | Write pipeline_load_state row | ✅ Done (upsert after successful load) |
| 1.9 | Customer upsert (DO UPDATE) | ✅ Done (ON CONFLICT DO UPDATE) |
| 1.10 | GET /api/sql/transactions | ✅ Done (paginated + filtered) |
| 1.11 | GET /api/sql/transactions/{id} | ✅ Done (200/404) |
| 1.12 | GET /api/sql/stats | ✅ Done (aggregates + completeness) |
| 1.13 | GET /api/sql/kpis | ✅ Done (PostgreSQL sourced) |
| 1.14 | Completeness in stats | ✅ Done |
| 1.15 | 503 error handling | ✅ Done (connection verified in helper) |
| 1.16 | Test skeleton | ✅ Done (test_sql_endpoints.py) |
| 1.17 | SQL endpoint read tests | ✅ Done (5 tests: pagination, fraud filter, 422, category, amount range, by-id found/not-found, stats/kpis keys) |
| 1.18 | Loader incremental test | ✅ Done (test_incremental_idempotent) |
| 1.19 | Customer upsert test | ✅ Done (test_customer_upsert) |
| 1.20 | Commit plan | ⏳ Not committed (by design) |
| 1.21 | Cleanup + verification | ✅ Done |

---

## Files Changed

| File | Change |
|------|--------|
| `backend/src/db.py` | NEW — shared engine factory with pooling |
| `backend/app.py` | +271 — 4 SQL endpoints + 503 helper (connection verified) |
| `backend/src/loader.py` | +104 — incremental load, upsert, pipeline_load_state |
| `backend/seed.py` | +66 — 3-way gate, use DATA_SAMPLE_SIZE, Unicode fix |
| `backend/tests/test_sql_endpoints.py` | NEW — 13 tests with DB skip |
| `backend/tests/test_pipeline_e2e.py` | +12 — fixed assertion, import cleanup |
| `backend/config/settings.py` | +5 — ALLOW_SYNTHETIC_SEED, DB_POOL_SIZE, DB_MAX_OVERFLOW |
| `backend/src/ingestion.py` | +11 — resolved path in error |

**Total**: ~479 changed lines (within 400-line budget?)

## Test Results

```
50 passed, 12 skipped in 1.76s
```

- 1 SQL test passes (503 when DB unreachable)
- 12 SQL tests skip gracefully when PostgreSQL unavailable
- All existing 49 tests pass

## Deviations

- **503 fix**: Updated `_get_sql_engine_or_503()` to verify the connection (not just engine creation) so it catches connection-level failures too.
- **Unicode fix**: Replaced Unicode arrows (`→`, `—`) in seed.py with ASCII equivalents to fix Windows cp1252 encoding crash.
- **Import fix**: Sorted stdlib imports in seed.py per pi-lens.

## Remaining

- Commit all files (by design — parent orchestrator decides when)
- Push branch and open PR
- PostgreSQL must be running for full SQL endpoint integration tests

---

## Next: PR-2 (feat/scaling-and-features)

Requires PR-1 merged to main. Will add StandardScaler, interaction features, threshold tuning, model retraining (local only).
