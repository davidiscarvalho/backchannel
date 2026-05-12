"""Observability primitives: structured JSON logging + in-memory metrics.

Designed to slot into the existing WSGI app without adding dependencies.
The /metrics endpoint exposes counters and histograms in Prometheus text
exposition format — scrape it from the existing Hetzner Prometheus.

Why in-memory and dependency-free: keeps the deployment story (E5)
single-container. When the app scales out, swap StatRegistry for a
push-to-Otel collector — the per-callsite call shape doesn't change.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

# --- Logging -------------------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """One-line JSON per log record. Adds request_id when present in extra."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for attr in ("request_id", "key_id", "path", "status", "duration_ms", "traceparent"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_json_logging(level: str = "INFO") -> None:
    """Replace the root handler with a JSON one. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers so we don't double-log under reloads.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)


# --- Metrics -------------------------------------------------------------


@dataclass
class _Histogram:
    buckets: list[float]  # upper bounds, ascending
    counts: list[int] = field(default_factory=list)
    sum_: float = 0.0
    total: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.sum_ += value
        self.total += 1
        for i, upper in enumerate(self.buckets):
            if value <= upper:
                self.counts[i] += 1


class StatRegistry:
    """Thread-safe in-memory metrics registry. Prom-compatible text output."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, frozenset], int] = {}
        self._histograms: dict[tuple[str, frozenset], _Histogram] = {}
        self._histogram_buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]

    def inc(self, name: str, labels: dict[str, str] | None = None, by: int = 1) -> None:
        key = (name, frozenset((labels or {}).items()))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + by

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = (name, frozenset((labels or {}).items()))
        with self._lock:
            hist = self._histograms.get(key)
            if hist is None:
                hist = _Histogram(buckets=list(self._histogram_buckets))
                self._histograms[key] = hist
            hist.observe(value)

    def render_prometheus(self) -> str:
        """Render registry as Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            # Counters
            by_name: dict[str, list[tuple[frozenset, int]]] = {}
            for (name, labels), value in self._counters.items():
                by_name.setdefault(name, []).append((labels, value))
            for name, rows in by_name.items():
                lines.append(f"# TYPE {name} counter")
                for labels, value in rows:
                    lines.append(f"{name}{_fmt_labels(labels)} {value}")
            # Histograms
            hist_by_name: dict[str, list[tuple[frozenset, _Histogram]]] = {}
            for (name, labels), hist in self._histograms.items():
                hist_by_name.setdefault(name, []).append((labels, hist))
            for name, rows in hist_by_name.items():
                lines.append(f"# TYPE {name} histogram")
                for labels, hist in rows:
                    base = dict(labels)
                    for upper, count in zip(hist.buckets, hist.counts):
                        bucket_labels = {**base, "le": str(upper)}
                        lines.append(f"{name}_bucket{_fmt_labels(frozenset(bucket_labels.items()))} {count}")
                    bucket_inf = {**base, "le": "+Inf"}
                    lines.append(f"{name}_bucket{_fmt_labels(frozenset(bucket_inf.items()))} {hist.total}")
                    lines.append(f"{name}_sum{_fmt_labels(labels)} {hist.sum_}")
                    lines.append(f"{name}_count{_fmt_labels(labels)} {hist.total}")
        return "\n".join(lines) + "\n"


def _fmt_labels(labels: Iterable[tuple[str, str]]) -> str:
    pairs = [(k, str(v).replace('"', '\\"')) for k, v in labels]
    if not pairs:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in sorted(pairs)) + "}"


# Module-level default registry. The app reuses this so /metrics scrapes
# every counter, regardless of which module recorded it.
registry = StatRegistry()


# --- Convenience helpers --------------------------------------------------


def record_request(method: str, path_template: str, status: int, duration_ms: float) -> None:
    """Standard request observation called from the WSGI dispatch loop."""
    registry.inc(
        "backchannel_requests_total",
        labels={"method": method, "status": str(status), "path": path_template},
    )
    registry.observe(
        "backchannel_request_duration_seconds",
        duration_ms / 1000.0,
        labels={"method": method, "path": path_template},
    )
