"""Tests for the govinfo client's pure logic.

Network behavior is not exercised here; these cover the two things most likely
to break silently -- request pacing and the bulk listing parser.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from uscongress.govinfo import BulkFile, GovInfoClient, RateLimiter


async def test_rate_limiter_spaces_requests() -> None:
    """Five acquisitions at 50/s take at least the expected four intervals."""
    limiter = RateLimiter(per_second=50.0)
    start = time.perf_counter()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.perf_counter() - start
    # 5 acquisitions => 4 gaps of 0.02s. Allow slack for scheduler jitter.
    assert elapsed >= 0.06


async def test_rate_limiter_is_concurrency_safe() -> None:
    """Concurrent callers are serialized rather than all firing at once."""
    limiter = RateLimiter(per_second=100.0)
    start = time.perf_counter()
    await asyncio.gather(*(limiter.acquire() for _ in range(10)))
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.08


def test_list_bulkdata_parses_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Folder flags and integer sizes survive the round trip."""
    payload = {
        "files": [
            {
                "name": "COMPS-8768.xml",
                "link": "https://www.govinfo.gov/bulkdata/COMPS/COMPS-8768.xml",
                "size": 8_400_000,
                "folder": False,
            },
            {"name": "resources", "link": "https://x/resources", "folder": True},
        ]
    }

    class _Response:
        def json(self) -> dict:
            return payload

    async def _fake_request(self, url, headers=None):  # noqa: ANN001, ARG001
        assert headers == {"Accept": "application/json"}, "406 trap: header required"
        return _Response()

    monkeypatch.setenv("GOVINFO_API_KEY", "test-key")
    monkeypatch.setattr(GovInfoClient, "_request", _fake_request)

    async def run() -> list[BulkFile]:
        client = GovInfoClient(api_key="test-key")
        try:
            return await client.list_bulkdata("COMPS")
        finally:
            await client.__aexit__()

    entries = asyncio.run(run())
    assert len(entries) == 2
    assert entries[0].name == "COMPS-8768.xml"
    assert entries[0].size == 8_400_000
    assert entries[0].is_folder is False
    # Missing "size" on folders must not raise.
    assert entries[1].is_folder is True
    assert entries[1].size == 0


async def test_api_key_travels_in_a_header_not_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key must never be part of a URL, because a URL ends up in messages."""
    seen: dict[str, object] = {}

    class _Response:
        def json(self) -> dict:
            return {"count": 0}

    async def _fake_request(self, url, headers=None):  # noqa: ANN001, ARG001
        seen["url"] = url
        seen["headers"] = headers
        return _Response()

    monkeypatch.setattr(GovInfoClient, "_request", _fake_request)
    client = GovInfoClient(api_key="test-key")
    try:
        await client.api_json("collections/BILLSTATUS/2026-01-01T00:00:00Z", offsetMark="*")
    finally:
        await client.__aexit__()

    assert "test-key" not in str(seen["url"])
    assert seen["url"] == (
        "https://api.govinfo.gov/collections/BILLSTATUS/2026-01-01T00:00:00Z?offsetMark=*"
    )
    assert seen["headers"] == {"X-Api-Key": "test-key"}


async def test_an_api_error_does_not_carry_the_key() -> None:
    """The failure that published the key: a 500's message quoted the full URL.

    On 2026-09-05 that message became ``last_outcome`` in two state files and
    the Outcome row of STATUS.md, all committed to a public repository by the
    scheduled jobs. Whatever an error says now, the key is not in it.
    """
    received: list[httpx.Request] = []

    def _answer(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(500, text="Internal Server Error")

    client = GovInfoClient(api_key="secret-key-123", max_attempts=1)
    await client._client.aclose()  # noqa: SLF001
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_answer))  # noqa: SLF001
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.api_json("published/2026-01-01/2026-01-02", collection="CREC")
    finally:
        await client.__aexit__()

    assert received[0].headers["X-Api-Key"] == "secret-key-123"
    assert "secret-key-123" not in str(received[0].url)
    assert "secret-key-123" not in str(caught.value)
    assert "secret-key-123" not in repr(caught.value.request.url)
