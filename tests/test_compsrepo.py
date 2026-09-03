"""Tests for publishing the Statute Compilations snapshots.

Weighted towards what the commit message claims, because for this repository
the message *is* the interface. ``git log`` answering *what changed in this
compilation, and when* is the entire reason these files are named for
compilations rather than for their hashes, and a message that says everything
changed every day destroys the thing the layout was chosen for.

It said exactly that for nineteen days. The counts came from writes against a
manifest kept outside the repository, and the scheduled runner that does the
publishing starts with nothing, so the manifest was absent on every single run.
"""

from __future__ import annotations

from pathlib import Path

from uscongress.gitbuild import GitRepo
from uscongress.jobs import compsrepo


def _repo(tmp_path: Path) -> GitRepo:
    """Return an initialised repository on the snapshots branch.

    Args:
        tmp_path: Pytest fixture.

    Returns:
        The repository.
    """
    repo = GitRepo(tmp_path / "us-congress-comps")
    repo.init()
    repo._run("checkout", "--quiet", "-B", compsrepo.SNAPSHOTS)  # noqa: SLF001
    return repo


def _day(**files: str) -> dict[str, str]:
    """Build a snapshot tree, with the metadata file every day carries.

    Args:
        **files: Compilation name without suffix mapped to its contents.

    Returns:
        The tree.
    """
    tree = {f"COMPS-{name.lstrip('c')}.xml": body for name, body in files.items()}
    tree["snapshot.json"] = '{"snapshot_date": "2026-09-03"}\n'
    return tree


def test_an_unchanged_day_reports_nothing_changed(tmp_path: Path) -> None:
    """The bug, stated as a test.

    Every published commit claimed all 2,682 compilations had changed while the
    real diff was one file. Anyone reading `git log` to find out when a
    compilation was amended got every day as the answer.
    """
    repo = _repo(tmp_path)
    compsrepo._materialise(repo, _day(c1="a\n", c2="b\n"))  # noqa: SLF001
    repo.commit("first")

    changed, withdrawn = compsrepo._materialise(  # noqa: SLF001
        repo, _day(c1="a\n", c2="b\n")
    )

    assert (changed, withdrawn) == (0, 0)


def test_a_withdrawn_compilation_leaves_the_tree(tmp_path: Path) -> None:
    """Nothing was ever deleted, and nothing said so.

    The removal pass iterated the manifest, so an empty manifest withdrew
    nothing: a compilation govinfo dropped would have stayed in the tree for
    ever while the message reported "0 withdrawn". It had not happened yet,
    which is luck rather than design.
    """
    repo = _repo(tmp_path)
    compsrepo._materialise(repo, _day(c1="a\n", c2="b\n"))  # noqa: SLF001
    repo.commit("first")

    changed, withdrawn = compsrepo._materialise(repo, _day(c1="a\n"))  # noqa: SLF001
    repo.commit("second")

    assert (changed, withdrawn) == (0, 1)
    assert "COMPS-2.xml" not in repo.list_files(compsrepo.SNAPSHOTS)


def test_amendments_and_additions_are_counted_apart_from_withdrawals(
    tmp_path: Path,
) -> None:
    """The three things a day can do to the collection, in one commit."""
    repo = _repo(tmp_path)
    compsrepo._materialise(repo, _day(c1="a\n", c2="b\n", c3="c\n"))  # noqa: SLF001
    repo.commit("first")

    changed, withdrawn = compsrepo._materialise(  # noqa: SLF001
        repo, _day(c1="a\n", c2="AMENDED\n", c4="new\n")
    )

    assert (changed, withdrawn) == (2, 1)


def test_the_metadata_file_is_not_counted_as_a_compilation(tmp_path: Path) -> None:
    """It moves every day by construction, which is what makes the commit exist.

    Counting it would put a floor of one under every count and make an
    unchanged day indistinguishable from a day that amended one compilation.
    """
    repo = _repo(tmp_path)
    compsrepo._materialise(repo, _day(c1="a\n"))  # noqa: SLF001
    repo.commit("first")

    tree = _day(c1="a\n")
    tree["snapshot.json"] = '{"snapshot_date": "2026-09-04"}\n'

    assert compsrepo._materialise(repo, tree) == (0, 0)  # noqa: SLF001


def test_the_counts_do_not_depend_on_anything_outside_the_repository(
    tmp_path: Path,
) -> None:
    """A scheduled runner starts with nothing but the clone, and must still be right.

    This is the property the old implementation lacked: its manifest lived in
    `data/repos/`, which is gitignored, so a fresh runner compared against an
    empty record and reported every file as new.
    """
    repo = _repo(tmp_path)
    compsrepo._materialise(repo, _day(c1="a\n", c2="b\n"))  # noqa: SLF001
    repo.commit("first")

    # Nothing outside the repository survives; the clone is all there is.
    for stray in tmp_path.iterdir():
        if stray != repo.path:
            stray.unlink()

    assert compsrepo._materialise(repo, _day(c1="a\n", c2="b\n")) == (0, 0)  # noqa: SLF001
