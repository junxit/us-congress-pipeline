"""Tests for the govinfo client's pure logic.

Network behaviour is not exercised here; these cover the two things most likely
to break silently -- request pacing and the bulk listing parser.
"""

from __future__ import annotations

import asyncio
import time

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
    """Concurrent callers are serialised rather than all firing at once."""
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
