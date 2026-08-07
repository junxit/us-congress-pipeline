"""Tests for moving refs between here and a remote.

The remote here is a local bare repository, so these pin the mechanics --
force-pushing rewritten branches, batching, and verifying from the remote rather
than from git's own report -- without touching GitHub. The failure modes the
module is shaped around were measured against the live service and are recorded
in its docstring; what is testable locally is that the reconciliation loop
actually reconciles, and that a push reporting success is never taken at its
word.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs.publish import (
    PARKED_HEAD,
    prepare,
    push,
    remote_exists,
    remote_refs,
    remote_tags,
    repo_url,
)


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare repository standing in for GitHub."""
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


@pytest.fixture
def source(tmp_path: Path) -> GitRepo:
    """A built repository with three measure branches and a main."""
    repo = GitRepo(tmp_path / "source")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("main", {"README.md": "readme\n"}, "Artifacts")
        for number in (1, 2, 3):
            stream.commit(
                f"hr-{number}",
                {"bill.md": f"v1 of {number}\n"},
                "Introduced in House",
                date(2026, 1, 30),
            )
    return repo


def test_url_embeds_a_token_only_when_given() -> None:
    """A URL without a credential is what gets printed in logs and errors."""
    assert repo_url("us-congress-bills-119") == (
        "https://github.com/junxit/us-congress-bills-119.git"
    )
    assert "x-access-token:secret@" in repo_url("us-congress-bills-119", "secret")


def test_push_lands_every_ref(origin: Path, source: GitRepo) -> None:
    """The baseline the reconciliation is measured against."""
    report = push(source.path, str(origin), ["hr-1", "hr-2", "hr-3"])

    assert report.ok
    assert report.pushed == ["hr-1", "hr-2", "hr-3"]
    assert set(remote_refs(str(origin))) == {"hr-1", "hr-2", "hr-3"}


def test_push_batches(origin: Path, source: GitRepo) -> None:
    """A single request carrying ~10,000 refs is rejected atomically.

    It is rejected *after* transferring everything, and zero refs land, so the
    batch size is not a tuning knob -- it is the difference between a push that
    works and one that silently achieves nothing.
    """
    report = push(source.path, str(origin), ["hr-1", "hr-2", "hr-3"], batch=1)

    assert report.ok
    assert len(remote_refs(str(origin))) == 3


def test_a_rewritten_branch_is_force_pushed(origin: Path, source: GitRepo) -> None:
    """A correction cannot be expressed as a fast-forward.

    Rebuilding a measure gives its commits new SHAs, so a push that refused
    non-fast-forwards would reject exactly the updates this exists to make.
    """
    push(source.path, str(origin), ["hr-1"])
    before = remote_refs(str(origin))["hr-1"]

    with source.fast_import(replace=True) as stream:
        stream.commit(
            "hr-1", {"bill.md": "corrected\n"}, "Introduced in House", date(2026, 1, 30)
        )
    report = push(source.path, str(origin), ["hr-1"])

    assert report.ok
    assert remote_refs(str(origin))["hr-1"] != before
    assert remote_refs(str(origin))["hr-1"] == source.ref_map()["hr-1"]


def test_what_landed_is_read_back_from_the_remote(
    origin: Path, source: GitRepo, monkeypatch
) -> None:
    """git reports success for refs that did not land.

    About one ref in 2,000 failed transiently during the 119th while the push
    exited zero, so the only trustworthy account is the remote's own. Here the
    first round is made to drop a ref; the reconciliation must notice and send
    it again rather than believe the exit code.
    """
    import uscongress.jobs.publish as publish_module

    real_git = publish_module._git  # noqa: SLF001
    dropped: list[str] = []

    def flaky(path: Path, *args: str, check: bool = True):
        if args and args[0] == "push" and not dropped:
            # Drop hr-2 from the first push only, as a transient failure would.
            dropped.append("hr-2")
            kept = [a for a in args if "hr-2" not in a]
            return real_git(path, *kept, check=check)
        return real_git(path, *args, check=check)

    monkeypatch.setattr(publish_module, "_git", flaky)
    report = push(source.path, str(origin), ["hr-1", "hr-2", "hr-3"])

    assert report.ok, "the dropped ref should have been re-pushed"
    assert report.attempts == 2
    assert set(remote_refs(str(origin))) == {"hr-1", "hr-2", "hr-3"}


