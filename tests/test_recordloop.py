"""Tests for the Congressional Record's scheduled loop.

One invariant carries this module: the loop must never rebuild. The Record is a
single cumulative branch, so rewriting an issue day in the middle rewrites
every commit after it, and govinfo restamps already-published days in bulk --
nine of them on 2026-08-12, contents identical, 1,469 documents before and
after. A loop that reacted to those stamps would force-push the whole history
to produce the same trees. Append-only is what makes every push a fast-forward,
so it is asserted rather than assumed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from uscongress.jobs import record, recordloop
from uscongress.jobs import update as update_job


class _NoClient:
    """Stands in for :class:`uscongress.govinfo.GovInfoClient`."""

    async def __aenter__(self) -> _NoClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


@pytest.fixture
def seeded(monkeypatch, tmp_path: Path) -> dict[str, object]:
    """Run the loop against a stubbed seed and report how it was called.

    Args:
        monkeypatch: Pytest fixture.
        tmp_path: Pytest fixture.

    Returns:
        The keyword arguments :func:`uscongress.jobs.record.seed` received.
    """
    seen: dict[str, object] = {}

    async def fake_seed(_client: object, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(recordloop, "GovInfoClient", _NoClient)
    monkeypatch.setattr(record, "seed", fake_seed)
    monkeypatch.setattr(recordloop, "_days_held", lambda _path: 346)
    monkeypatch.setattr(recordloop.config, "REPOS_DIR", tmp_path)
    return seen


def test_the_scheduled_loop_never_rebuilds(seeded, tmp_path: Path) -> None:
    """Rebuilding would force-push a cumulative branch's whole history."""
    asyncio.run(
        recordloop.run(
            congress=119, publish_changes=False, state_path=tmp_path / "record.json"
        )
    )

    assert seeded["congress"] == 119
    assert seeded.get("rebuild") in (None, False)


def test_the_congress_defaults_to_the_one_sitting_today(
    seeded, tmp_path: Path
) -> None:
    """A hardcoded number would skip the 120th in silence on the day it convenes."""
    asyncio.run(
        recordloop.run(publish_changes=False, state_path=tmp_path / "record.json")
    )

    assert seeded["congress"] == record.congress_of(datetime.now(UTC).date())


def test_publishing_without_a_credential_is_recorded_not_raised(
    seeded, tmp_path: Path
) -> None:
    """The heartbeat has to survive the failure, or the run leaves no trace.

    An exception here would exit before the watermark was written, and a run
    that failed silently is the one outcome this file exists to make visible.
    """
    state_path = tmp_path / "record.json"

    status = asyncio.run(
        recordloop.run(congress=119, publish_changes=True, token="", state_path=state_path)
    )

    state = update_job.load_record_state(state_path)
    assert status == 1
    assert state.last_run is not None
    assert state.last_success is None
    assert "GITHUB_TOKEN" in state.last_outcome


def test_a_successful_run_moves_the_date(seeded, tmp_path: Path) -> None:
    """The date is the signal; nothing else on the page has to fire."""
    state_path = tmp_path / "record.json"

    status = asyncio.run(
        recordloop.run(
            congress=119, publish_changes=False, state_path=state_path
        )
    )

    state = update_job.load_record_state(state_path)
    assert status == 0
    assert state.last_outcome == "ok"
    assert state.stale_for is None
    assert state.days_present == 346
