"""MBTA V3 REST API client — thin async wrapper (Layer 3)."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

import httpx

_BASE_URL = "https://api-v3.mbta.com"
_CONFIG_PATH = Path(__file__).parent / "config.ini"


def _load_api_key() -> str:
    """Read the MBTA API key from config.ini."""
    config = configparser.ConfigParser()
    config.read(_CONFIG_PATH)
    try:
        key = config["mbta"]["api_key"].strip()
    except KeyError as exc:
        raise RuntimeError(
            "MBTA API key not found. Ensure [mbta] api_key is set in config.ini."
        ) from exc
    if not key or key == "YOUR_MBTA_API_KEY":
        raise RuntimeError(
            "Replace the api_key placeholder in config.ini with your real MBTA API key.\n"
            "Register free at https://api-v3.mbta.com/register"
        )
    return key


class MBTAError(Exception):
    """Raised when the MBTA API returns a non-2xx response."""


class MBTAClient:
    """Async HTTP client for the MBTA V3 REST API.

    Use as an async context manager::

        async with MBTAClient() as client:
            alerts = await client.get_alerts(route_id="Red")

    An explicit *api_key* overrides the value read from config.ini.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or _load_api_key()
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> MBTAClient:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-api-key": self._api_key},
            timeout=10.0,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("MBTAClient must be used as an async context manager.")
        # Drop None values so they don't get sent as the string "None"
        cleaned = {k: v for k, v in (params or {}).items() if v is not None}
        response = await self._http.get(path, params=cleaned)
        if response.status_code >= 400:
            raise MBTAError(
                f"MBTA API returned HTTP {response.status_code} for {path}: "
                f"{response.text[:300]}"
            )
        return response.json()  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Public resource methods — return raw "data" lists from the API
    # ------------------------------------------------------------------

    async def get_alerts(self, route_id: str | None = None) -> list[dict[str, Any]]:
        """Return active service alerts, optionally filtered to *route_id*."""
        params: dict[str, Any] = {"filter[activity]": "BOARD,EXIT,RIDE"}
        if route_id:
            params["filter[route]"] = route_id
        data = await self._get("/alerts", params)
        return data.get("data", [])

    async def get_predictions(
        self,
        stop_id: str,
        route_id: str | None = None,
        direction_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return real-time departure predictions at *stop_id*."""
        params: dict[str, Any] = {
            "filter[stop]": stop_id,
            "filter[route]": route_id,
            "filter[direction_id]": direction_id,
            "include": "route,trip,stop",
            "sort": "departure_time",
        }
        data = await self._get("/predictions", params)
        return data.get("data", [])

    async def get_routes(self, route_type: int | None = None) -> list[dict[str, Any]]:
        """Return routes filtered by *route_type* (0=LR, 1=HR, 2=CR) or all."""
        params: dict[str, Any] = {"filter[type]": route_type}
        data = await self._get("/routes", params)
        return data.get("data", [])

    async def get_stops(
        self,
        route_id: str | None = None,
        route_type: str | None = None,
        direction_id: int | None = None,
        name_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return stops with optional filters.

        Args:
            route_id:     Limit to stops on this route (e.g. "Orange").
            route_type:   Comma-separated type codes (e.g. "0,1,2").
            direction_id: 0 or 1 — only stops served in that direction.
            name_filter:  Case-insensitive substring applied client-side.
        """
        params: dict[str, Any] = {
            "filter[route]": route_id,
            "filter[route_type]": route_type,
            "filter[direction_id]": direction_id,
        }
        data = await self._get("/stops", params)
        stops: list[dict[str, Any]] = data.get("data", [])
        if name_filter:
            query = name_filter.lower()
            stops = [
                s
                for s in stops
                if query in s.get("attributes", {}).get("name", "").lower()
            ]
        return stops

    async def get_schedules(
        self,
        stop_id: str,
        route_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return scheduled trips at *stop_id*."""
        params: dict[str, Any] = {
            "filter[stop]": stop_id,
            "filter[route]": route_id,
        }
        data = await self._get("/schedules", params)
        return data.get("data", [])
