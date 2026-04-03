"""MBTA V3 REST API client — thin async wrapper (Layer 3)."""

from __future__ import annotations

import configparser
import logging
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

import httpx

try:
    from .mbta_logging import log_event, record_http_event
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mbta_logging import log_event, record_http_event

_BASE_URL = "https://api-v3.mbta.com"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.ini"


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

    def __init__(
        self,
        message: str,
        *,
        category: str,
        status_code: int | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.path = path


class MBTAClient:
    """Async HTTP client for the MBTA V3 REST API.

    Use as an async context manager::

        async with MBTAClient() as client:
            alerts = await client.get_alerts(route_id="Red")

    An explicit *api_key* overrides the value read from config.ini.
    """

    def __init__(
        self,
        api_key: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._api_key = api_key or _load_api_key()
        self._correlation_id = correlation_id
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> MBTAClient:
        timeout = 10.0
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"x-api-key": self._api_key},
            timeout=timeout,
        )
        log_event(
            logging.INFO,
            "http_client_open",
            correlation_id=self._correlation_id,
            base_url=_BASE_URL,
            timeout_seconds=timeout,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
            log_event(
                logging.INFO,
                "http_client_close",
                correlation_id=self._correlation_id,
                base_url=_BASE_URL,
            )

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("MBTAClient must be used as an async context manager.")
        # Drop None values so they don't get sent as the string "None"
        cleaned = {k: v for k, v in (params or {}).items() if v is not None}
        started = perf_counter()
        log_event(
            logging.INFO,
            "http_request_start",
            correlation_id=self._correlation_id,
            endpoint=path,
            params=cleaned,
            cache_hit=False,
        )
        try:
            response = await self._http.get(path, params=cleaned)
        except httpx.TimeoutException as exc:
            duration_ms = round((perf_counter() - started) * 1000, 2)
            log_event(
                logging.ERROR,
                "http_request_finish",
                correlation_id=self._correlation_id,
                endpoint=path,
                duration_ms=duration_ms,
                outcome="failure",
                failure_category="timeout",
                cache_hit=False,
            )
            record_http_event(path, "failure", "timeout", duration_ms)
            raise MBTAError(
                f"MBTA request timed out for {path}.",
                category="timeout",
                path=path,
            ) from exc
        except httpx.RequestError as exc:
            duration_ms = round((perf_counter() - started) * 1000, 2)
            log_event(
                logging.ERROR,
                "http_request_finish",
                correlation_id=self._correlation_id,
                endpoint=path,
                duration_ms=duration_ms,
                outcome="failure",
                failure_category="network_error",
                cache_hit=False,
                error=str(exc),
            )
            record_http_event(path, "failure", "network_error", duration_ms)
            raise MBTAError(
                f"Network error reaching the MBTA API for {path}: {exc}",
                category="network_error",
                path=path,
            ) from exc

        duration_ms = round((perf_counter() - started) * 1000, 2)
        if response.status_code >= 400:
            if response.status_code == 429:
                category = "rate_limit"
            elif 400 <= response.status_code < 500:
                category = "upstream_4xx"
            else:
                category = "upstream_5xx"

            log_event(
                logging.ERROR,
                "http_request_finish",
                correlation_id=self._correlation_id,
                endpoint=path,
                duration_ms=duration_ms,
                status_code=response.status_code,
                outcome="failure",
                failure_category=category,
                cache_hit=False,
            )
            record_http_event(path, "failure", category, duration_ms)
            raise MBTAError(
                f"MBTA API returned HTTP {response.status_code} for {path}: "
                f"{response.text[:300]}",
                category=category,
                status_code=response.status_code,
                path=path,
            )

        data = response.json()  # type: ignore[assignment]
        result_count = len(data.get("data", [])) if isinstance(data, dict) else None
        log_event(
            logging.INFO,
            "http_request_finish",
            correlation_id=self._correlation_id,
            endpoint=path,
            duration_ms=duration_ms,
            status_code=response.status_code,
            outcome="success",
            failure_category=None,
            cache_hit=False,
            result_count=result_count,
        )
        record_http_event(path, "success", None, duration_ms)
        return data  # type: ignore[return-value]

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
        date: str | None = None,
        min_time: str | None = None,
        max_time: str | None = None,
        direction_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return scheduled trips at *stop_id*.

        Args:
            stop_id:      Stop identifier, e.g. "place-WNatick".
            route_id:     Optional — restrict to one route, e.g. "CR-Worcester".
            date:         Optional — date in YYYY-MM-DD format. Defaults to today
                          when omitted. Use tomorrow's date for next-day planning.
            min_time:     Optional — earliest departure time as HH:MM (24-hour).
            max_time:     Optional — latest departure time as HH:MM (24-hour).
            direction_id: Optional — 0 (outbound) or 1 (inbound).
        """
        params: dict[str, Any] = {
            "filter[stop]": stop_id,
            "filter[route]": route_id,
            "filter[date]": date,
            "filter[min_time]": min_time,
            "filter[max_time]": max_time,
            "filter[direction_id]": direction_id,
            "include": "route,trip",
            "sort": "departure_time",
        }
        data = await self._get("/schedules", params)
        return data.get("data", [])

    async def get_trip_schedule(
        self,
        trip_id: str,
    ) -> dict[str, Any]:
        """Return all stops and times for a specific trip.

        Args:
            trip_id: Trip identifier obtained from a schedule or prediction result.

        Returns a dict with:
            ``data`` — list of schedule stop entries sorted by stop_sequence.
            ``included`` — list of related stop objects keyed by ID.
        """
        params: dict[str, Any] = {
            "filter[trip]": trip_id,
            "include": "stop",
            "sort": "stop_sequence",
        }
        return await self._get("/schedules", params)
