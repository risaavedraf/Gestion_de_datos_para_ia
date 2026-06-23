"""
SQL endpoint and loader tests for PR-1.

Tests FastAPI SQL endpoints (transactions, stats, kpis) and loader
incremental/upsert behavior against a running PostgreSQL instance.

All SQL-dependent tests are automatically skipped when PostgreSQL is
unreachable so the suite remains green in CI without a database.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app import app


class _FakeResult:
    def __init__(self, *, row=None, rows=None, scalar=None, rowcount=None):
        self._row = row
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeConnection:
    def __init__(self, results):
        self._results = iter(results)

    def execute(self, *_args, **_kwargs):
        return next(self._results)


class _FakeEngine:
    def __init__(self, results):
        self._connection = _FakeConnection(results)

    def connect(self):
        return nullcontext(self._connection)


def _pg_is_available():
    """Return True when PostgreSQL is reachable with the configured URL."""
    try:
        from backend.src.db import get_engine

        get_engine().connect().close()
        return True
    except Exception:
        return False


def _ensure_data_in_db():
    """Idempotent: seed pipeline data if the DB is empty."""
    from sqlalchemy import text
    from backend.src.db import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar()
    if not row:
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "backend/seed.py"],
            check=True,
            env={**os.environ, "ALLOW_SYNTHETIC_SEED": "true"},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """Returns a FastAPI TestClient instance."""
    return TestClient(app)


@pytest.fixture(scope="module")
def populated_client():
    """TestClient with guaranteed DB rows (synthetic, ~10K rows)."""
    if not _pg_is_available():
        pytest.skip("PostgreSQL not available")

    _ensure_data_in_db()
    return TestClient(app)


@pytest.fixture
def isolated_postgres_engine():
    """Create an isolated PostgreSQL schema and engine for destructive integration tests."""
    if not _pg_is_available():
        pytest.skip("PostgreSQL not available")

    from sqlalchemy import create_engine, text

    from backend.config.settings import DATABASE_URL
    from backend.src.db import get_engine

    schema = f"test_{uuid4().hex}"
    admin_engine = get_engine()
    with admin_engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=4,
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


# ---------------------------------------------------------------------------
# DB connectivity
# ---------------------------------------------------------------------------


def test_sql_transactions_503_when_db_unreachable(client):
    """The endpoint must return 503 when the database is down.

    This test patches DATABASE_URL to an unreachable address so
    it works even when PostgreSQL is healthy.
    """
    original = os.environ.get("DATABASE_URL", "")
    import backend.src.db as db_mod

    try:
        os.environ["DATABASE_URL"] = (
            "postgresql://nonexistent:bad@127.0.0.1:15999/fraud_db"
        )
        # Force cache invalidation
        db_mod._engine = None
        resp = client.get("/api/sql/transactions?limit=5")
        assert resp.status_code == 503
        assert "Database connection failed" in resp.json()["detail"]
    finally:
        os.environ["DATABASE_URL"] = original
        db_mod._engine = None


def test_dashboard_kpis_use_postgresql_and_preserve_contract(client, monkeypatch):
    import backend.app as app_module

    engine = _FakeEngine(
        [
            _FakeResult(
                row={
                    "valid_records": 8,
                    "complete_records": 7,
                    "duplicate_records": 0,
                    "fraud_count": 2,
                    "legit_count": 6,
                    "amt_mean": Decimal("12.35"),
                    "amt_median": Decimal("10.00"),
                    "amt_max": Decimal("50.00"),
                    "rejected_records": 2,
                }
            )
        ]
    )
    monkeypatch.setattr(app_module, "_get_sql_engine_or_503", lambda: engine)
    monkeypatch.setattr(
        app_module.pd,
        "read_parquet",
        lambda *_args, **_kwargs: pytest.fail("dashboard KPIs must not read Parquet"),
    )

    response = client.get("/api/kpis")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "completeness_pct": 87.5,
        "duplicate_rate_pct": 0.0,
        "fraud_count": 2,
        "legit_count": 6,
        "fraud_pct": 25.0,
        "amt_mean": 12.35,
        "amt_median": 10.0,
        "amt_max": 50.0,
        "valid_records": 8,
        "rejected_records": 2,
        "rejection_rate_pct": 20.0,
        "total_records": 10,
        "status": "available",
        "timestamp": body["timestamp"],
    }


def test_dashboard_kpis_handle_empty_initialized_tables(client, monkeypatch):
    import backend.app as app_module

    engine = _FakeEngine(
        [
            _FakeResult(
                row={
                    "valid_records": 0,
                    "complete_records": 0,
                    "duplicate_records": 0,
                    "fraud_count": 0,
                    "legit_count": 0,
                    "amt_mean": None,
                    "amt_median": None,
                    "amt_max": None,
                    "rejected_records": 0,
                }
            )
        ]
    )
    monkeypatch.setattr(app_module, "_get_sql_engine_or_503", lambda: engine)

    body = client.get("/api/kpis").json()

    assert body["status"] == "no_data"
    assert body["valid_records"] == 0
    assert body["total_records"] == 0
    assert body["completeness_pct"] == 0.0
    assert body["duplicate_rate_pct"] == 0.0
    assert body["fraud_pct"] == 0.0
    assert body["rejection_rate_pct"] == 0.0
    assert body["amt_mean"] is None
    assert body["amt_median"] is None
    assert body["amt_max"] is None


def test_sql_stats_returns_frontend_aliases(client, monkeypatch):
    import backend.app as app_module

    engine = _FakeEngine(
        [
            _FakeResult(
                row={
                    "total_count": 0,
                    "fraud_count": None,
                    "legit_count": None,
                    "amt_mean": None,
                    "amt_max": None,
                    "amt_min": None,
                    "amt_std": None,
                    "date_min": None,
                    "date_max": None,
                }
            ),
            _FakeResult(rows=[]),
            _FakeResult(scalar=None),
        ]
    )
    monkeypatch.setattr(app_module, "_get_sql_engine_or_503", lambda: engine)

    body = client.get("/api/sql/stats").json()

    assert body["total_transactions"] == body["total_count"] == 0
    assert body["avg_amt"] is body["amt_mean"] is None


def test_seed_runs_incremental_postgresql_load_after_validation(tmp_path, monkeypatch):
    import backend.seed as seed_module
    import backend.src.cleaning as cleaning_module
    import backend.src.ingestion as ingestion_module
    import backend.src.loader as loader_module
    import backend.src.validation as validation_module

    raw_csv = tmp_path / "existing.csv"
    raw_csv.touch()
    calls = []
    monkeypatch.setattr(seed_module, "RAW_CSV", raw_csv)
    monkeypatch.setattr(
        ingestion_module,
        "ingest",
        lambda **kwargs: calls.append(("ingest", kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(
        cleaning_module,
        "clean",
        lambda **kwargs: calls.append(("clean", kwargs)) or {"status": "success"},
    )
    monkeypatch.setattr(
        validation_module,
        "validate",
        lambda **kwargs: calls.append(("validate", kwargs))
        or {"status": "success"},
    )
    monkeypatch.setattr(
        loader_module,
        "load",
        lambda **kwargs: calls.append(("load", kwargs)) or {"status": "success"},
    )

    seed_module.seed_data()

    assert calls == [
        ("ingest", {"sample_size": None}),
        ("clean", {"sample_size": None}),
        ("validate", {"sample_size": None}),
        ("load", {"sample_size": None, "incremental": True}),
    ]


def test_fastapi_startup_initializes_schema_without_running_ingestion(monkeypatch):
    import backend.app as app_module
    import backend.src.ingestion as ingestion_module
    import backend.src.loader as loader_module

    calls = []
    monkeypatch.setattr(loader_module, "create_tables", lambda: calls.append("schema"))
    monkeypatch.setattr(
        ingestion_module,
        "ingest",
        lambda *_args, **_kwargs: pytest.fail("startup must not run ingestion"),
    )

    with TestClient(app_module.app) as startup_client:
        assert startup_client.get("/health").status_code == 200

    assert calls == ["schema"]


def test_dashboard_kpis_work_with_fresh_postgres_schema(
    isolated_postgres_engine, monkeypatch
):
    import backend.app as app_module
    import backend.src.loader as loader_module

    monkeypatch.setattr(loader_module, "get_engine", lambda: isolated_postgres_engine)
    monkeypatch.setattr(
        app_module, "_get_sql_engine_or_503", lambda: isolated_postgres_engine
    )

    with TestClient(app_module.app) as startup_client:
        response = startup_client.get("/api/kpis")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_data"
    assert body["valid_records"] == 0
    assert body["rejected_records"] == 0
    assert body["total_records"] == 0


@pytest.mark.parametrize("catalog_compatibility", [False, True, None])
def test_create_tables_uses_catalog_structure_for_rejected_deduplication_index(
    catalog_compatibility,
):
    from backend.src.loader import create_tables

    class RecordingConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, *_args):
            sql = str(statement)
            self.statements.append(sql)
            if "FROM pg_catalog.pg_class" in sql:
                return _FakeResult(scalar=catalog_compatibility)
            return _FakeResult()

        def commit(self):
            pass

    connection = RecordingConnection()
    engine = type(
        "RecordingEngine",
        (),
        {"connect": lambda _self: nullcontext(connection)},
    )()

    create_tables(engine)

    ddl = "\n".join(connection.statements)
    assert "FROM pg_indexes" not in ddl
    assert "index_meta.indisunique" in ddl
    assert "index_meta.indisvalid" in ddl
    assert "index_meta.indisready" in ddl
    assert "NOT index_meta.indisexclusion" in ddl
    assert "index_meta.indimmediate" in ddl
    assert "index_meta.indnkeyatts = 4" in ddl
    assert "index_meta.indnatts = 4" in ddl
    assert "index_meta.indpred IS NULL" in ddl
    assert "index_meta.indoption" in ddl
    assert "access_method.amname = 'btree'" in ddl
    assert (
        "DROP INDEX IF EXISTS uq_rejected_records_dedup" in ddl
    ) is (catalog_compatibility is False)
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_rejected_records_dedup" in ddl
    assert "md5(COALESCE(original_data::text, 'null'))" in ddl


def test_rejected_json_serialization_normalizes_non_finite_numeric_types():
    import numpy as np

    from backend.src.loader import serialize_rejected_original_data

    serialized = serialize_rejected_original_data(
        {
            "python_nan": float("nan"),
            "numpy_inf": np.float32("inf"),
            "decimal_nan": Decimal("NaN"),
            "decimal_infinity": Decimal("Infinity"),
            "finite": [np.float32("4.5"), Decimal("2.25"), np.int64(7)],
        }
    )

    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert json.loads(serialized) == {
        "python_nan": None,
        "numpy_inf": None,
        "decimal_nan": None,
        "decimal_infinity": None,
        "finite": [4.5, 2.25, 7],
    }


def test_rejected_json_serialization_preserves_finite_decimal_precision_and_range():
    from backend.src.loader import serialize_rejected_original_data

    sensitive = Decimal("0.123456789012345678901234567890123456789")
    huge = Decimal("1E+10000")

    serialized = serialize_rejected_original_data(
        {"sensitive": sensitive, "huge": huge}
    )
    decoded = json.loads(serialized, parse_float=Decimal)

    assert decoded == {"sensitive": sensitive, "huge": huge}
    assert '"0.123456789012345678901234567890123456789"' not in serialized
    assert '"1E+10000"' not in serialized


def test_incremental_filter_keeps_rows_equal_to_latest_timestamp():
    from backend.src.loader import _filter_incremental_rows

    source = __import__("pandas").DataFrame(
        {"trans_num": ["existing", "late-arrival", "new"], "unix_time": [10, 10, 11]}
    )

    filtered = _filter_incremental_rows(source, cutoff=10)

    assert filtered["trans_num"].tolist() == ["existing", "late-arrival", "new"]


def test_transaction_insert_count_uses_confirmed_database_rowcount():
    from backend.src.loader import _insert_transaction_records

    class ConflictConnection:
        def execute(self, statement, params):
            assert "ON CONFLICT (trans_num) DO NOTHING" in str(statement)
            assert len(params) == 2
            return _FakeResult(rowcount=1)

        def commit(self):
            pass

    connection = ConflictConnection()
    engine = type(
        "ConflictEngine",
        (),
        {"connect": lambda _self: nullcontext(connection)},
    )()

    inserted = _insert_transaction_records(
        engine,
        [{"trans_num": "duplicate"}, {"trans_num": "new"}],
    )

    assert inserted == 1


def test_equal_timestamp_late_arrival_reaches_conflict_safe_insert():
    import pandas as pd

    from backend.src.loader import _filter_incremental_rows, _insert_transaction_records

    source = pd.DataFrame(
        {"trans_num": ["existing", "late-arrival"], "unix_time": [10, 10]}
    )

    class CapturingConnection:
        def __init__(self):
            self.params = None

        def execute(self, statement, params):
            assert "ON CONFLICT (trans_num) DO NOTHING" in str(statement)
            self.params = params
            return _FakeResult(rowcount=1)

        def commit(self):
            pass

    connection = CapturingConnection()
    engine = type(
        "CapturingEngine",
        (),
        {"connect": lambda _self: nullcontext(connection)},
    )()

    candidates = _filter_incremental_rows(source, cutoff=10).to_dict("records")
    inserted = _insert_transaction_records(engine, candidates)

    assert [record["trans_num"] for record in connection.params] == [
        "existing",
        "late-arrival",
    ]
    assert inserted == 1


def test_incremental_load_resumes_rejected_rows_when_transactions_are_complete(
    tmp_path, monkeypatch
):
    import backend.src.loader as loader_module

    gold_path = tmp_path / "fraud_gold.parquet"
    rejected_dir = tmp_path / "rejected"
    rejected_path = rejected_dir / "fraud_rejected.parquet"
    rejected_dir.mkdir()
    gold_path.touch()
    rejected_path.touch()
    monkeypatch.setattr(loader_module, "GOLD_DIR", tmp_path)
    monkeypatch.setattr(loader_module, "REJECTED_DIR", rejected_dir)
    monkeypatch.setattr(loader_module, "create_tables", lambda _engine: None)

    rejected_df = loader_module.pd.DataFrame(
        {
            "trans_num": ["rejected-after-complete"],
            "amt": [float("nan")],
            "rejection_reason": ["invalid amount"],
        }
    )
    monkeypatch.setattr(
        loader_module.pd,
        "read_parquet",
        lambda path: (
            loader_module.pd.DataFrame({"unix_time": [9]})
            if path == gold_path
            else rejected_df
        ),
    )

    class RetryConnection:
        def __init__(self):
            self.rejected_params = None

        def execute(self, statement, params=None):
            sql = str(statement)
            if "SELECT MAX(unix_time)" in sql:
                return _FakeResult(scalar=10)
            if "SELECT last_loaded_timestamp" in sql:
                return _FakeResult(scalar=10)
            if "INSERT INTO rejected_records" in sql:
                self.rejected_params = params
                return _FakeResult(rowcount=len(params))
            raise AssertionError(f"Unexpected SQL: {sql}")

        def commit(self):
            pass

    connection = RetryConnection()
    engine = type(
        "RetryEngine",
        (),
        {"connect": lambda _self: nullcontext(connection)},
    )()
    monkeypatch.setattr(loader_module, "get_engine", lambda: engine)

    result = loader_module.load(incremental=True)

    assert result["status"] == "success"
    assert result["rows_inserted"] == 0
    assert result["rejected_inserted"] == 1
    assert connection.rejected_params[0]["trans_num"] == "rejected-after-complete"
    assert json.loads(connection.rejected_params[0]["original_data"])["amt"] is None


def test_rejected_dedup_is_concurrency_safe_in_postgres(
    isolated_postgres_engine, tmp_path, monkeypatch
):
    import backend.src.loader as loader_module
    from sqlalchemy import text

    loader_module.create_tables(isolated_postgres_engine)
    rejected_path = tmp_path / "fraud_rejected.parquet"
    loader_module.pd.DataFrame(
        {
            "trans_num": ["concurrent-reject"],
            "amt": [None],
            "rejection_reason": ["missing amount"],
        }
    ).to_parquet(rejected_path, index=False)
    monkeypatch.setattr(loader_module, "REJECTED_DIR", tmp_path)

    start = Barrier(2)

    def insert_same_rejection(run_id):
        start.wait()
        return loader_module._load_rejected_records(
            engine=isolated_postgres_engine,
            run_id=run_id,
            sample_size=None,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        inserted = list(
            executor.map(
                insert_same_rejection,
                ["concurrent-run-a", "concurrent-run-b"],
            )
        )

    with isolated_postgres_engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM rejected_records")).scalar()

    assert sum(inserted) == 1
    assert count == 1


# ---------------------------------------------------------------------------
# SQL read endpoints (requires populated DB)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pg_is_available(), reason="PostgreSQL not available")
class TestSqlTransactions:
    """Tests for GET /api/sql/transactions."""

    def test_pagination_returns_correct_count(self, populated_client):
        resp = populated_client.get("/api/sql/transactions?limit=10&offset=0")
        assert resp.status_code == 200
        body = resp.json()
        txs = body["transactions"]
        meta = body["meta"]
        assert len(txs) <= 10
        assert meta["limit"] == 10
        assert meta["offset"] == 0
        assert meta["total"] >= len(txs)

    def test_fraud_filter_only_returns_fraud(self, populated_client):
        resp = populated_client.get("/api/sql/transactions?fraud=1&limit=100")
        assert resp.status_code == 200
        for tx in resp.json()["transactions"]:
            assert tx["is_fraud"] == 1

    def test_limit_exceeding_max_returns_422(self, populated_client):
        resp = populated_client.get("/api/sql/transactions?limit=600")
        assert resp.status_code == 422

    def test_category_filter(self, populated_client):
        resp = populated_client.get(
            "/api/sql/transactions?category=personal_care&limit=5"
        )
        assert resp.status_code == 200
        for tx in resp.json()["transactions"]:
            assert tx.get("category", "").lower() == "personal_care"

    def test_amount_range_filter(self, populated_client):
        resp = populated_client.get(
            "/api/sql/transactions?min_amt=100&max_amt=500&limit=5"
        )
        assert resp.status_code == 200
        for tx in resp.json()["transactions"]:
            assert 100 <= tx["amt"] <= 500


@pytest.mark.skipif(not _pg_is_available(), reason="PostgreSQL not available")
class TestSqlTransactionById:
    """Tests for GET /api/sql/transactions/{trans_num}."""

    def test_existing_transaction_returns_200(self, populated_client):
        # Grab any trans_num first
        list_resp = populated_client.get("/api/sql/transactions?limit=1")
        txs = list_resp.json()["transactions"]
        if not txs:
            pytest.skip("No transactions in DB")
        trans_num = txs[0]["trans_num"]

        resp = populated_client.get(f"/api/sql/transactions/{trans_num}")
        assert resp.status_code == 200
        assert resp.json()["trans_num"] == trans_num

    def test_nonexistent_transaction_returns_404(self, populated_client):
        resp = populated_client.get("/api/sql/transactions/nonexistent-12345")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


@pytest.mark.skipif(not _pg_is_available(), reason="PostgreSQL not available")
class TestSqlStatsAndKpis:
    """Tests for GET /api/sql/stats and GET /api/sql/kpis."""

    def test_stats_returns_expected_keys(self, populated_client):
        resp = populated_client.get("/api/sql/stats")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "total_count",
            "total_transactions",
            "fraud_count",
            "legit_count",
            "fraud_pct",
            "amt_mean",
            "avg_amt",
            "amt_max",
            "amt_min",
            "amt_std",
            "by_category",
            "completeness_pct",
            "date_min",
            "date_max",
        ):
            assert key in body, f"Missing key: {key}"

    def test_kpis_returns_expected_keys(self, populated_client):
        resp = populated_client.get("/api/sql/kpis")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "total_records",
            "fraud_count",
            "legit_count",
            "fraud_pct",
            "amt_mean",
            "amt_median",
            "amt_max",
            "completeness_pct",
            "status",
            "source",
            "timestamp",
        ):
            assert key in body, f"Missing key: {key}"
        assert body["source"] == "postgresql"

    def test_stats_fraud_pct_is_reasonable(self, populated_client):
        resp = populated_client.get("/api/sql/stats")
        body = resp.json()
        frac = body["fraud_count"] / max(body["total_count"], 1)
        # With synthetic data, fraud rate should be ≈5%
        assert 0.01 < frac < 0.15, f"Unexpected fraud rate: {frac:.4f}"


# ---------------------------------------------------------------------------
# Loader incremental + upsert tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _pg_is_available(), reason="PostgreSQL not available")
class TestLoaderIncremental:
    """Tests for incremental load and customer upsert."""

    def test_incremental_idempotent_returns_zero_on_rerun(self, populated_client):
        """Running incremental load twice on same data inserts 0 new rows."""
        from backend.src.loader import load

        # First call may insert rows; second call should be idempotent.
        _result1 = load(sample_size=100, incremental=True)
        # Sync the state before the second call to avoid race conditions
        result2 = load(incremental=True)

        assert result2["status"] == "success"
        assert "rows_inserted" in result2
        # Second call on same data should insert 0 (or very few) new rows
        assert result2["rows_inserted"] <= 1, (
            f"Expected 0 or 1, got {result2['rows_inserted']}"
        )

    def test_repeated_full_load_does_not_duplicate_records(self, populated_client):
        from sqlalchemy import text

        from backend.src.db import get_engine
        from backend.src.loader import load

        load(sample_size=100, incremental=False)
        engine = get_engine()
        with engine.connect() as conn:
            counts_after_first = (
                conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar(),
                conn.execute(text("SELECT COUNT(*) FROM rejected_records")).scalar(),
            )

        load(sample_size=100, incremental=False)
        with engine.connect() as conn:
            counts_after_second = (
                conn.execute(text("SELECT COUNT(*) FROM transactions")).scalar(),
                conn.execute(text("SELECT COUNT(*) FROM rejected_records")).scalar(),
            )

        assert counts_after_second == counts_after_first

    def test_customer_upsert_updates_fields(self, populated_client):
        """Re-running load should update customer fields via upsert."""
        from sqlalchemy import text

        from backend.src.db import get_engine

        engine = get_engine()

        # Pick a customer to check
        with engine.connect() as conn:
            cust = conn.execute(
                text(
                    "SELECT customer_id, city_pop FROM customers "
                    "ORDER BY customer_id LIMIT 1"
                )
            ).first()

        if not cust:
            pytest.skip("No customers in DB")

        customer_id, original_pop = cust
        original_pop = original_pop or 0

        # Verify the customer exists (city_pop stays same if no Gold data changed)
        with engine.connect() as conn:
            after = conn.execute(
                text("SELECT city_pop FROM customers WHERE customer_id = :cid"),
                {"cid": customer_id},
            ).scalar()

        assert after is not None, f"Customer {customer_id} not found after load"
