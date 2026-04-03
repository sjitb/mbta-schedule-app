"""Stream MBTA MCP session logs to the console for local debugging.

Usage:
    python tail_logs.py                                      # follow latest session file
    python tail_logs.py --no-follow                          # print existing lines and exit
    python tail_logs.py --filter tool_start tool_finish      # only matching event types
    python tail_logs.py --raw                                # raw JSON, one line per event
    python tail_logs.py --list                               # list all available session files
    python tail_logs.py --log logs/2026-04-02/mbta-mcp-....jsonl  # explicit file
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _find_all_logs() -> list[Path]:
    return sorted(_LOG_DIR.rglob("*.jsonl"))


def _find_latest_log() -> Path | None:
    files = _find_all_logs()
    return files[-1] if files else None


def _event_label(event: dict) -> str:  # type: ignore[type-arg]
    event_type: str = event.get("event_type", event.get("message", ""))
    ts: str = event.get("ts", "")[:19].replace("T", " ")
    level: str = event.get("level", "INFO")[:4]
    corr: str = (event.get("correlation_id") or "--------")[:8]
    outcome: str = event.get("outcome", "")
    category: str = event.get("failure_category") or ""
    cat_str = f" [{category}]" if category else ""

    if event_type == "tool_start":
        tool = event.get("tool_name", "")
        args = event.get("arguments", {})
        args_str = ", ".join(
            f"{k}={v!r}" for k, v in (args or {}).items() if v is not None
        )
        return f"{ts} [{level}] [{corr}] >> {tool}({args_str})"

    if event_type == "tool_finish":
        tool = event.get("tool_name", "")
        ms = event.get("duration_ms", "?")
        count = event.get("result_count")
        count_str = f" results={count}" if count is not None else ""
        return f"{ts} [{level}] [{corr}] << {tool} {outcome}{cat_str}{count_str} ({ms}ms)"

    if event_type == "http_request_start":
        return f"{ts} [{level}] [{corr}]    GET {event.get('endpoint', '')}"

    if event_type == "http_request_finish":
        status = event.get("status_code", "")
        ms = event.get("duration_ms", "?")
        return (
            f"{ts} [{level}] [{corr}]    GET {event.get('endpoint', '')} "
            f"{status}{cat_str} ({ms}ms)"
        )

    if event_type == "server_start":
        return f"{ts} [{level}] server started  log={event.get('log_path', '')}"

    if event_type == "session_metrics":
        metrics = event.get("metrics", {})
        invocations = metrics.get("tool_invocations_total", {})
        failures = metrics.get("tool_failures_total", {})
        http_req = metrics.get("mbta_http_requests_total", {})
        lines = [f"{ts} [{level}] session_metrics"]
        for tool, count in sorted(invocations.items()):
            latency = metrics.get("tool_latency", {}).get(tool, {})
            avg = latency.get("avg_ms", "?")
            p95 = latency.get("p95_ms", "?")
            lines.append(
                f"    tool  {tool:<30} invocations={count}  avg={avg}ms  p95={p95}ms"
            )
        for ep, count in sorted(http_req.items()):
            latency = metrics.get("http_latency", {}).get(ep, {})
            avg = latency.get("avg_ms", "?")
            lines.append(f"    http  {ep:<30} requests={count}  avg={avg}ms")
        for key, count in sorted(failures.items()):
            lines.append(f"    fail  {key}  count={count}")
        return "\n".join(lines)

    # Generic fallback
    return f"{ts} [{level}] {event_type}"


def _tail(log_path: Path, allowed: set[str] | None, raw: bool, follow: bool) -> None:
    print(f"# {log_path}", file=sys.stderr)
    with log_path.open("r", encoding="utf-8") as fh:
        while True:
            line = fh.readline()
            if not line:
                if not follow:
                    break
                time.sleep(0.3)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(line)
                continue
            if allowed and event.get("event_type") not in allowed:
                continue
            print(json.dumps(event) if raw else _event_label(event))
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream MBTA MCP session logs to the console."
    )
    parser.add_argument(
        "--filter",
        nargs="+",
        metavar="EVENT_TYPE",
        help=(
            "Only show these event types. "
            "Common values: tool_start tool_finish http_request_finish "
            "server_start session_metrics"
        ),
    )
    parser.add_argument(
        "--raw", action="store_true", help="Print raw JSON, one line per event."
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Print existing lines and exit instead of following new output.",
    )
    parser.add_argument(
        "--log",
        metavar="FILE",
        help="Explicit log file path. Defaults to the most recent session file.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available session log files and exit.",
    )
    args = parser.parse_args()

    if args.list:
        files = _find_all_logs()
        if not files:
            print("No session log files found under logs/.", file=sys.stderr)
            sys.exit(1)
        for f in files:
            print(f)
        return

    if args.log:
        log_path = Path(args.log)
        if not log_path.exists():
            print(f"File not found: {log_path}", file=sys.stderr)
            sys.exit(1)
    else:
        log_path = _find_latest_log()
        if log_path is None:
            print(
                "No session log files found under logs/. Run the server first.",
                file=sys.stderr,
            )
            sys.exit(1)

    allowed = set(args.filter) if args.filter else None
    _tail(log_path, allowed, args.raw, follow=not args.no_follow)


if __name__ == "__main__":
    main()
