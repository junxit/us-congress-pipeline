"""HTTP client for govinfo bulk data and the govinfo API.

Two traps this module exists to absorb:

* Bulk listing endpoints return **HTTP 406** unless ``Accept: application/json``
  is sent. The plain URL works in a browser and fails from a script.
* The API is rate limited to 36,000 requests/hour per key. Exceeding it returns
  429s that cost more time than pacing would have.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

import httpx

from . import config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_USER_AGENT = "uscongress-pipeline/0.1 (+https://github.com/junxit/us-congress-pipeline)"


class RateLimiter:
    """Spaces requests to a fixed maximum rate.

    Args:
        per_second: Maximum sustained requests per second.
    """

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        """Block until the caller may issue its request."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_at = max(now, self._next_at) + self._interval


@dataclass(frozen=True)
class BulkFile:
    """One entry in a govinfo bulk data directory listing.

    Attributes:
        name: File or folder name, e.g. ``COMPS-8768.xml``.
        url: Absolute download URL.
        size: Size in bytes, or 0 for folders.
        is_folder: Whether this entry is a directory.
    """

    name: str
    url: str
    size: int
    is_folder: bool


class GovInfoClient:
    """Rate-limited, retrying async client for govinfo.

    Use as an async context manager::

        async with GovInfoClient() as client:
            files = await client.list_bulkdata("COMPS")

    Args:
        api_key: govinfo API key. Read from the environment when omitted.
        max_concurrency: Maximum simultaneous in-flight requests.
        max_attempts: Attempts per request before giving up.
    """

    def __init__(
        self,
        api_key: str | None = None,
        max_concurrency: int = 12,
        max_attempts: int = 5,
    ) -> None:
        self._api_key = api_key or config.govinfo_api_key()
        self._limiter = RateLimiter(config.GOVINFO_RATE_PER_SEC)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=30.0),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            limits=httpx.Limits(max_connections=max_concurrency),
        )

    async def __aenter__(self) -> GovInfoClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.aclose()

    async def _request(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        """Issue a GET with pacing, retries and exponential backoff.

        Args:
            url: Absolute URL to fetch.
            headers: Extra request headers.

        Returns:
            The successful response.

        Raises:
            httpx.HTTPStatusError: If every attempt failed.
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                # Full jitter: spreads retries so a burst of 429s does not
                # resynchronize into another burst.
                await asyncio.sleep(random.uniform(0, min(30.0, 2.0**attempt)))
            async with self._semaphore:
                await self._limiter.acquire()
                try:
                    response = await self._client.get(url, headers=headers)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    continue
            if response.status_code in _RETRY_STATUS:
                last_exc = httpx.HTTPStatusError(
                    f"{response.status_code} for {url}",
                    request=response.request,
                    response=response,
                )
                continue
            response.raise_for_status()
            return response
        assert last_exc is not None
        raise last_exc

    async def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """Fetch a URL and return its raw body.

        Args:
            url: Absolute URL.
            headers: Extra request headers. Needed more often than it looks:
                bulk data serves ``STATUTE/107``, ``108`` and ``109`` as a
                67,225-byte "Govinfo Bulkdata Service Error" page at **HTTP 200**
                under httpx's default ``Accept: */*``, and returns all three in
                full -- 13.7, 35.1 and 19.1 MB -- under ``Accept:
                application/xml``. That is the same trap as the 406 on listings,
                pointed the other way, and it is silent rather than fatal.

        Returns:
            The response body.
        """
        response = await self._request(url, headers=headers)
        return response.content

    async def list_bulkdata(self, path: str) -> list[BulkFile]:
        """List one level of a govinfo bulk data directory.

        Args:
            path: Collection path below ``/bulkdata``, e.g. ``COMPS`` or
                ``BILLSTATUS/119/hr``.

        Returns:
            The entries in that directory.
        """
        url = f"{config.GOVINFO_BULKDATA}/json/{path.strip('/')}"
        # Without this header govinfo answers 406.
        response = await self._request(url, headers={"Accept": "application/json"})
        payload: dict[str, Any] = response.json()
        return [
            BulkFile(
                name=entry["name"],
                url=entry["link"],
                size=int(entry.get("size") or 0),
                is_folder=bool(entry.get("folder")),
            )
            for entry in payload.get("files", [])
        ]

    async def api_json(self, path: str, **params: str | int) -> Any:
        """Call the govinfo REST API.

        Args:
            path: Path below the API root, e.g. ``collections``.
            **params: Query parameters. The API key is added automatically.

        Returns:
            The decoded JSON body.
        """
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{config.GOVINFO_API}/{path.strip('/')}?{query}&api_key={self._api_key}"
        response = await self._request(url)
        return response.json()
