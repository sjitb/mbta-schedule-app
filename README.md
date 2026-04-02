# MBTA Schedule MCP Server

Lightweight Python MCP server for real-time MBTA transit data.

This project wraps the MBTA V3 API in a small async client, then exposes transit tools to Claude via MCP so you can ask natural-language questions like:

- "Any delays on the Red Line right now?"
- "What stops are on the Orange Line?"
- "When is the next train at Park Street?"

## What This Project Includes

- `mbta_client.py`: async MBTA REST client (alerts, predictions, routes, stops, schedules)
- `mbta_mcp_server.py`: MCP server exposing MBTA tools
- `.mcp.json`: Claude Code MCP configuration for this workspace
- `config.ini.example`: API key template
- `config.ini`: local API key file (ignored by git)

## MCP Tools Exposed To Claude

1. `get_line_alerts(route_id)`
2. `get_predictions(stop_id, route_id=None, direction_id=None)`
3. `get_route_status(route_id)`
4. `find_stop(name)`
5. `get_stops_for_route(route_id, direction_id=None)`

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

- `mbta_client.py` loads the key from `config.ini` next to the script.
- `config.ini` is in `.gitignore` and should never be committed.

## Claude Code Setup

This repo already includes a working `.mcp.json`:

```json
{
	"mcpServers": {
		"mbta": {
			"command": "python",
			"args": ["mbta_mcp_server.py"],
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
			"args": ["C:\\path\\to\\mbta-schedule-app\\mbta_mcp_server.py"]
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

- `CR-Franklin`, `CR-Needham`, `CR-Providence`, `CR-Worcester`

## Local Run / Smoke Test

Run the server directly:

```bash
python mbta_mcp_server.py
```

Quick import test:

```bash
python -c "from mbta_mcp_server import mcp; print('ok')"
```

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
