"""Tests for the link checker.

The Markdown here is generated in bulk into repositories that are published
without their pipeline, so one wrong branch in a template multiplies: linking a
repository that did not exist yet put the same 404 into thirteen repositories at
once, and nothing noticed until someone read one.
"""

from __future__ import annotations

from uscongress.jobs.links import check_document

REPOS = {"us-congress-pipeline", "us-congress-code", "us-congress-bills-113"}
FILES = {"README.md", "LICENSE", "GAPS.md"}


def _check(body: str):
    """Check one document against a fixed set of repos and files."""
    return check_document(
        body, repo="us-congress-bills-113", document="README.md", files=FILES, repos=REPOS
    )


def test_link_to_an_existing_repository_passes() -> None:
    """Navigation is the point, where navigation is possible."""
    assert not _check("See [code](https://github.com/junxit/us-congress-code).")


def test_link_to_a_repository_that_does_not_exist_fails() -> None:
    """A planned repository has no URL to point at yet."""
    broken = _check("See [statutes](https://github.com/junxit/us-congress-statutes).")

    assert len(broken) == 1
    assert broken[0].reason == "repository does not exist"


def test_link_to_a_name_template_fails() -> None:
    """``us-congress-bills-{congress}`` is a family name, not a repository.

    Linking it produces a URL with a brace in it, which resolves to nothing.
    """
    broken = _check("See [bills](https://github.com/junxit/us-congress-bills-{congress}).")

    assert len(broken) == 1
    assert broken[0].reason == "links a name template"


def test_relative_link_to_a_present_file_passes() -> None:
    """Each generated repository carries its own licence."""
    assert not _check("Terms are in [`LICENSE`](LICENSE).")


def test_relative_link_to_a_missing_file_fails() -> None:
    """A repository with no gaps has no GAPS.md, so nothing may link one."""
    broken = check_document(
        "See [gaps](GAPS.tsv).",
        repo="us-congress-bills-116",
        document="README.md",
        files={"README.md", "LICENSE"},
        repos=REPOS,
    )

    assert len(broken) == 1
    assert broken[0].reason == "no such file in this repo"


def test_anchor_to_a_real_heading_passes() -> None:
    """In-document navigation has to survive heading edits."""
    assert not _check("## Using it\n\nJump to [using it](#using-it).")


def test_anchor_to_a_missing_heading_fails() -> None:
    """A renamed heading silently breaks its own table of contents."""
    broken = _check("## Using it\n\nJump to [layout](#the-layout).")

    assert len(broken) == 1
    assert broken[0].reason == "no such heading"


def test_external_links_are_not_fetched() -> None:
    """Reaching third-party hosts turns a local check into a flaky one.

    A government site reorganising is not a failure this project can fix by
    editing a template, so those links are left alone.
    """
    assert not _check("See [govinfo](https://www.govinfo.gov) and [uv](https://x.invalid).")


def test_a_broken_link_reports_where_it_is() -> None:
    """The report has to name the document, or it cannot be acted on."""
    broken = _check("[statutes](https://github.com/junxit/us-congress-statutes)")

    assert "us-congress-bills-113/README.md" in str(broken[0])
    assert "repository does not exist" in str(broken[0])
