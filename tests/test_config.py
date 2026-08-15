"""Tests for the filesystem layout helpers.

Weighted towards :func:`uscongress.config.built_shards`, because the thing it
replaced was a hardcoded ``range(108, 120)`` in ``republish``: correct on the
day it was written, and silently wrong on the day the 120th Congress convenes.
A default that skips a repository while exiting zero is the failure mode this
project is built around preventing, so the test that matters is the one for a
Congress that does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

from uscongress import config


def _shard(root: Path, name: str) -> Path:
    """Create something that looks enough like a cloned repository.

    Args:
        root: Directory to create it under.
        name: Repository directory name.

    Returns:
        The directory created.
    """
    path = root / name
    (path / ".git").mkdir(parents=True)
    return path


def test_a_new_congress_is_picked_up_without_a_code_change(
    monkeypatch, tmp_path: Path
) -> None:
    """The 120th convening must not need anyone to remember to widen a range."""
    for congress in (118, 119, 120):
        _shard(tmp_path, f"us-congress-bills-{congress}")
    monkeypatch.setattr(config, "REPOS_DIR", tmp_path)

    found = config.built_shards("us-congress-bills-{congress}")

    assert found == [
        "us-congress-bills-118",
        "us-congress-bills-119",
        "us-congress-bills-120",
    ]


def test_shards_sort_numerically_not_as_text(monkeypatch, tmp_path: Path) -> None:
    """Sorted as text the 109th comes after the 110th, and the report misleads."""
    for congress in (108, 109, 110, 119):
        _shard(tmp_path, f"us-congress-bills-{congress}")
    monkeypatch.setattr(config, "REPOS_DIR", tmp_path)

    found = config.built_shards("us-congress-bills-{congress}")

    assert [name.rsplit("-", 1)[-1] for name in found] == ["108", "109", "110", "119"]


def test_a_preserved_pre_fix_copy_is_not_a_repository(
    monkeypatch, tmp_path: Path
) -> None:
    """It matches the glob and nobody consumes it; pushing it would be wrong."""
    _shard(tmp_path, "us-congress-code")
    _shard(tmp_path, "us-congress-code.pre-fix")
    monkeypatch.setattr(config, "REPOS_DIR", tmp_path)

    assert config.built_shards("us-congress-cod{congress}") == ["us-congress-code"]


def test_a_directory_without_a_git_dir_is_not_a_repository(
    monkeypatch, tmp_path: Path
) -> None:
    """A half-made directory must not be reported as something to publish."""
    (tmp_path / "us-congress-bills-119").mkdir()
    monkeypatch.setattr(config, "REPOS_DIR", tmp_path)

    assert config.built_shards("us-congress-bills-{congress}") == []


def test_nothing_cloned_reports_nothing(monkeypatch, tmp_path: Path) -> None:
    """A fresh clone has no data repositories, and that is not an error."""
    monkeypatch.setattr(config, "REPOS_DIR", tmp_path)

    assert config.built_shards("us-congress-record-{congress}") == []
