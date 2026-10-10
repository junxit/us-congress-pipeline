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


def _tag(repo: GitRepo, name: str, branch: str) -> str:
    """Tag a branch tip, lightweight as ``GitRepo.tag`` makes them.

    Args:
        repo: The repository.
        name: Tag name.
        branch: Branch whose tip to tag.

    Returns:
        The object the tag names.
    """
    repo._run("tag", name, branch)  # noqa: SLF001
    return repo.tag_map()[name]


def test_a_tag_github_lacks_is_offered_and_one_it_disagrees_on_is_left(
    tmp_path, monkeypatch
) -> None:
    """``stat-138`` reached GitHub by hand because nothing here pushed tags.

    The opposite failure would be worse. A tag is a citation, and moving one
    already published to another commit rewrites what it cites without a
    trace, so a disagreement is reported for a person and never offered.
    """
    repo = _repo(tmp_path / "r", {"main": "a", "next": "b"})
    present = _tag(repo, "stat-137", "main")
    _tag(repo, "stat-138", "next")
    _tag(repo, "stat-136", "main")
    _remote(monkeypatch, repo.ref_map())
    monkeypatch.setattr(
        publish, "remote_tag_refs", lambda url: {"stat-137": present, "stat-136": "0" * 40}
    )

    divergence = republish.compare(tmp_path / "r", "us-congress-statutes")

    assert divergence.tags_missing == ["stat-138"]
    assert divergence.tags_conflicting == ["stat-136"]
    assert divergence.to_push == []


def test_unreadable_tags_offer_none(tmp_path, monkeypatch) -> None:
    """A network error read as "no tags" would offer every tag the repository has."""
    repo = _repo(tmp_path / "r", {"main": "a"})
    _tag(repo, "stat-001", "main")
    _remote(monkeypatch, repo.ref_map())
    monkeypatch.setattr(publish, "remote_tag_refs", lambda url: None)

    divergence = republish.compare(tmp_path / "r", "us-congress-statutes")

    assert divergence.tag_error
    assert divergence.tags_missing == []


def test_a_missing_tag_is_pushed_and_a_disagreeing_one_fails_the_run(
    tmp_path, monkeypatch, capsys
) -> None:
    """The run publishes what it can and still says a person is needed."""
    repo = _repo(tmp_path / "us-congress-statutes", {"main": "a"})
    _tag(repo, "stat-138", "main")
    _tag(repo, "stat-136", "main")
    _remote(monkeypatch, repo.ref_map())
    monkeypatch.setattr(publish, "remote_tag_refs", lambda url: {"stat-136": "0" * 40})
    asked: list[list[str]] = []

    def _push_tags(path, url, tags, batch=publish.BATCH):
        asked.append(list(tags))
        return publish.PushReport(pushed=list(tags), attempts=1)

    monkeypatch.setattr(publish, "push_tags", _push_tags)

    status = republish.run(["us-congress-statutes"], token="t", repos_dir=tmp_path)

    assert asked == [["stat-138"]]
    assert status == 1
    assert "stat-136" in capsys.readouterr().out


def test_a_dry_run_pushes_no_tag(tmp_path, monkeypatch, capsys) -> None:
    """Reported, not sent: a tag is as public as a branch."""
    repo = _repo(tmp_path / "us-congress-statutes", {"main": "a"})
    _tag(repo, "stat-138", "main")
    _remote(monkeypatch, repo.ref_map())
    monkeypatch.setattr(publish, "remote_tag_refs", lambda url: {})

    def _fail(*args, **kwargs):  # pragma: no cover
        raise AssertionError("dry run must not push")

    monkeypatch.setattr(publish, "push_tags", _fail)

    assert republish.run(["us-congress-statutes"], dry_run=True, repos_dir=tmp_path) == 0
    assert "would push 1 tags" in capsys.readouterr().out
