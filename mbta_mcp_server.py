"""MBTA MCP Server — exposes MBTA transit data as tools for Claude (Layer 2).

Run directly for stdio transport (required by Claude Desktop / Claude Code):
    python mbta_mcp_server.py
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mbta_client import MBTAClient, MBTAError

mcp = FastMCP(
    "mbta",
    instructions=(
        "Real-time MBTA transit data for commuter rail, subway, and trolley. "
        "Provides alerts, predictions, route status, and stop lookups."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _attr(item: dict[str, Any]) -> dict[str, Any]:
    """Return the attributes sub-dict of an MBTA API resource object."""
    return item.get("attributes", {})


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
    async with MBTAClient() as client:
        try:
            alerts = await client.get_alerts(route_id=route_id)
        except MBTAError as exc:
            return f"API error fetching alerts: {exc}"

    if not alerts:
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
    """Return upcoming departure predictions at an MBTA stop.

    Args:
        stop_id:      Stop identifier, e.g. "place-pktrm" for Park Street.
                      Use find_stop to look up an ID by name first.
        route_id:     Optional — restrict to one route, e.g. "Red".
        direction_id: Optional — 0 or 1. Use get_stops_for_route to learn
                      which number is inbound vs. outbound for a given route.
    """
    async with MBTAClient() as client:
        try:
            predictions = await client.get_predictions(stop_id, route_id, direction_id)
        except MBTAError as exc:
            return f"API error fetching predictions: {exc}"

    if not predictions:
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
    async with MBTAClient() as client:
        try:
            alerts = await client.get_alerts(route_id=route_id)
        except MBTAError as exc:
            return f"API error fetching route status: {exc}"

    if not alerts:
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
    async with MBTAClient() as client:
        try:
            # route_type "0,1,2" covers light rail, heavy rail, and commuter rail
            stops = await client.get_stops(route_type="0,1,2", name_filter=name)
        except MBTAError as exc:
            return f"API error searching stops: {exc}"

    if not stops:
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
    async with MBTAClient() as client:
        try:
            stops = await client.get_stops(route_id=route_id, direction_id=direction_id)
        except MBTAError as exc:
            return f"API error fetching stops for route: {exc}"

    if not stops:
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
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
