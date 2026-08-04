"""Tests for telling an editorially retired title from a truncated archive.

Both look identical to a section count: a title loses most of its content and
OLRC does not declare it as affected. Freezing the wrong one either invents a
mass deletion or asserts law that has been repealed, so the fixtures below are
modelled on the two real cases -- Title 50 Appendix, eliminated in 2015, and
Title 5 Appendix, gutted by Pub. L. 117-286 in 2022 -- against the truncation
of ``usc46.xml`` at 113-44 that the carry-forward guard exists for.
"""

from __future__ import annotations

from uscongress.jobs.uscode import is_editorially_retired, repair_truncated_titles

NS = "http://xml.house.gov/schemas/uslm/1.0"


def _doc(body: str) -> bytes:
    """Wrap USLM body markup in a minimal conforming document."""
    return f'<uscDoc xmlns="{NS}">{body}</uscDoc>'.encode()


def _section(num: str, status: str = "") -> str:
    """One section, optionally carrying a disposition status."""
    attr = f' status="{status}"' if status else ""
    return f'<section{attr}><num value="{num}">§ {num}.</num><heading>H</heading></section>'


def test_all_sections_disposed_is_retirement() -> None:
    """Title 5 Appendix: 19 sections remain and every one is disposed of.

    USLM leaves operative text with no ``status`` attribute, so a title where
    every survivor carries one has nothing left in force.
    """
    doc = _doc(
        _section("1", "transferred")
        + _section("2 to 15", "repealed")
        + _section("16", "omitted")
    )
    assert is_editorially_retired(doc) is True


def test_one_live_section_is_not_retirement() -> None:
    """A single section still in force means the title is alive.

    This is the conservative direction: anything short of a complete teardown
    falls back to carrying the previous snapshot forward.
    """
    doc = _doc(
        _section("1", "repealed") + _section("2", "transferred") + _section("3")
    )
    assert is_editorially_retired(doc) is False


def test_empty_document_with_elimination_marker_is_retirement() -> None:
    """Title 50 Appendix: no sections, and the heading says why."""
    doc = _doc(
        "<appendix><num value='50a'>Title 50—APPENDIX</num>"
        "<heading>WAR AND NATIONAL DEFENSE [ELIMINATED] Current through 114–86u1</heading>"
        "</appendix>"
    )
    assert is_editorially_retired(doc) is True


def test_empty_document_without_marker_is_not_retirement() -> None:
    """An empty archive with no explanation is indistinguishable from a bad one.

    A truncated download can also yield zero sections, so absent an explicit
    marker the safe reading is truncation.
    """
    doc = _doc("<appendix><heading>WAR AND NATIONAL DEFENSE</heading></appendix>")
    assert is_editorially_retired(doc) is False


def test_elimination_marker_elsewhere_does_not_count() -> None:
    """``usc46.xml`` mentions ELIMINATED in notes while holding 912 sections.

    The marker only settles the question when there is no section left, so a
    populated title is never retired on the strength of a stray note.
    """
    doc = _doc(
        "<note><p>Provisions were [ELIMINATED] by a prior Act.</p></note>"
        + _section("1")
    )
    assert is_editorially_retired(doc) is False


def test_unparseable_document_is_not_retirement() -> None:
    """Damage must not be read as an intentional teardown."""
    assert is_editorially_retired(b"<uscDoc><section") is False


def _files(folder: str, count: int) -> dict[str, str]:
    """A file map with ``count`` sections under one title directory."""
    return {f"{folder}/sec-{n}.md": f"body {n}" for n in range(count)}


def test_retired_title_is_not_carried_forward() -> None:
    """The drop is committed as the real change it is."""
    previous = _files("title-50a", 538)
    files, repaired = repair_truncated_titles(
        {}, previous, declared=(), retired={"title-50a"}
    )
    assert files == {}
    assert repaired == []


def test_truncated_title_is_still_carried_forward() -> None:
    """Regression guard: the 113-44 case must keep working.

    ``usc46.xml`` drops 912 sections to 576 with no retirement signal, so the
    previous snapshot has to survive or the history gains 336 false repeals.
    """
    previous = _files("title-46", 912)
    files, repaired = repair_truncated_titles(
        _files("title-46", 576), previous, declared=(), retired=set()
    )
    assert len(files) == 912
    assert repaired == ["title-46 (912 -> 576 sections)"]


def test_declared_title_still_wins_over_retirement() -> None:
    """A title OLRC declares as affected is believed regardless."""
    previous = _files("title-46", 912)
    files, repaired = repair_truncated_titles(
        _files("title-46", 576), previous, declared=(46,), retired=set()
    )
    assert len(files) == 576
    assert repaired == []
