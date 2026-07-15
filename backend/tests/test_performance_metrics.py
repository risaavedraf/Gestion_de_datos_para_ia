from importlib import import_module

from fastapi.testclient import TestClient

PerformanceMetricsStore = import_module("backend.src.metrics").PerformanceMetricsStore


def test_performance_metrics_store_records_and_summarizes_operations(tmp_path):
    store = PerformanceMetricsStore(tmp_path / "performance_metrics.jsonl")

    with store.measure("pipeline.stage", stage="bronze", sample_size=100) as metric:
        metric.set_rows_processed(100)

    history = store.history(limit=10)
    assert len(history) == 1
    record = history[0]
    assert record["operation"] == "pipeline.stage"
    assert record["stage"] == "bronze"
    assert record["status"] == "success"
    assert record["duration_ms"] >= 0
    assert record["memory_rss_mb_start"] >= 0
    assert record["memory_rss_mb_end"] >= 0
    assert record["memory_rss_mb_peak"] >= record["memory_rss_mb_start"]
    assert record["rows_processed"] == 100

    summary = store.summary()
    assert summary["total_operations"] == 1
    assert summary["latest"] == record
    assert summary["by_operation"]["pipeline.stage"]["count"] == 1


def test_performance_metrics_endpoint_returns_history_and_summary(tmp_path, monkeypatch):
    import backend.app as app_module

    store = PerformanceMetricsStore(tmp_path / "performance_metrics.jsonl")
    with store.measure("pipeline.stage", stage="silver"):
        pass
    monkeypatch.setattr(app_module, "metrics_store", store)

    client = TestClient(app_module.app)
    response = client.get("/api/metrics/performance?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_operations"] == 1
    assert body["history"][0]["stage"] == "silver"


def test_performance_metrics_store_records_failures(tmp_path):
    store = PerformanceMetricsStore(tmp_path / "performance_metrics.jsonl")

    try:
        with store.measure("model.train"):
            raise RuntimeError("training failed")
    except RuntimeError:
        pass

    record = store.history(limit=1)[0]
    assert record["status"] == "error"
    assert record["error_type"] == "RuntimeError"
