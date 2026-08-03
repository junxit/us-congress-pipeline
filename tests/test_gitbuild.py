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
