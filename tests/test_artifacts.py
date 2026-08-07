"""Tests for the artifacts that let a generated repository stand alone.

These repositories are published without their pipeline, so the README has to
answer "what is this and what can I do with it" on its own, and the licence has
to be right about two different kinds of material at once.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs import artifacts
from uscongress.jobs.artifacts import (
    LICENSE_DATA,
    LICENSE_PIPELINE,
    readme,
    write_repo,
)

BUILT = {"us-congress-code", "us-congress-bills-113", "us-congress-bills-114"}


@pytest.fixture
def bills_repo(tmp_path: Path) -> GitRepo:
    """A bills repository with one measure and a gaps record on main."""
    repo = GitRepo(tmp_path / "us-congress-bills-113")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("hr-588", {"bill.md": "text\n"}, "H.R. 588 Introduced in House")
        stream.commit("main", {"GAPS.md": "# gaps\n"}, "Record gaps")
    return repo


def test_data_licence_disclaims_the_federal_text() -> None:
    """Federal law cannot be copyrighted, so claiming it would be a false claim.

    17 U.S.C. § 105 puts work of the United States Government in the public
    domain. Asserting rights over the statutes would be visibly wrong once these
    repositories are public.
    """
    assert "17 U.S.C. § 105" in LICENSE_DATA
    assert "public domain" in LICENSE_DATA.lower()
    assert "no rights are asserted over" in LICENSE_DATA.lower()


def test_data_licence_still_reserves_the_authored_layer() -> None:
    """The conversion, structure and prose are not federal work."""
    assert "Copyright (c) 2026 Jade Naaman" in LICENSE_DATA
    assert "All rights are reserved over that layer" in LICENSE_DATA


def test_pipeline_licence_is_plainly_proprietary() -> None:
    """The pipeline itself carries no public-domain component."""
    assert "All rights reserved" in LICENSE_PIPELINE
    assert "proprietary" in LICENSE_PIPELINE
    assert "17 U.S.C." not in LICENSE_PIPELINE


def test_readme_points_back_at_the_pipeline(tmp_path: Path) -> None:
    """A published repository must say what generated it."""
    text = readme("us-congress-bills-113", tmp_path, BUILT)
    assert "https://github.com/junxit/us-congress-pipeline" in text


def test_readme_links_sibling_shards(tmp_path: Path) -> None:
    """A shard family is useless as a cross-reference unless members are named.

    Someone reading the 113th should reach the 114th without guessing a URL.
    """
    text = readme("us-congress-bills-113", tmp_path, BUILT)
    assert "us-congress-bills-114" in text
    assert "**`113`**" in text  # the current repo, not a link to itself


def test_readme_marks_where_you_are(tmp_path: Path) -> None:
    """The cross-reference table is shared, so it has to orient the reader."""
    text = readme("us-congress-bills-113", tmp_path, BUILT)
    assert "← you are here" in text


def test_readme_states_the_diff_caveat(tmp_path: Path) -> None:
    """The project's central caveat must survive being read in isolation.

    A bill diff shows how the bill changed, not how the US Code would change.
    Someone consuming this repository alone has no other way to learn that.
    """
    text = readme("us-congress-bills-113", tmp_path, BUILT)
    assert "not how the US Code would change" in text
    assert "derived copy" in text


def test_code_readme_describes_its_own_layout(tmp_path: Path) -> None:
    """The two repository shapes need different instructions."""
    text = readme("us-congress-code", tmp_path, BUILT)
    assert "title-NN/chapter-NN/sec-NNN.md" in text
    assert "release point" in text
    assert "hr-588" not in text  # bills guidance must not leak in


def test_write_repo_keeps_the_existing_gaps_record(bills_repo: GitRepo) -> None:
    """fast-import sets the whole tree, so an unread file would be deleted."""
    assert write_repo(bills_repo.path, "us-congress-bills-113", BUILT)

    listing = subprocess.run(
        ["git", "-C", str(bills_repo.path), "ls-tree", "--name-only", "main"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert sorted(listing) == ["GAPS.md", "LICENSE", "README.md"]


def test_write_repo_leaves_measure_branches_alone(bills_repo: GitRepo) -> None:
    """Artifacts belong on main; a bill branch holds only its own files."""
    write_repo(bills_repo.path, "us-congress-bills-113", BUILT)

    listing = subprocess.run(
        ["git", "-C", str(bills_repo.path), "ls-tree", "--name-only", "hr-588"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert listing == ["bill.md"]


def test_a_fast_import_repository_is_never_written_through_the_working_tree(
    tmp_path,
) -> None:
    """Which write path is correct depends on how a repository was built.

    This used to test the name prefix ``us-congress-bills-``. The Congressional
    Record shards are also written through fast-import and also have nothing
    checked out, so they did not match it, took the working-tree path, and
    ``git add -A`` staged the deletion of everything on ``main`` that was not one
    of the two files just written. It really removed ``GAPS.md`` from
    ``us-congress-record-115`` -- silently, because deleting a file is a
    perfectly ordinary commit.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("main", {"GAPS.md": "# gaps\n"}, "Record what is missing")

    artifacts.write_repo(repo.path, "us-congress-record-115", {"us-congress-record-115"})

    assert sorted(repo.read_tree("main")) == ["GAPS.md", "LICENSE", "README.md"]


