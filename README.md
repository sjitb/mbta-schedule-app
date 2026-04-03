# MBTA Schedule MCP Server

Lightweight Python MCP server for real-time MBTA transit data.

This project wraps the MBTA V3 API in a small async client, then exposes transit tools to Claude via MCP so you can ask natural-language questions like:

- "Any delays on the Red Line right now?"
- "What stops are on the Orange Line?"
- "When is the next train at Park Street?"
- "If I need to reach South Station by 9am tomorrow, which train do I take from West Natick?"

## What This Project Includes

- `src/mbta_mcp/mbta_client.py`: async MBTA REST client (alerts, predictions, routes, stops, schedules)
- `src/mbta_mcp/mbta_mcp_server.py`: MCP server exposing MBTA tools
- `src/mbta_mcp/mbta_logging.py`: structured event logging + in-memory session metrics
- `scripts/tail_logs.py`: local log tail helper for session debugging
- `.mcp.json`: Claude Code MCP configuration for this workspace
- `config.ini.example`: API key template
- `config.ini`: local API key file (ignored by git)

## MCP Tools Exposed To Claude

| Tool | Purpose |
|---|---|
| `get_line_alerts(route_id)` | Active service alerts for a route |
| `get_predictions(stop_id, route_id, direction_id)` | **Real-time** next departures at a stop (current moment only) |
| `get_route_status(route_id)` | Human-readable disruption summary for a route |
| `find_stop(name)` | Search stops by full or partial name to get a stop ID |
| `get_stops_for_route(route_id, direction_id)` | Ordered stop list for a route |
| `get_schedules(stop_id, route_id, date, direction_id, max_time)` | **Planned schedule** at a stop for a specific date and time window |
| `get_trip_schedule(trip_id)` | All stops and times for a specific trip (use to confirm arrival time at destination) |

> **Real-time vs. scheduled:** Use `get_predictions` for "next train right now". Use `get_schedules` → `get_trip_schedule` for any future date or arrival-time question.

## Requirements

- Python 3.10+
- MBTA API key: https://api-v3.mbta.com/register

Install dependencies:

```bash
pip install -r requirements.txt
```

## Configure Your API Key

Create `config.ini` in the project root:

```ini
[mbta]
api_key = YOUR_MBTA_API_KEY
```

Notes:

- Runtime modules load `config.ini` from the project root.
- `config.ini` is in `.gitignore` and should never be committed.

Optional logging settings for local Phase 1 instrumentation:

```ini
[logging]
enabled = true
level = INFO
directory = logs
file_strategy = session
session_prefix = mbta-mcp
mirror_to_stderr = false
```

`logs/` is created automatically on first run. In `session` mode, each server start gets a timestamped `.jsonl` file under a date folder.

## Claude Code Setup

This repo already includes a working `.mcp.json`:

```json
{
	"mcpServers": {
		"mbta": {
			"command": "python",
			"args": ["src/mbta_mcp/mbta_mcp_server.py"],
			"cwd": "${workspaceFolder}"
		}
	}
}
```

From the project folder, start Claude Code. It should detect `.mcp.json` and register the `mbta` server automatically.

## Claude Desktop Setup (Optional)

If you want the same tools in Claude Desktop, add this server to your desktop config file:

- Windows Store install path often resolves under roaming cache, for example:
	- `%APPDATA%\\Claude\\claude_desktop_config.json`
	- or `AppData\\Local\\Packages\\Claude_...\\LocalCache\\Roaming\\Claude\\claude_desktop_config.json`

```json
{
	"mcpServers": {
		"mbta": {
			"command": "python",
			"args": ["C:\\path\\to\\mbta-schedule-app\\src\\mbta_mcp\\mbta_mcp_server.py"]
		}
	}
}
```

Then fully quit and relaunch Claude Desktop.

## How To Interact With Claude Code After MCP Is Configured

Use normal language; Claude decides when to call tools.

### Basic queries

- "Check route status for Red"
- "Show line alerts for CR-Franklin"
- "Find stops matching South Station"
- "List stops for Orange"
- "Show tomorrow's inbound schedule from West Natick on the Worcester line"

### Multi-step query patterns

Example 1: next train from a named station

1. You ask: "When is the next Red Line train from Park Street?"
2. Claude typically calls: `find_stop("Park Street")`
3. Claude then calls: `get_predictions(stop_id="place-pktrm", route_id="Red")`
4. Claude returns a human summary of upcoming departures.

Example 2: commuter rail status + departures

1. You ask: "Is Needham service delayed and what are the next departures?"
2. Claude calls: `get_line_alerts("CR-Needham")`
3. Claude may call: `find_stop("Needham Heights")` then `get_predictions(...)`
4. Claude combines alert context with prediction times.

Example 3: future trip planning with an arrival deadline

1. You ask: "If I need to reach South Station by 9am tomorrow, which train do I take from West Natick on the Worcester or Framingham line?"
2. Claude calls: `find_stop("West Natick")` → gets stop ID `place-WNatick`
3. Claude calls: `find_stop("South Station")` → gets stop ID `place-sstat`
4. Claude calls: `get_schedules(stop_id="place-WNatick", route_id="CR-Worcester", date="2026-04-03", direction_id=1, max_time="09:00")`
5. For each candidate trip, Claude calls: `get_trip_schedule(trip_id=...)` to confirm the arrival time at South Station
6. Claude reports the latest departure from West Natick that gets you to South Station by 9am.

### Recommended prompt style

- Include route ID when possible: `Red`, `Orange`, `CR-Franklin`, `Green-D`
- Include stop name if needed: "at South Station"
- Ask for formatting explicitly when useful: "show top 5 only"

## Useful MBTA Route IDs

Subway:

- `Red`, `Orange`, `Blue`
- `Green`, `Green-B`, `Green-C`, `Green-D`, `Green-E`
- `Mattapan`

Commuter rail examples:

- `CR-Franklin`, `CR-Needham`, `CR-Providence`
- `CR-Worcester` (also serves the Framingham line stops — West Natick, Framingham, etc.)

## Local Run / Smoke Test

Run the server directly:

```bash
python src/mbta_mcp/mbta_mcp_server.py
```

Quick import test (file-path mode):

```bash
python -c "import pathlib, sys; sys.path.insert(0, str(pathlib.Path('src/mbta_mcp').resolve())); from mbta_mcp_server import mcp; print('ok')"
```

Inspect latest session logs locally:

```bash
python scripts/tail_logs.py --no-follow --filter tool_start tool_finish session_metrics
```

When Phase 1 instrumentation is enabled, local structured logs are written under `logs/YYYY-MM-DD/` as JSON Lines. This avoids writing telemetry to stdout, which would interfere with MCP stdio transport.

## Troubleshooting

- `ModuleNotFoundError: mcp`
	- Reinstall deps: `pip install -r requirements.txt`
- "MBTA API key not found"
	- Ensure `config.ini` exists and includes `[mbta]` + `api_key`
- MCP tools do not appear in Claude
	- Confirm `.mcp.json` is in project root
	- Restart Claude Code / Claude Desktop
	- Validate JSON syntax in config files

## Security Notes

- Never commit `config.ini`
- If a key is exposed, regenerate it at MBTA and replace locally

## License

Use and adapt as needed for personal or internal transit tooling.
