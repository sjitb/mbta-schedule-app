"""MBTA MCP Server — exposes MBTA transit data as tools for Claude (Layer 2).

Run directly for stdio transport (required by Claude Desktop / Claude Code):
    python src/mbta_mcp/mbta_mcp_server.py
"""

from __future__ import annotations

import logging
import re
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

from pathlib import Path

from mcp.server.fastmcp import FastMCP

try:
    from .mbta_logging import LOG_PATH, log_event, record_tool_event
    from .mbta_client import MBTAClient, MBTAError
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mbta_logging import LOG_PATH, log_event, record_tool_event
    from mbta_client import MBTAClient, MBTAError

mcp = FastMCP(
    "mbta",
    instructions=(
        "Real-time and scheduled MBTA transit data for commuter rail, subway, and trolley. "
        "For next real-time departures, use get_predictions. "
        "For future or date-specific trip planning (e.g. tomorrow, a specific date), "
        "use get_schedules, then use get_trip_schedule to verify arrival time at a destination stop. "
        "Provides alerts, predictions, schedules, route status, and stop lookups."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _attr(item: dict[str, Any]) -> dict[str, Any]:
    """Return the attributes sub-dict of an MBTA API resource object."""
    return item.get("attributes", {})


def _new_correlation_id() -> str:
    return uuid4().hex


def _normalize_required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"'{field_name}' must be a non-empty string.")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_direction_id(value: int | None) -> int | None:
    if value is None:
        return None
    if value not in (0, 1):
        raise ValueError("'direction_id' must be 0 or 1 when provided.")
    return value


_DATE_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$")
_TIME_HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not _DATE_RE.match(stripped):
        raise ValueError(
            f"'date' must be in YYYY-MM-DD format (e.g. '2026-04-02'), got '{stripped}'."
        )
    return stripped


def _normalize_time_hhmm(value: str | None, field_name: str = "time") -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if not _TIME_HHMM_RE.match(stripped):
        raise ValueError(
            f"'{field_name}' must be in HH:MM 24-hour format (e.g. '09:00'), got '{stripped}'."
        )
    return stripped


def _tool_started(tool_name: str, arguments: dict[str, Any]) -> tuple[str, float]:
    correlation_id = _new_correlation_id()
    log_event(
        logging.INFO,
        "tool_start",
        correlation_id=correlation_id,
        tool_name=tool_name,
        arguments=arguments,
    )
    return correlation_id, perf_counter()


def _tool_finished(
    tool_name: str,
    correlation_id: str,
    started: float,
    *,
    outcome: str,
    failure_category: str | None = None,
    result_count: int | None = None,
    error: str | None = None,
) -> None:
    duration_ms = round((perf_counter() - started) * 1000, 2)
    record_tool_event(tool_name, outcome, failure_category, duration_ms)
    log_event(
        logging.INFO if outcome == "success" else logging.WARNING,
        "tool_finish",
        correlation_id=correlation_id,
        tool_name=tool_name,
        duration_ms=duration_ms,
        outcome=outcome,
        failure_category=failure_category,
        result_count=result_count,
        error=error,
    )


def _invalid_input_response(tool_name: str, field_error: str) -> str:
    return f"Invalid input ({tool_name}, invalid_input): {field_error}"


def _api_error_response(action: str, exc: MBTAError) -> str:
    return f"API error fetching {action} ({exc.category}): {exc}"


# ---------------------------------------------------------------------------
# Tool: get_line_alerts
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_line_alerts(route_id: str) -> str:
    """Return active service alerts for an MBTA route.

    Args:
        route_id: Route identifier, e.g. "Red", "Orange", "CR-Franklin", "Green-B".
                  Consult the route ID reference in the README for a full list.
    """
    tool_name = "get_line_alerts"
    normalized_args = {"route_id": _normalize_optional_text(route_id)}
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_route_id = _normalize_required_text(route_id, "route_id")
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            alerts = await client.get_alerts(route_id=normalized_route_id)
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("alerts", exc)

    if not alerts:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return f"No active alerts for route '{route_id}'. Service appears normal."

    lines: list[str] = [f"{len(alerts)} active alert(s) for '{route_id}':\n"]
    for alert in alerts:
        a = _attr(alert)
        header = a.get("header") or "(no header)"
        severity = a.get("severity", "?")
        effect = a.get("effect", "UNKNOWN")
        description = a.get("description") or ""
        lines.append(f"[{effect} | severity {severity}] {header}")
        if description:
            # Truncate long descriptions to keep output readable
            truncated = description[:400] + ("…" if len(description) > 400 else "")
            lines.append(f"  {truncated}")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(alerts),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_predictions
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_predictions(
    stop_id: str,
    route_id: str | None = None,
    direction_id: int | None = None,
) -> str:
    """Return upcoming REAL-TIME departure predictions at an MBTA stop.

    Only returns departures happening right now or very soon — no date filtering
    is possible. For a specific date or future schedule (e.g. tomorrow morning),
    use get_schedules instead.

    Args:
        stop_id:      Stop identifier, e.g. "place-pktrm" for Park Street.
                      Use find_stop to look up an ID by name first.
        route_id:     Optional — restrict to one route, e.g. "Red".
        direction_id: Optional — 0 or 1. Use get_stops_for_route to learn
                      which number is inbound vs. outbound for a given route.
    """
    tool_name = "get_predictions"
    normalized_args = {
        "stop_id": _normalize_optional_text(stop_id),
        "route_id": _normalize_optional_text(route_id),
        "direction_id": direction_id,
    }
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_stop_id = _normalize_required_text(stop_id, "stop_id")
        normalized_route_id = _normalize_optional_text(route_id)
        normalized_direction_id = _normalize_direction_id(direction_id)
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            predictions = await client.get_predictions(
                normalized_stop_id,
                normalized_route_id,
                normalized_direction_id,
            )
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("predictions", exc)

    if not predictions:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return (
            f"No predictions found for stop '{stop_id}'. "
            "The stop may have no active service right now, or the stop ID may be wrong. "
            "Try find_stop to verify the ID."
        )

    lines: list[str] = [f"Next departures at stop '{stop_id}':\n"]
    for pred in predictions[:10]:
        a = _attr(pred)
        departure = a.get("departure_time") or a.get("arrival_time") or "unknown time"
        status = a.get("status") or "on time"
        direction = a.get("direction_id", "?")
        lines.append(f"  {departure}  dir={direction}  {status}")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(predictions),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_route_status
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_route_status(route_id: str) -> str:
    """Return a human-readable service status summary for an MBTA route.

    Args:
        route_id: Route identifier, e.g. "Red", "CR-Franklin", "Green-E".
    """
    tool_name = "get_route_status"
    normalized_args = {"route_id": _normalize_optional_text(route_id)}
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_route_id = _normalize_required_text(route_id, "route_id")
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            alerts = await client.get_alerts(route_id=normalized_route_id)
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("route status", exc)

    if not alerts:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return f"Route '{route_id}': No active alerts — service appears normal."

    effects = {_attr(a).get("effect", "UNKNOWN") for a in alerts}
    flags: list[str] = []
    if "SUSPENSION" in effects:
        flags.append("SUSPENSION in effect")
    if "DELAY" in effects:
        flags.append("delays reported")
    if "DETOUR" in effects:
        flags.append("detour in effect")
    if "STATION_CLOSURE" in effects:
        flags.append("station closure")

    flag_str = " | ".join(flags) if flags else "service disruption"
    lines: list[str] = [
        f"Route '{route_id}': {len(alerts)} active alert(s) — {flag_str}\n"
    ]
    for alert in alerts[:5]:
        header = _attr(alert).get("header") or ""
        if header:
            lines.append(f"  - {header}")
    if len(alerts) > 5:
        lines.append(f"  … and {len(alerts) - 5} more alert(s).")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(alerts),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: find_stop
# ---------------------------------------------------------------------------

@mcp.tool()
async def find_stop(name: str) -> str:
    """Search for MBTA stops by name across all subway and commuter rail routes.

    Args:
        name: Full or partial stop name, e.g. "Park Street", "South Station",
              "Needham Heights", "Alewife".
    """
    tool_name = "find_stop"
    normalized_args = {"name": _normalize_optional_text(name)}
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_name = _normalize_required_text(name, "name")
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            # route_type "0,1,2" covers light rail, heavy rail, and commuter rail
            stops = await client.get_stops(route_type="0,1,2", name_filter=normalized_name)
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("stops", exc)

    if not stops:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return f"No stops found matching '{name}'. Try a shorter or different search term."

    # Deduplicate: a parent station can appear once per route that serves it
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for stop in stops:
        sid = stop.get("id", "")
        if sid not in seen:
            seen.add(sid)
            unique.append(stop)

    lines: list[str] = [f"{len(unique)} stop(s) matching '{name}':\n"]
    for stop in unique[:20]:
        a = _attr(stop)
        stop_id = stop.get("id", "?")
        stop_name = a.get("name", "?")
        municipality = a.get("municipality") or ""
        lines.append(f"  {stop_name:<40} id={stop_id}  {municipality}")
    if len(unique) > 20:
        lines.append(f"  … and {len(unique) - 20} more. Use a more specific name.")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(unique),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_stops_for_route
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_stops_for_route(route_id: str, direction_id: int | None = None) -> str:
    """Return the ordered list of stops served by an MBTA route.

    Args:
        route_id:     Route identifier, e.g. "Orange", "CR-Needham", "Green-D".
        direction_id: Optional — 0 or 1 to see stops in one direction only.
    """
    tool_name = "get_stops_for_route"
    normalized_args = {
        "route_id": _normalize_optional_text(route_id),
        "direction_id": direction_id,
    }
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_route_id = _normalize_required_text(route_id, "route_id")
        normalized_direction_id = _normalize_direction_id(direction_id)
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            stops = await client.get_stops(
                route_id=normalized_route_id,
                direction_id=normalized_direction_id,
            )
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("stops for route", exc)

    if not stops:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return (
            f"No stops found for route '{route_id}'. "
            "Check that the route ID is correct (e.g. 'Orange', 'CR-Franklin')."
        )

    dir_label = f" (direction {direction_id})" if direction_id is not None else ""
    lines: list[str] = [
        f"Stops for '{route_id}'{dir_label} — {len(stops)} stop(s):\n"
    ]
    for i, stop in enumerate(stops, 1):
        a = _attr(stop)
        stop_id = stop.get("id", "?")
        stop_name = a.get("name", "?")
        lines.append(f"  {i:>3}. {stop_name:<40} (id={stop_id})")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(stops),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_schedules
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_schedules(
    stop_id: str,
    route_id: str | None = None,
    date: str | None = None,
    direction_id: int | None = None,
    max_time: str | None = None,
) -> str:
    """Return planned MBTA schedule entries at a stop for a given date.

    Use this tool (not get_predictions) when the user asks about a future date,
    tomorrow's trains, or a specific time window. Returns scheduled departure
    times and trip IDs. To verify arrival time at a destination stop, pass the
    trip_id to get_trip_schedule.

    Args:
        stop_id:      Stop identifier, e.g. "place-WNatick" for West Natick.
                      Use find_stop to look up an ID by name first.
        route_id:     Optional — restrict to one route, e.g. "CR-Worcester".
        date:         Optional — date in YYYY-MM-DD format. Defaults to today.
                      Use tomorrow's date for next-morning trip planning.
        direction_id: Optional — 0 (outbound) or 1 (inbound toward Boston).
        max_time:     Optional — latest departure time to include, as HH:MM
                      in 24-hour format, e.g. "09:00" to find trains before 9am.
    """
    tool_name = "get_schedules"
    normalized_args = {
        "stop_id": _normalize_optional_text(stop_id),
        "route_id": _normalize_optional_text(route_id),
        "date": _normalize_optional_text(date),
        "direction_id": direction_id,
        "max_time": _normalize_optional_text(max_time),
    }
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_stop_id = _normalize_required_text(stop_id, "stop_id")
        normalized_route_id = _normalize_optional_text(route_id)
        normalized_date = _normalize_date(date)
        normalized_direction_id = _normalize_direction_id(direction_id)
        normalized_max_time = _normalize_time_hhmm(max_time, "max_time")
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            schedules = await client.get_schedules(
                stop_id=normalized_stop_id,
                route_id=normalized_route_id,
                date=normalized_date,
                max_time=normalized_max_time,
                direction_id=normalized_direction_id,
            )
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("schedules", exc)

    if not schedules:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return (
            f"No scheduled trips found for stop '{stop_id}' "
            f"on {date or 'today'}. "
            "Check that the stop ID, route ID, and date are correct."
        )

    lines: list[str] = [
        f"{len(schedules)} schedule(s) at stop '{stop_id}' on {date or 'today'}:\n"
    ]
    for sched in schedules[:20]:
        a = _attr(sched)
        departure = a.get("departure_time") or a.get("arrival_time") or "unknown time"
        # Extract trip_id from relationships for use with get_trip_schedule
        trip_id = (
            sched.get("relationships", {})
            .get("trip", {})
            .get("data", {})
            .get("id", "?")
        )
        route_rel = (
            sched.get("relationships", {})
            .get("route", {})
            .get("data", {})
            .get("id", "?")
        )
        lines.append(
            f"  {departure}  route={route_rel}  trip_id={trip_id}"
        )
    if len(schedules) > 20:
        lines.append(f"  … and {len(schedules) - 20} more.")
    lines.append(
        "\nUse get_trip_schedule(trip_id) to see all stops and verify arrival time at destination."
    )
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(schedules),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: get_trip_schedule
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_trip_schedule(trip_id: str) -> str:
    """Return all stops and times for a specific trip.

    Use this after get_schedules to verify arrival time at a destination stop
    (e.g. confirm a train from West Natick arrives at South Station by 9am).

    Args:
        trip_id: Trip identifier obtained from a get_schedules result.
    """
    tool_name = "get_trip_schedule"
    normalized_args = {"trip_id": _normalize_optional_text(trip_id)}
    correlation_id, started = _tool_started(tool_name, normalized_args)

    try:
        normalized_trip_id = _normalize_required_text(trip_id, "trip_id")
    except ValueError as exc:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="failure",
            failure_category="invalid_input",
            error=str(exc),
        )
        return _invalid_input_response(tool_name, str(exc))

    async with MBTAClient(correlation_id=correlation_id) as client:
        try:
            result = await client.get_trip_schedule(trip_id=normalized_trip_id)
        except MBTAError as exc:
            _tool_finished(
                tool_name,
                correlation_id,
                started,
                outcome="failure",
                failure_category=exc.category,
                error=str(exc),
            )
            return _api_error_response("trip schedule", exc)

    stops_data: list[dict[str, Any]] = result.get("data", [])
    included: list[dict[str, Any]] = result.get("included", [])

    if not stops_data:
        _tool_finished(
            tool_name,
            correlation_id,
            started,
            outcome="empty_result",
            failure_category="empty_result",
            result_count=0,
        )
        return f"No stop data found for trip '{trip_id}'. Check that the trip ID is correct."

    # Build a lookup from stop ID → stop name using the included resources
    stop_names: dict[str, str] = {}
    for item in included:
        if item.get("type") == "stop":
            stop_id = item.get("id", "")
            name = item.get("attributes", {}).get("name", stop_id)
            stop_names[stop_id] = name

    lines: list[str] = [f"Stop sequence for trip '{trip_id}':\n"]
    for entry in stops_data:
        a = _attr(entry)
        arrival = a.get("arrival_time") or ""
        departure = a.get("departure_time") or ""
        seq = a.get("stop_sequence", "?")
        stop_rel_id = (
            entry.get("relationships", {})
            .get("stop", {})
            .get("data", {})
            .get("id", "?")
        )
        stop_name = stop_names.get(stop_rel_id, stop_rel_id)
        time_str = departure or arrival or "unknown"
        lines.append(f"  #{seq:>3}  {stop_name:<40}  {time_str}")
    _tool_finished(
        tool_name,
        correlation_id,
        started,
        outcome="success",
        result_count=len(stops_data),
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log_event(
        logging.INFO,
        "server_start",
        transport="stdio",
        log_path=str(LOG_PATH) if LOG_PATH is not None else None,
    )
    mcp.run(transport="stdio")
