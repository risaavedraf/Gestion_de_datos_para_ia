"""Persistent performance measurements for expensive application operations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from json import JSONDecodeError
from pathlib import Path
from threading import Event, Lock, Thread
from time import perf_counter
from typing import Any, Iterator

import psutil


class PerformanceMeasurement:
    """Measure elapsed time and process RSS for one operation."""

    def __init__(
        self,
        store: "PerformanceMetricsStore",
        operation: str,
        metadata: dict[str, Any],
    ) -> None:
        self._store = store
        self._operation = operation
        self._metadata = metadata
        self._process = psutil.Process()
        self._stop_sampling = Event()
        self._peak_rss_bytes = 0
        self._start_rss_bytes = 0
        self._rows_processed: int | None = None
        self._started_at = ""
        self._started_at_counter = 0.0
        self._sampler: Thread | None = None

    def start(self) -> None:
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._started_at_counter = perf_counter()
        self._start_rss_bytes = self._rss_bytes()
        self._peak_rss_bytes = self._start_rss_bytes
        self._sampler = Thread(target=self._sample_memory, daemon=True)
        self._sampler.start()

    def finish(self, error_type: str | None = None) -> None:
        self._stop_sampling.set()
        if self._sampler:
            self._sampler.join(timeout=0.2)

        end_rss_bytes = self._rss_bytes()
        self._peak_rss_bytes = max(self._peak_rss_bytes, end_rss_bytes)
        record: dict[str, Any] = {
            "timestamp": self._started_at,
            "operation": self._operation,
            "status": "error" if error_type else "success",
            "duration_ms": round((perf_counter() - self._started_at_counter) * 1000, 2),
            "memory_rss_mb_start": round(self._start_rss_bytes / (1024 * 1024), 2),
            "memory_rss_mb_end": round(end_rss_bytes / (1024 * 1024), 2),
            "memory_rss_mb_peak": round(self._peak_rss_bytes / (1024 * 1024), 2),
            **self._metadata,
        }
        if self._rows_processed is not None:
            record["rows_processed"] = self._rows_processed
        if error_type:
            record["error_type"] = error_type
        self._store._append(record)

    def set_rows_processed(self, rows_processed: int | None) -> None:
        """Attach the actual input/output volume once it is known."""
        self._rows_processed = rows_processed

    def _sample_memory(self) -> None:
        while not self._stop_sampling.wait(0.05):
            self._peak_rss_bytes = max(self._peak_rss_bytes, self._rss_bytes())

    def _rss_bytes(self) -> int:
        return self._process.memory_info().rss


class PerformanceMetricsStore:
    """Append-only JSONL store with history and dashboard summaries."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._write_lock = Lock()

    @contextmanager
    def measure(
        self, operation: str, **metadata: Any
    ) -> Iterator[PerformanceMeasurement]:
        measurement = PerformanceMeasurement(self, operation, metadata)
        measurement.start()
        try:
            yield measurement
        except Exception as error:
            measurement.finish(error.__class__.__name__)
            raise
        else:
            measurement.finish()

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []

        records: list[dict[str, Any]] = []
        with self._path.open(encoding="utf-8") as file:
            for line in file:
                try:
                    records.append(json.loads(line))
                except JSONDecodeError:
                    continue
        return list(reversed(records[-limit:]))

    def summary(self) -> dict[str, Any]:
        history = self.history(limit=500)
        by_operation: dict[str, dict[str, Any]] = {}
        for record in history:
            operation = record["operation"]
            current = by_operation.setdefault(
                operation,
                {"count": 0, "duration_ms_total": 0.0, "memory_rss_mb_peak": 0.0},
            )
            current["count"] += 1
            current["duration_ms_total"] += record["duration_ms"]
            current["memory_rss_mb_peak"] = max(
                current["memory_rss_mb_peak"], record["memory_rss_mb_peak"]
            )

        for current in by_operation.values():
            current["duration_ms_average"] = round(
                current.pop("duration_ms_total") / current["count"], 2
            )

        return {
            "total_operations": len(history),
            "latest": history[0] if history else None,
            "by_operation": by_operation,
        }

    def _append(self, record: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            with self._path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record) + "\n")
