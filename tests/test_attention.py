"""Tests for the check that says what a schedule cannot do.

Weighted towards one property: **a condition that cannot be evaluated must not
read as a condition that is not due.** This check exists to replace a
hand-written list nobody rereads, and a check that silently always passes is
worse than the list it replaced -- it looks like an answer.

So every condition is tested in both directions, and the ones that reach the
network are tested for what they say when the network does not answer.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from uscongress.jobs import attention
from uscongress.jobs.attention import Condition


def _keys(due: list[Condition]) -> list[str]:
    """Return the keys of a condition list.

    Args:
        due: Conditions.

    Returns:
        Their keys, in order.
    """
    return [c.key for c in due]


def test_a_missing_shard_for_the_sitting_congress_is_due(monkeypatch) -> None:
    """The 120th convening is the ordinary way to reach this.

    Both loops fail loudly every day once govinfo reports a shard nothing has
    created. Saying so before that turns an outage into an errand.
    """
    monkeypatch.setattr(attention.publish, "remote_exists", lambda _url: False)

    assert _keys(attention.shards_exist(120)) == [
        "repo-missing:us-congress-bills-120",
        "repo-missing:us-congress-record-120",
    ]


def test_an_existing_shard_is_not_due(monkeypatch) -> None:
    """The ordinary day has to be quiet, or nobody reads the noisy one."""
    monkeypatch.setattr(attention.publish, "remote_exists", lambda _url: True)

    assert attention.shards_exist(119) == []


def test_a_disabled_schedule_is_due(monkeypatch) -> None:
    """A disabled workflow never runs to report its own death.

    GitHub disables a schedule after 60 days without repository activity, which
    is why this is asked of GitHub rather than of either loop.
    """

    def fake(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [], 0, stdout="update active\nrecord disabled_inactivity\n", stderr=""
        )

    monkeypatch.setattr(attention.subprocess, "run", fake)

    assert _keys(attention.schedules_enabled()) == [
        "schedule-disabled:record",
    ]


def test_an_unanswerable_schedule_question_is_due_not_silent(monkeypatch) -> None:
    """The whole point. Not knowing must not render as nothing being wrong."""

    def fake(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout="", stderr="gh: not logged in")

    monkeypatch.setattr(attention.subprocess, "run", fake)

    assert _keys(attention.schedules_enabled()) == ["schedule-unknown"]


def test_a_backlog_the_loop_computes_but_never_escalates_is_due() -> None:
    """Both of these sit on STATUS.md indefinitely with nothing pressing them.

    Neither is an error -- holding the watermark for an unplaceable package
    would freeze every measure behind it -- so escalation has to come from
    somewhere other than the run's exit code.
    """
    from uscongress.jobs.update import State

    due = attention.backlog(
        State(pending_release_points=["pl-119-103"], unplaceable=["BILLSTATUS-119zz1"])
    )

    assert _keys(due) == ["release-points-pending", "unplaceable-packages"]
    assert attention.backlog(State()) == []


def test_a_crosswalk_older_than_the_sitting_congress_is_due(monkeypatch) -> None:
    """A senator seated after extraction gets no bioguide ID, invisibly.

    That renders exactly like the pre-crosswalk behavior, so nothing about the
    output would tell you the table had gone stale.
    """
    monkeypatch.setattr(
        attention.record, "congress_span", lambda _c: (date(2027, 1, 3), date(2029, 1, 3))
    )

    assert _keys(attention.members_current(120)) == ["members-stale"]


def test_a_current_crosswalk_is_not_due(monkeypatch) -> None:
    """The table was extracted during this Congress, so it covers it."""
    monkeypatch.setattr(
        attention.record, "congress_span", lambda _c: (date(2025, 1, 3), date(2027, 1, 3))
    )

    assert attention.members_current(119) == []


def test_a_stale_comps_snapshot_is_due(monkeypatch, tmp_path: Path) -> None:
    """The only job whose missed day cannot be recovered.

    govinfo replaces Statute Compilations in place and keeps no archive, so this
    is the one staleness in the project that is permanent rather than catching
    up on the next run.
    """
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "2026-08-01.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(attention.config, "COMPS_SNAPSHOTS_DIR", snapshots)

    due = attention.comps_current(datetime(2026, 8, 16, tzinfo=UTC))

    assert _keys(due) == ["comps-stale"]
    assert "15 days old" in due[0].summary


def test_a_fresh_comps_snapshot_is_not_due(monkeypatch, tmp_path: Path) -> None:
    """One missed day is a hiccup; the threshold is two, like the heartbeat."""
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    (snapshots / "2026-08-15.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(attention.config, "COMPS_SNAPSHOTS_DIR", snapshots)

    assert attention.comps_current(datetime(2026, 8, 16, tzinfo=UTC)) == []


def test_the_list_round_trips(tmp_path: Path) -> None:
    """STATUS.md renders from this file, so what is written must read back."""
    due = [Condition("a-key", "something is true", "do something")]
    path = attention.save(due, tmp_path / "attention.json")

    checked, read = attention.load(path)

    assert read == due
    assert checked is not None


def test_a_missing_list_reads_as_never_checked(tmp_path: Path) -> None:
    """Never checked must not render as a clean bill of health."""
    checked, due = attention.load(tmp_path / "absent.json")

    assert checked is None
    assert due == []


def test_upstream_is_reported_unchecked_rather_than_passing() -> None:
    """No client means the question went unasked, which is not the same as no."""
    import asyncio

    due = asyncio.run(attention.check(None, Path("/nonexistent/update.json")))

    assert "upstream-unchecked" in _keys(due)


def test_a_repository_built_here_and_absent_from_github_is_due(
    monkeypatch, tmp_path: Path
) -> None:
    """The other way a repository comes to be owed.

    The pipeline learns to build something new, it is verified locally, and then
    it waits on the two steps no API can take. Without this the job that
    publishes it just fails every day with nothing saying why.
    """
    (tmp_path / "us-congress-comps" / ".git").mkdir(parents=True)
    monkeypatch.setattr(attention.config, "REPOS_DIR", tmp_path)
    monkeypatch.setattr(attention.publish, "remote_exists", lambda _url: False)

    assert _keys(attention.registry_repos_exist()) == [
        "repo-unpublished:us-congress-comps"
    ]


def test_a_repository_never_built_here_is_not_owed(monkeypatch, tmp_path: Path) -> None:
    """Not yet written is not the same as waiting on somebody."""
    monkeypatch.setattr(attention.config, "REPOS_DIR", tmp_path)
    monkeypatch.setattr(attention.publish, "remote_exists", lambda _url: False)

    assert attention.registry_repos_exist() == []
