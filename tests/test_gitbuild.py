"""Tests for repository construction.

The incremental sync is load-bearing: rewriting all ~56,000 section files per
release point made a single commit take minutes, which across 386 release
points is over a day. These tests pin the behaviour that makes it fast *and*
still correct about repeals.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    """An initialised repository in a temporary directory."""
    built = GitRepo(tmp_path / "repo")
    built.init()
    return built


def _manifest(repo: GitRepo) -> Path:
    """Manifest path for a repository, kept outside the working tree."""
    return repo.path.parent / "manifest.json"


def test_first_sync_writes_everything(repo: GitRepo) -> None:
    """With no manifest, every file is new."""
    change = repo.sync_tree({"a/x.md": "one", "a/y.md": "two"}, _manifest(repo))
    assert (change.written, change.removed, change.total) == (2, 0, 2)
    assert (repo.path / "a/x.md").read_text() == "one"


def test_unchanged_files_are_not_rewritten(repo: GitRepo) -> None:
    """The second sync of identical content touches nothing.

    This is the whole point: consecutive release points differ by a few hundred
    of ~56,000 sections.
    """
    files = {"a/x.md": "one", "a/y.md": "two"}
    repo.sync_tree(files, _manifest(repo))
    change = repo.sync_tree(files, _manifest(repo))
    assert change.written == 0
    assert change.removed == 0


def test_only_changed_files_are_written(repo: GitRepo) -> None:
    """A single edit writes exactly one file."""
    repo.sync_tree({"a/x.md": "one", "a/y.md": "two"}, _manifest(repo))
    change = repo.sync_tree({"a/x.md": "one", "a/y.md": "CHANGED"}, _manifest(repo))
    assert change.written == 1
    assert (repo.path / "a/y.md").read_text() == "CHANGED"


def test_removed_files_are_deleted(repo: GitRepo) -> None:
    """A repealed section must disappear, not linger.

    A release point is a full snapshot; a section absent from it has been
    repealed, and leaving the file behind would misrepresent the law.
    """
    repo.sync_tree({"a/x.md": "one", "a/y.md": "two"}, _manifest(repo))
    change = repo.sync_tree({"a/x.md": "one"}, _manifest(repo))
    assert change.removed == 1
    assert not (repo.path / "a/y.md").exists()


def test_empty_directories_are_pruned(repo: GitRepo) -> None:
    """Removing every section in a chapter removes the chapter directory."""
    repo.sync_tree({"a/gone/x.md": "one", "a/kept/y.md": "two"}, _manifest(repo))
    repo.sync_tree({"a/kept/y.md": "two"}, _manifest(repo))
    assert not (repo.path / "a/gone").exists()
    assert (repo.path / "a/kept").is_dir()


def test_commit_is_dated_to_publication(repo: GitRepo) -> None:
    """Commit dates follow legal chronology, not when the pipeline ran."""
    repo.sync_tree({"a/x.md": "one"}, _manifest(repo))
    assert repo.commit("first", when=date(2013, 7, 18)) is True
    out = subprocess.run(
        ["git", "-C", str(repo.path), "log", "-1", "--format=%ad", "--date=short"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == "2013-07-18"


def test_commit_returns_false_when_nothing_changed(repo: GitRepo) -> None:
    """An unchanged tree must not produce an empty commit.

    Release points that touch no rendered text would otherwise litter the
    history with commits that show nothing.
    """
    repo.sync_tree({"a/x.md": "one"}, _manifest(repo))
    assert repo.commit("first", when=date(2013, 7, 18)) is True
    assert repo.commit("second", when=date(2013, 8, 9)) is False
    assert repo.commit_count() == 1


def test_tags_are_detected(repo: GitRepo) -> None:
    """Tag presence drives resumability, so it must be reliable."""
    repo.sync_tree({"a/x.md": "one"}, _manifest(repo))
    repo.commit("first", when=date(2013, 7, 18))
    assert repo.has_tag("pl-113-21") is False
    repo.tag("pl-113-21")
    assert repo.has_tag("pl-113-21") is True


def _git(repo: GitRepo, *args: str) -> str:
    """Run a read-only git command and return trimmed stdout."""
    return subprocess.run(
        ["git", "-C", str(repo.path), *args], capture_output=True, text=True
    ).stdout.strip()


def test_fast_import_chains_commits_on_one_branch(repo: GitRepo) -> None:
    """Successive versions of a bill are commits on its branch, oldest first."""
    with repo.fast_import() as stream:
        stream.commit("hr-588", {"bill.md": "v1\n"}, "Introduced in House", date(2013, 2, 6))
        stream.commit("hr-588", {"bill.md": "v2\n"}, "Reported in House", date(2013, 4, 9))

    assert _git(repo, "log", "--format=%s", "hr-588").splitlines() == [
        "Reported in House",
        "Introduced in House",
    ]


def test_fast_import_appends_to_a_branch_from_an_earlier_run(repo: GitRepo) -> None:
    """A resumed build must extend existing branches, not restart them.

    fast-import does not adopt an existing branch tip on its own: without an
    explicit ``from``, the first commit of a new stream starts a fresh root and
    git rejects the update as a non-fast-forward. That failure arrives only
    after the whole run has been done, so it is worth pinning.
    """
    with repo.fast_import() as stream:
        stream.commit("hr-588", {"bill.md": "v1\n"}, "Introduced in House", date(2013, 2, 6))
    with repo.fast_import() as stream:
        stream.commit("hr-588", {"bill.md": "v2\n"}, "Enrolled Bill", date(2013, 7, 19))

    assert _git(repo, "log", "--format=%s", "hr-588").splitlines() == [
        "Enrolled Bill",
        "Introduced in House",
    ]
    assert _git(repo, "show", "hr-588:bill.md") == "v2"


def test_replace_rewrites_a_branch_from_its_root(repo: GitRepo) -> None:
    """Correcting a rendering defect cannot be expressed as an append.

    The committee selector was wrong in every ``metadata.md`` already written,
    so fixing it changes the content of commits that exist, and with it every
    SHA below them. Replace mode drops the old history rather than chaining a
    correction on top of it, which would leave the wrong text in the branch.
    """
    with repo.fast_import() as stream:
        stream.commit("hr-588", {"bill.md": "v1\n"}, "Introduced in House", date(2013, 2, 6))
        stream.commit("hr-588", {"bill.md": "v2\n"}, "Enrolled Bill", date(2013, 7, 19))

    with repo.fast_import(replace=True) as stream:
        stream.commit("hr-588", {"bill.md": "fixed\n"}, "Introduced in House", date(2013, 2, 6))

    assert _git(repo, "log", "--format=%s", "hr-588").splitlines() == ["Introduced in House"]
    assert _git(repo, "show", "hr-588:bill.md") == "fixed"
    assert _git(repo, "rev-list", "--count", "hr-588") == "1"


def test_replace_leaves_branches_it_does_not_write_alone(repo: GitRepo) -> None:
    """A rebuild of the measures must not disturb ``main``.

    ``main`` carries the README, the licence and GAPS.md, none of which the
    measure rebuild regenerates. ``--force`` applies per ref, so a branch the
    stream never mentions is untouched -- worth pinning, because losing it would
    be silent until someone opened the repository.
    """
    with repo.fast_import() as stream:
        stream.commit("main", {"README.md": "readme\n"}, "Artifacts")
        stream.commit("hr-588", {"bill.md": "v1\n"}, "Introduced in House", date(2013, 2, 6))
    before = _git(repo, "rev-parse", "main")

    with repo.fast_import(replace=True) as stream:
        stream.commit("hr-588", {"bill.md": "fixed\n"}, "Introduced in House", date(2013, 2, 6))

    assert _git(repo, "rev-parse", "main") == before
    assert repo.read_tree("main") == {"README.md": "readme\n"}


def test_branches_are_independent(repo: GitRepo) -> None:
    """Bills do not descend from a common trunk; each branch is its own root."""
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"bill.md": "house\n"}, "Introduced in House", date(2013, 1, 3))
        stream.commit("s-1", {"bill.md": "senate\n"}, "Introduced in Senate", date(2013, 1, 3))

    assert repo.branches() == {"hr-1", "s-1"}
    assert _git(repo, "show", "hr-1:bill.md") == "house"
    assert _git(repo, "show", "s-1:bill.md") == "senate"
    assert _git(repo, "rev-list", "--count", "hr-1") == "1"


def test_non_ascii_content_survives(repo: GitRepo) -> None:
    """Bill text carries section signs, em dashes and typographic quotes.

    fast-import frames payloads by byte length, so measuring characters instead
    would desynchronise the stream on any of them.
    """
    body = "§ 2. Amended— insert “and”, then sección.\n"
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"bill.md": body}, "Introduced in House", date(2013, 1, 3))

    assert _git(repo, "show", "hr-1:bill.md") == body.strip()


def test_commit_dates_follow_the_upstream_version_date(repo: GitRepo) -> None:
    """``git log`` should read as legislative chronology."""
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"bill.md": "x\n"}, "Introduced in House", date(2013, 2, 6))

    assert _git(repo, "log", "-1", "--format=%ad", "--date=short", "hr-1") == "2013-02-06"


def test_fast_import_leaves_the_working_tree_alone(repo: GitRepo) -> None:
    """The whole point: tens of thousands of branches without a checkout."""
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"bill.md": "x\n"}, "Introduced in House", date(2013, 1, 3))

    assert not (repo.path / "bill.md").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_each_commit_replaces_the_whole_tree(repo: GitRepo) -> None:
    """A version supersedes the last, so stale files must not linger."""
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"a.md": "1\n", "b.md": "2\n"}, "Introduced in House", date(2013, 1, 3))
        stream.commit("hr-1", {"a.md": "1\n"}, "Reported in House", date(2013, 2, 3))

    assert _git(repo, "ls-tree", "--name-only", "hr-1").split() == ["a.md"]
