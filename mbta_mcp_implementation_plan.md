# MBTA MCP Server — Implementation Plan

## Overview

A two-layer Python project that wraps the MBTA V3 REST API and exposes it as an MCP (Model Context Protocol) server, enabling Claude (via Claude Code or Claude Desktop) to answer natural-language questions about real-time commuter rail, subway, and trolley status.

---

## Prerequisites

### MBTA API Key
- Register at: https://api-v3.mbta.com/register (free, instant)
- Without a key: ~20 requests/min
- With a key: ~1000 requests/min
- Store as `MBTA_API_KEY` in a `.env` file at project root

### Python Dependencies
```
httpx
mcp[cli]
python-dotenv
```

---

## Project Structure

```
mbta-mcp/
├── mbta_client.py        # Layer 3 — raw MBTA V3 API wrapper
├── mbta_mcp_server.py    # Layer 2 — MCP server exposing tools to Claude
├── .env                  # MBTA_API_KEY=your_key_here
├── requirements.txt
└── README.md
```

---

## Architecture

```
┌─────────────────────────────────────┐
│  Layer 1: Claude / LLM Agent        │
│  (Claude Code or Claude Desktop)    │
└────────────────┬────────────────────┘
                 │ MCP protocol (stdio)
┌────────────────▼────────────────────┐
│  Layer 2: MCP Server                │
│  mbta_mcp_server.py                 │
│                                     │
│  Tools:                             │
│  - get_line_alerts(route_id)        │
│  - get_predictions(stop_id, ...)    │
│  - get_route_status(route_id)       │
│  - find_stop(name)                  │
│  - get_stops_for_route(route_id)    │
└────────────────┬────────────────────┘
                 │ Python function calls
┌────────────────▼────────────────────┐
│  Layer 3: MBTA API Client           │
│  mbta_client.py                     │
│  Wraps MBTA V3 REST API             │
│  (alerts, predictions, stops,       │
│   routes, schedules)                │
└────────────────┬────────────────────┘
                 │ HTTPS + API key
┌────────────────▼────────────────────┐
│  MBTA V3 REST API                   │
│  https://api-v3.mbta.com            │
└─────────────────────────────────────┘
```

---

## Layer 3: `mbta_client.py`

A thin, async HTTP client wrapping the MBTA V3 API.

### Responsibilities
- Manage base URL, API key header, and request lifecycle
- Provide one method per MBTA API resource needed
- Return raw parsed JSON (dicts/lists); no business logic here

### Key MBTA V3 Endpoints to Wrap

| Method | Endpoint | Purpose |
|---|---|---|
| `get_alerts(route_id?)` | `GET /alerts` | Service alerts, delays, suspensions |
| `get_predictions(stop_id, route_id?)` | `GET /predictions` | Next departures at a stop |
| `get_routes(type?)` | `GET /routes` | List all routes (filter by type for rail/subway) |
| `get_stops(route_id?)` | `GET /stops` | Stops on a route or by name |
| `get_schedules(stop_id, route_id?)` | `GET /schedules` | Scheduled trips |

### MBTA Route Types (filter param)
- `0` — Light rail (Green Line, Mattapan trolley)
- `1` — Heavy rail (Red, Orange, Blue lines)
- `2` — Commuter rail
- `3` — Bus (out of scope)

### Notes
- All requests: `GET https://api-v3.mbta.com/{resource}?api_key={key}&filter[...]=...`
- Use `httpx.AsyncClient` for async support
- Parse `response.json()["data"]` for list endpoints, handle `"included"` for related resources

---

## Layer 2: `mbta_mcp_server.py`

An MCP server built with the `mcp` Python SDK, exposing MBTA data as callable tools.

### MCP Tools to Implement

#### `get_line_alerts`
```
Input:  route_id (str) — e.g. "Red", "CR-Franklin", "Green-B"
Output: List of active alerts with header, description, severity, and affected stops
```

#### `get_predictions`
```
Input:  stop_id (str), route_id (str, optional), direction_id (int, optional: 0 or 1)
Output: Next N departures — route, destination, scheduled time, predicted time, status
```

