"""Tests for getting the generated repositories onto a machine that lacks them.

A fresh clone of the pipeline has an empty ``data/`` against roughly 93 GB on a
machine that has been building, and the obvious reading -- rebuild the corpus
from govinfo -- costs days where cloning costs minutes. These pin the two ways
that shortcut can quietly fail: fetching the wrong *set* of repositories, and
fetching them in a way that does not actually reproduce the remote.

The remote here is a local bare repository, as in ``test_publish.py``, so
nothing touches GitHub.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs import bootstrap
from uscongress.jobs.publish import prepare_all


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare repository standing in for GitHub, holding two branches."""
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)

    source = GitRepo(tmp_path / "source")
    source.init()
    with source.fast_import() as stream:
        stream.commit("main", {"README.md": "readme\n", "GAPS.md": "# gaps\n"}, "Artifacts")
        stream.commit("daily", {"1994/01-25/README.md": "day\n"}, "1994-01-25", date(1994, 1, 25))
    subprocess.run(
        ["git", "-C", str(source.path), "push", "-q", str(path),
         "+refs/heads/main:refs/heads/main", "+refs/heads/daily:refs/heads/daily"],
        check=True,
    )
    return path


def test_a_bootstrapped_clone_reproduces_the_remote(origin: Path, tmp_path: Path) -> None:
    """Anything less than an exact ref match is a corpus that disagrees.

    The point of cloning rather than rebuilding is that the history is the work;
    a fetch that drops a branch has thrown away the thing worth moving.
    """
    repo = prepare_all(tmp_path / "clone", str(origin))

    assert repo.branches() == {"daily", "main"}
    assert repo.read_tree("main")["GAPS.md"] == "# gaps\n"


def test_bootstrapping_twice_changes_nothing(origin: Path, tmp_path: Path) -> None:
    """Every job here is resumable, and this one is no exception.

    A second run must fetch rather than reclone, so interrupting a 31-repository
    bootstrap costs only the repository it was on.
    """
    first = prepare_all(tmp_path / "clone", str(origin)).ref_map()
    second = prepare_all(tmp_path / "clone", str(origin)).ref_map()

    assert first == second


def test_the_repository_list_comes_from_github_not_the_disk(monkeypatch) -> None:
    """This is the failure that would make the whole command useless.

    `describe.repositories` expands shard families by globbing `data/repos`,
    which is correct everywhere else and exactly wrong here: on the fresh clone
    this exists to serve, that directory is empty, so it reports the three
    unsharded repositories, omits all 29 shards, and leaves the machine looking
    bootstrapped. The list has to come from where the repositories actually are.
    """
    listing = (
        "us-congress-pipeline\nus-congress-code\nus-congress-bills-119\n"
        "us-congress-bills-109\nus-congress-bills-110\nunrelated-repo\n"
    )

    real_run = subprocess.run

    def fake(cmd, **kwargs):
        # Delegate anything that is not the GitHub listing. `bootstrap.subprocess`
        # *is* the shared module, so patching it blindly hands this stub to every
        # other caller in the process, pytest's own included -- which hangs the
        # run rather than failing it.
        page = [a for a in cmd if isinstance(a, str) and a.startswith(f"users/")]
        if not page:
            return real_run(cmd, **kwargs)
        # `endswith`, not `in`: the URL is `...per_page=100&page=2`, and
        # `"page=1" in` that matches inside `per_page=100`, so every page
        # returned the first one and the paging loop never terminated.
        out = listing if page[0].endswith("page=1") else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    names = bootstrap.remote_repositories()

    assert "unrelated-repo" not in names
    # Numeric, so the 109th does not sort after the 110th.
    assert names == [
        "us-congress-bills-109",
        "us-congress-bills-110",
        "us-congress-bills-119",
        "us-congress-code",
        "us-congress-pipeline",
    ]


def test_a_repository_that_does_not_exist_yet_is_reported_not_fatal(
    tmp_path: Path, monkeypatch
) -> None:
    """A planned phase names repositories nobody has created.

    Failing on them would make `bootstrap` unusable for exactly as long as any
    phase remains planned, which is most of this project's life.
    """
    # No network: `remote_exists` would otherwise reach github.com for a
    # repository that is meant not to be there.
    monkeypatch.setattr(bootstrap.publish, "remote_exists", lambda url: False)
    result = bootstrap.fetch_one("us-congress-nope", tmp_path)

    assert result.skipped
    assert result.refs == 0
    assert not (tmp_path / "us-congress-nope" / ".git").is_dir()
