"""Publishing a corpus-wide rewrite without pushing the 95% that did not move.

``seed-bills --rebuild`` rewrites every branch from its root, and a branch whose
content did not change re-renders to identical bytes and keeps its SHA. So the
set worth pushing is the set that actually differs from the remote, and reading
it back from the remote rather than assuming it is what turns a 160,190-ref
force push into a 7,510-ref one.

The remote here is a dictionary rather than a bare repository: what is being
tested is the comparison, and ``test_publish.py`` already covers git's side.
"""

from __future__ import annotations

from pathlib import Path

from uscongress.gitbuild import GitRepo
from uscongress.jobs import publish, republish


def _repo(path: Path, branches: dict[str, str]) -> GitRepo:
    """A repository with one commit per named branch."""
    repo = GitRepo(path)
    repo.init()
    with repo.fast_import() as stream:
        for branch, content in branches.items():
            stream.commit(branch, {"bill.md": content}, f"{branch}\n")
    return repo


def _remote(monkeypatch, refs: dict[str, str], exists: bool = True) -> None:
    """Stand in for GitHub."""
    monkeypatch.setattr(publish, "remote_refs", lambda url: dict(refs))
    monkeypatch.setattr(publish, "remote_exists", lambda url: exists)


def test_only_the_branches_that_moved_are_offered_for_pushing(
    tmp_path, monkeypatch
) -> None:
    """This is the whole point: 4.4% of measures carry a recorded vote.

    A rebuild touches every branch, so taking "what the rebuild wrote" as the
    push set would send all 160,190 refs of the corpus for a change that reaches
    7,510 of them -- hours of force-pushing to publish nothing new, at a batch
    size GitHub rejects outright above about 800.
    """
    repo = _repo(tmp_path / "r", {"hr-1": "a", "hr-2": "b", "hr-3": "c"})
    local = repo.ref_map()
    _remote(
        monkeypatch,
        {"hr-1": local["hr-1"], "hr-2": "0" * 40, "hr-3": local["hr-3"]},
    )

    divergence = republish.compare(tmp_path / "r", "us-congress-bills-113")

    assert divergence.moved == ["hr-2"]
    assert divergence.added == []
    assert divergence.unchanged == 2
    assert divergence.to_push == ["hr-2"]


def test_a_branch_that_is_not_published_yet_is_pushed(tmp_path, monkeypatch) -> None:
    """A new measure has no remote SHA to differ from."""
    repo = _repo(tmp_path / "r", {"hr-1": "a", "hr-2": "b"})
    _remote(monkeypatch, {"hr-1": repo.ref_map()["hr-1"]})

    divergence = republish.compare(tmp_path / "r", "us-congress-bills-113")

    assert divergence.added == ["hr-2"]
    assert divergence.moved == []
    assert divergence.to_push == ["hr-2"]


def test_a_branch_published_but_not_built_here_is_reported_never_deleted(
    tmp_path, monkeypatch
) -> None:
    """A branch missing from a local build is far more likely a broken build.

    A rebuild that died half way leaves a repository missing thousands of
    branches, and a publisher that treated absence as intent would delete them
    from GitHub. Nothing here deletes; the count is printed so the operator can
    tell the two apart themselves.
    """
    repo = _repo(tmp_path / "r", {"hr-1": "a"})
    _remote(monkeypatch, {"hr-1": repo.ref_map()["hr-1"], "hr-9": "0" * 40})

    divergence = republish.compare(tmp_path / "r", "us-congress-bills-113")

    assert divergence.remote_only == ["hr-9"]
    assert divergence.to_push == []


def test_a_repository_that_was_never_built_locally_is_an_error(tmp_path) -> None:
    """Comparing an empty directory against a live remote reports everything.

    Silently treating "nothing here" as "nothing to do" would let a run against
    the wrong ``--repos-path`` report a clean corpus.
    """
    divergence = republish.compare(tmp_path / "absent", "us-congress-bills-113")

    assert divergence.error == "not built locally"
    assert divergence.to_push == []


def test_a_repository_missing_from_github_is_an_error(tmp_path, monkeypatch) -> None:
    """The 120th Congress convening is the ordinary way to reach this."""
    _repo(tmp_path / "r", {"hr-1": "a"})
    _remote(monkeypatch, {}, exists=False)

    divergence = republish.compare(tmp_path / "r", "us-congress-bills-120")

    assert divergence.error == "no such repository on GitHub"


def test_a_dry_run_pushes_nothing(tmp_path, monkeypatch) -> None:
    """Force-pushing public repositories is not something to do by accident."""
    repo = _repo(tmp_path / "us-congress-bills-113", {"hr-1": "a"})
    _remote(monkeypatch, {"hr-1": "0" * 40})

    def _fail(*args, **kwargs):  # pragma: no cover
        raise AssertionError("dry run must not push")

    monkeypatch.setattr(publish, "push", _fail)

    assert republish.run(
        ["us-congress-bills-113"], dry_run=True, repos_dir=tmp_path
    ) == 0
    assert repo.ref_map()  # untouched


def test_refs_that_did_not_land_fail_the_run(tmp_path, monkeypatch) -> None:
    """git reports success for refs that did not land, so the exit code cannot.

    ``publish.push`` reads the remote back; this is the layer that has to act on
    what it found rather than on git's word.
    """
    _repo(tmp_path / "us-congress-bills-113", {"hr-1": "a"})
    _remote(monkeypatch, {"hr-1": "0" * 40})
    monkeypatch.setattr(
        publish,
        "push",
        lambda *a, **k: publish.PushReport(pushed=[], missing=["hr-1"], attempts=3),
    )

    assert republish.run(
        ["us-congress-bills-113"], token="t", repos_dir=tmp_path
    ) == 1
