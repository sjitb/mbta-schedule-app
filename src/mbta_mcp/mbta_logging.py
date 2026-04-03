"""Shared structured logging for the MBTA MCP server."""

from __future__ import annotations

import atexit
import configparser
import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT_DIR = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT_DIR / "config.ini"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_logging_settings() -> dict[str, Any]:
    config = configparser.ConfigParser()
    config.read(_CONFIG_PATH)
    section = config["logging"] if config.has_section("logging") else {}

    return {
        "enabled": _as_bool(section.get("enabled"), True),
        "level": section.get("level", "INFO").upper(),
        "directory": section.get("directory", "logs"),
        "file_strategy": section.get("file_strategy", "session").lower(),
        "session_prefix": section.get("session_prefix", "mbta-mcp"),
        "mirror_to_stderr": _as_bool(section.get("mirror_to_stderr"), False),
    }


def _build_log_path(base_dir: Path, prefix: str, strategy: str) -> tuple[str, Path]:
    now = _utc_now()
    day_dir = base_dir / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)

    if strategy == "day":
        session_id = now.strftime("%Y-%m-%d")
        filename = f"{prefix}-{session_id}.jsonl"
    else:
        session_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
        filename = f"{prefix}-{session_id}.jsonl"

    return session_id, day_dir / filename


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload.update(event)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True)


def _configure_logging() -> tuple[logging.Logger, str, Path | None]:
    settings = _load_logging_settings()
    logger = logging.getLogger("mbta_mcp")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(getattr(logging, settings["level"], logging.INFO))

    # Keep third-party HTTP debug output off the console. Phase 1 telemetry
    # is written through the structured logger instead.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if not settings["enabled"]:
        logger.addHandler(logging.NullHandler())
        return logger, "disabled", None

    log_dir = _ROOT_DIR / str(settings["directory"])
    session_id, log_path = _build_log_path(
        log_dir,
        str(settings["session_prefix"]),
        str(settings["file_strategy"]),
    )

    formatter = _JsonLineFormatter()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if settings["mirror_to_stderr"]:
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(formatter)
        logger.addHandler(stderr_handler)

    return logger, session_id, log_path


LOGGER, SESSION_ID, LOG_PATH = _configure_logging()


def log_event(level: int, event_type: str, **fields: Any) -> None:
    event = {"event_type": event_type, "session_id": SESSION_ID, **fields}
    LOGGER.log(level, event_type, extra={"event": event})


# ---------------------------------------------------------------------------
# In-memory metrics (Phase 2)
# ---------------------------------------------------------------------------

class _Metrics:
    _MAX_SAMPLES = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tool_invocations: dict[str, int] = defaultdict(int)
        self.tool_failures: dict[str, int] = defaultdict(int)
        self.http_requests: dict[str, int] = defaultdict(int)
        self.http_failures: dict[str, int] = defaultdict(int)
        self.tool_latencies: dict[str, list[float]] = defaultdict(list)
        self.http_latencies: dict[str, list[float]] = defaultdict(list)

    def record_tool(
        self,
        tool_name: str,
        outcome: str,
        failure_category: str | None,
        duration_ms: float,
    ) -> None:
        with self._lock:
            self.tool_invocations[tool_name] += 1
            if outcome != "success":
                key = f"{tool_name}/{failure_category or 'unknown'}"
                self.tool_failures[key] += 1
            samples = self.tool_latencies[tool_name]
            if len(samples) < self._MAX_SAMPLES:
                samples.append(duration_ms)

    def record_http(
        self,
        endpoint: str,
        outcome: str,
        failure_category: str | None,
        duration_ms: float,
    ) -> None:
        with self._lock:
            self.http_requests[endpoint] += 1
            if outcome != "success":
                key = f"{endpoint}/{failure_category or 'unknown'}"
                self.http_failures[key] += 1
            samples = self.http_latencies[endpoint]
            if len(samples) < self._MAX_SAMPLES:
                samples.append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            def _stats(samples: list[float]) -> dict[str, Any]:
                if not samples:
                    return {"count": 0}
                s = sorted(samples)
                n = len(s)
                return {
                    "count": n,
                    "min_ms": round(s[0], 2),
                    "max_ms": round(s[-1], 2),
                    "avg_ms": round(sum(s) / n, 2),
                    "p50_ms": round(s[n // 2], 2),
                    "p95_ms": round(s[min(int(n * 0.95), n - 1)], 2),
                }

            return {
                "tool_invocations_total": dict(self.tool_invocations),
                "tool_failures_total": dict(self.tool_failures),
                "mbta_http_requests_total": dict(self.http_requests),
                "mbta_http_failures_total": dict(self.http_failures),
                "tool_latency": {k: _stats(v) for k, v in self.tool_latencies.items()},
                "http_latency": {k: _stats(v) for k, v in self.http_latencies.items()},
            }


METRICS: _Metrics = _Metrics()


def record_tool_event(
    tool_name: str,
    outcome: str,
    failure_category: str | None,
    duration_ms: float,
) -> None:
    METRICS.record_tool(tool_name, outcome, failure_category, duration_ms)


def record_http_event(
    endpoint: str,
    outcome: str,
    failure_category: str | None,
    duration_ms: float,
) -> None:
    METRICS.record_http(endpoint, outcome, failure_category, duration_ms)


def get_metrics_snapshot() -> dict[str, Any]:
    return METRICS.snapshot()


def flush_metrics_to_log() -> None:
    log_event(logging.INFO, "session_metrics", metrics=METRICS.snapshot())


atexit.register(flush_metrics_to_log)