def test_refs_that_never_land_are_reported_not_assumed(
    origin: Path, source: GitRepo, monkeypatch
) -> None:
    """A ref that fails every attempt has to fail the run.

    Reporting it as pushed would leave a measure permanently behind, with the
    watermark advanced past the only window that would have caught it.
    """
    import uscongress.jobs.publish as publish_module

    real_git = publish_module._git  # noqa: SLF001

    def always_drops(path: Path, *args: str, check: bool = True):
        if args and args[0] == "push":
            return real_git(path, *[a for a in args if "hr-2" not in a], check=check)
        return real_git(path, *args, check=check)

    monkeypatch.setattr(publish_module, "_git", always_drops)
    report = push(source.path, str(origin), ["hr-1", "hr-2", "hr-3"], attempts=2)

    assert not report.ok
    assert report.missing == ["hr-2"]
    assert report.pushed == ["hr-1", "hr-3"]


def test_a_branch_that_exists_nowhere_is_not_reported_as_published(
    origin: Path, source: GitRepo
) -> None:
    """Absent locally and absent on the remote must not compare equal.

    Both lookups return None, so a naive comparison calls it published — the one
    answer this function must never give, since its whole purpose is to distrust
    the push's own account of itself.
    """
    report = push(source.path, str(origin), ["hr-1", "hr-9999"])

    assert report.pushed == ["hr-1"]
    assert report.missing == ["hr-9999"]
    assert not report.ok


def test_prepare_fetches_only_the_branches_asked_for(
    origin: Path, source: GitRepo, tmp_path: Path
) -> None:
    """The daily job touches ~170 measures, not the repository.

    Fetching every branch of the 119th costs 37 seconds and writes 18,046 loose
    ref files; fetching the ones about to be rebuilt costs 0.9 seconds and
    115 KiB.
    """
    push(source.path, str(origin), ["hr-1", "hr-2", "hr-3", "main"])

    repo = prepare(tmp_path / "ci", str(origin), ["hr-2"])

    # main comes too: it carries the README, the licence and GAPS.md.
    assert repo.branches() == {"hr-2", "main"}


def test_prepare_ignores_a_branch_that_does_not_exist_yet(
    origin: Path, source: GitRepo, tmp_path: Path
) -> None:
    """A measure introduced today has no ref on the remote.

    Naming it in the refspec makes git fail the whole fetch rather than skip it,
    so every other measure in that run would be lost too.
    """
    push(source.path, str(origin), ["hr-1", "main"])

    repo = prepare(tmp_path / "ci", str(origin), ["hr-1", "hr-9999"])

    assert repo.branches() == {"hr-1", "main"}


def test_prepare_parks_head_so_a_fetch_is_never_refused(
    origin: Path, source: GitRepo, tmp_path: Path
) -> None:
    """git refuses to fetch into the branch that is checked out.

    A fresh ``git init`` has HEAD on ``main``, which is exactly one of the
    branches that has to be fetched, so the whole fetch aborts.
    """
    push(source.path, str(origin), ["hr-1", "main"])
    repo = prepare(tmp_path / "ci", str(origin), ["hr-1"])

    head = subprocess.run(
        ["git", "-C", str(repo.path), "symbolic-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == PARKED_HEAD
    assert "main" in repo.branches()


def test_prepare_is_repeatable(origin: Path, source: GitRepo, tmp_path: Path) -> None:
    """A retried run must not fail on the remote it already added."""
    push(source.path, str(origin), ["hr-1", "main"])

    prepare(tmp_path / "ci", str(origin), ["hr-1"])
    repo = prepare(tmp_path / "ci", str(origin), ["hr-1"])

    assert repo.branches() == {"hr-1", "main"}


def test_remote_tags_dereferences_annotated_tags(origin: Path, source: GitRepo) -> None:
    """An annotated tag is listed twice, the second entry ending ``^{}``.

    Taking the listing literally reports a release point as two, one of which
    can never match a tag name.
    """
    push(source.path, str(origin), ["hr-1"])
    subprocess.run(
        ["git", "-C", str(source.path), "tag", "-a", "pl-119-102", "-m", "rp", "hr-1"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source.path), "push", "-q", str(origin), "pl-119-102"],
        check=True,
    )

    assert remote_tags(str(origin)) == {"pl-119-102"}


def test_missing_repository_reads_as_empty(tmp_path: Path) -> None:
    """A repository not created yet must not raise, only report nothing."""
    absent = str(tmp_path / "nope.git")

    assert remote_refs(absent) == {}
    assert remote_tags(absent) == set()


def test_an_empty_repository_is_distinguished_from_a_missing_one(
    origin: Path, tmp_path: Path
) -> None:
    """"No refs" and "no repository" look identical downstream.

    A Congress that has just convened has an empty shard, and so does a shard
    nobody has created. Conflating them answers "N refs did not land", which
    sends the reader hunting a transient failure that never happened.
    """
    assert remote_exists(str(origin)) is True  # exists, and has no refs yet
    assert remote_refs(str(origin)) == {}
    assert remote_exists(str(tmp_path / "nope.git")) is False