def test_the_wreckage_of_a_bad_run_does_not_look_like_a_working_tree(tmp_path) -> None:
    """The first fix for the above was defeated by its own damage.

    It asked whether *anything* was on disk. But the buggy run had already
    written ``README.md`` and ``LICENSE`` into the directory, so the next run saw
    two files, concluded there was a working tree, and deleted ``GAPS.md`` a
    second time. The question has to be whether *every* tracked file is present.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("main", {"GAPS.md": "# gaps\n"}, "Record what is missing")
    # Exactly the residue the bad run left behind.
    (repo.path / "README.md").write_text("stale\n", encoding="utf-8")
    (repo.path / "LICENSE").write_text("stale\n", encoding="utf-8")

    artifacts.write_repo(repo.path, "us-congress-record-115", {"us-congress-record-115"})

    assert sorted(repo.read_tree("main")) == ["GAPS.md", "LICENSE", "README.md"]


def test_write_repo_is_idempotent(bills_repo: GitRepo) -> None:
    """Regenerating after a phase lands must not churn every repository."""
    assert write_repo(bills_repo.path, "us-congress-bills-113", BUILT)
    assert not write_repo(bills_repo.path, "us-congress-bills-113", BUILT)


def test_write_repo_creates_main_when_there_are_no_gaps(tmp_path: Path) -> None:
    """A Congress with complete coverage has no gaps record, so no main branch.

    The 116th is exactly this case, and without handling it the repository would
    publish with no README at all.
    """
    repo = GitRepo(tmp_path / "us-congress-bills-116")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("hr-1", {"bill.md": "text\n"}, "H.R. 1 Introduced in House")

    assert write_repo(repo.path, "us-congress-bills-116", BUILT)
    assert "main" in repo.branches()


def test_readme_does_not_link_repositories_that_do_not_exist(tmp_path: Path) -> None:
    """A planned repository has no URL to point at yet.

    Linking one publishes a 404 into every repository in the set at once, and
    these are generated in bulk, so a single wrong branch multiplies.
    """
    text = readme("us-congress-bills-113", tmp_path, BUILT)

    assert "https://github.com/junxit/us-congress-statutes" not in text
    assert "`us-congress-statutes`" in text  # still named, just not linked


def test_readme_links_repositories_that_do_exist(tmp_path: Path) -> None:
    """The point of the table is to be navigable where navigation is possible."""
    text = readme("us-congress-bills-113", tmp_path, BUILT)
    assert "https://github.com/junxit/us-congress-code" in text


def test_write_repo_refuses_to_publish_a_broken_link(bills_repo: GitRepo, monkeypatch) -> None:
    """Catching a bad link at write time beats catching it after publication.

    These are generated in bulk, so a wrong branch in the template lands in
    every repository at once; refusing here means it is never committed.
    """
    import uscongress.jobs.artifacts as mod

    monkeypatch.setattr(
        mod,
        "readme",
        lambda *a, **k: "See [ghost](https://github.com/junxit/us-congress-ghost).\n",
    )
    with pytest.raises(ValueError, match="broken links"):
        write_repo(bills_repo.path, "us-congress-bills-113", BUILT)

    # Nothing was committed: main still holds only what it started with.
    assert sorted(GitRepo(bills_repo.path).read_tree("main")) == ["GAPS.md"]


def test_record_examples_name_paths_that_exist(tmp_path) -> None:
    """A README whose commands fail is worse than one with none.

    The first draft hard-coded `git show
    daily:2017/01-03/senate/001-senate-chamber-action.md`, and no such file
    exists -- that day's first Senate item is `001-congressional-record.md` --
    while the diff example named a day the bound edition does not carry. Shard
    contents differ, so the examples are read from the repository they will be
    published into.
    """
    repo = GitRepo(tmp_path / "us-congress-record-115")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit(
            "daily",
            {
                "2017/01-03/README.md": "index\n",
                "2017/01-03/senate/001-congressional-record.md": "text\n",
            },
            "2017-01-03",
        )
        stream.commit("bound", {"2017/01-03/senate/001-congressional-record.md": "x\n"}, "b")

    day, item, shared = artifacts._record_examples(repo.path)  # noqa: SLF001

    # A day is YYYY/MM-DD, not the year: the day's own README sits one level
    # shallower than the documents and must not collapse the answer.
    assert day == "2017/01-03"
    assert item in repo.list_files("daily")
    assert shared == "2017/01-03"


def test_record_examples_omit_the_diff_when_there_is_nothing_to_compare(tmp_path) -> None:
    """A shard whose bound edition is unbuilt must not advertise `git diff`."""
    repo = GitRepo(tmp_path / "us-congress-record-119")
    repo.init()
    with repo.fast_import() as stream:
        stream.commit("daily", {"2025/01-03/senate/001-a.md": "t\n"}, "2025-01-03")

    assert artifacts._record_examples(repo.path)[2] == ""  # noqa: SLF001