#### `get_route_status`
```
Input:  route_id (str)
Output: Human-readable summary — number of active alerts, any suspensions, typical headway
```

#### `find_stop`
```
Input:  name (str) — e.g. "Park Street", "South Station", "Needham Heights"
Output: Matching stops with stop_id, route(s) served, and municipality
```

#### `get_stops_for_route`
```
Input:  route_id (str), direction_id (int, optional)
Output: Ordered list of stops — stop_id, name, and sequence number
```

### Transport
- Use `stdio` transport (required for Claude Desktop and Claude Code)
- Server entry point: `mcp.run(transport="stdio")`

---

## Claude Desktop Integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "mbta": {
      "command": "python",
      "args": ["/absolute/path/to/mbta-mcp/mbta_mcp_server.py"],
      "env": {
        "MBTA_API_KEY": "your_key_here"
      }
    }
  }
}
```

On Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Restart Claude Desktop after saving. The MBTA tools will appear in the tools panel.

---

## Claude Code Integration

In your project directory, create `.mcp.json`:

```json
{
  "mcpServers": {
    "mbta": {
      "command": "python",
      "args": ["mbta_mcp_server.py"],
      "env": {
        "MBTA_API_KEY": "your_key_here"
      }
    }
  }
}
```

Or register globally via the Claude Code CLI:
```bash
claude mcp add mbta -- python /absolute/path/to/mbta_mcp_server.py
```

---

## Example Agent Interactions

Once connected, Claude can answer queries like:

| Query | Tools called |
|---|---|
| "Is the Franklin Line running on time?" | `get_line_alerts("CR-Franklin")` |
| "Next train from Park Street to Kenmore?" | `find_stop("Park Street")` → `get_predictions(stop_id, "Green-D")` |
| "Any delays on the Red Line?" | `get_route_status("Red")` |
| "What stops does the Orange Line serve?" | `get_stops_for_route("Orange")` |
| "Status between South Station and Needham?" | `find_stop("South Station")` → `get_predictions(...)` + `get_line_alerts("CR-Needham")` |

---

## MBTA Route ID Reference

### Subway
| Route | ID |
|---|---|
| Red Line | `Red` |
| Orange Line | `Orange` |
| Blue Line | `Blue` |
| Green Line (trunk) | `Green` |
| Green-B | `Green-B` |
| Green-C | `Green-C` |
| Green-D | `Green-D` |
| Green-E | `Green-E` |
| Mattapan Trolley | `Mattapan` |

### Commuter Rail (selected)
| Route | ID |
|---|---|
| Franklin Line | `CR-Franklin` |
| Providence/Stoughton | `CR-Providence` |
| Needham Line | `CR-Needham` |
| Fairmount Line | `CR-Fairmount` |
| Fitchburg Line | `CR-Fitchburg` |
| Newburyport/Rockport | `CR-Newburyport` |
| Lowell Line | `CR-Lowell` |
| Haverhill Line | `CR-Haverhill` |
| Worcester/Framingham | `CR-Worcester` |
| Kingston/Plymouth | `CR-Kingston` |
| Middleborough/Lakeville | `CR-Middleborough` |
| Greenbush Line | `CR-Greenbush` |

Full route list: `GET https://api-v3.mbta.com/routes?filter[type]=2`

---

## Implementation Order

1. Set up project, `requirements.txt`, `.env`
2. Build and test `mbta_client.py` in isolation (verify API key, test each endpoint)
3. Build `mbta_mcp_server.py` — wire client methods to MCP tools
4. Register with Claude Desktop or Claude Code via config
5. Test end-to-end with natural-language queries
6. Iterate: add `get_stops_between(from_stop, to_stop, route_id)` as a higher-level tool

---

## Resources

- MBTA V3 API docs: https://api-v3.mbta.com/docs/swagger/index.html
- MBTA API registration: https://api-v3.mbta.com/register
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- MCP spec: https://modelcontextprotocol.io
