"""Tests for the filesystem layout helpers, and for keeping credentials out of
committed text.

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


#: A fine-grained token's shape, the kind `DATA_REPO_TOKEN` is. Not a real one.
_TOKEN = "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"


def _no_credentials(monkeypatch) -> None:
    """Clear whatever credentials this shell holds, so only the test's count.

    Args:
        monkeypatch: Pytest fixture.
    """
    for name in ("GITHUB_TOKEN", "GOVINFO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", Path("/nonexistent"))


def test_a_push_token_in_a_url_is_redacted(monkeypatch) -> None:
    """The message a failed ``git remote set-url`` raises, token and all.

    ``CalledProcessError`` quotes the whole command, and both loops commit an
    exception's message as their outcome -- the route the govinfo key took into
    this public repository on 2026-09-05.
    """
    _no_credentials(monkeypatch)
    message = (
        "CalledProcessError: Command '['git', '-C', 'data/repos/us-congress-record-119', "
        "'remote', 'set-url', 'origin', "
        f"'https://x-access-token:{_TOKEN}@github.com/junxit/us-congress-record-119.git']' "
        "returned non-zero exit status 255."
    )

    redacted = config.redact(message)

    assert _TOKEN not in redacted
    assert "'https://***@github.com/junxit/us-congress-record-119.git'" in redacted
    assert redacted.endswith("returned non-zero exit status 255.")


def test_a_configured_credential_is_redacted_wherever_it_appears(monkeypatch) -> None:
    """Outside a URL only its value identifies it, as GitHub's masking assumes."""
    _no_credentials(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", _TOKEN)

    assert config.redact(f"remote: Invalid credentials {_TOKEN}.") == (
        "remote: Invalid credentials ***."
    )


def test_a_govinfo_key_in_a_query_string_is_redacted(monkeypatch) -> None:
    """The text committed on 2026-09-05, with a stand-in for the key."""
    _no_credentials(monkeypatch)
    message = (
        "listing changed packages: HTTPStatusError: 500 for https://api.govinfo.gov/"
        "collections/BILLSTATUS/2026-09-04T08:18:33Z?offsetMark=*&pageSize=1000"
        "&api_key=0123456789abcdefABCDEF0123456789abcdefAB"
    )

    assert config.redact(message).endswith("&pageSize=1000&api_key=***")


def test_an_outcome_without_credentials_is_left_alone(monkeypatch) -> None:
    """Redacting must not cost an outcome its meaning."""
    _no_credentials(monkeypatch)
    # Far too short to be a credential, and replacing it would rewrite words.
    monkeypatch.setenv("GITHUB_TOKEN", "ok")
    message = (
        "HTTPStatusError: 500 for https://api.govinfo.gov/published/2024-12-04/"
        "2027-02-01?offsetMark=*&pageSize=1000&collection=CREC"
    )

    assert config.redact(message) == message
    assert config.redact("ok") == "ok"
