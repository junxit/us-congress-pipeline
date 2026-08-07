"""Tests for the Statutes at Large build.

Every fixture below is trimmed from a real ``STATUTE-N.xml``, and almost all of
them exist because of one thing: **the Statutes at Large prints marginalia inside
the sentence.** A ``<sidenote>``, a ``<page>`` and a ``<centerRunningHead>`` all
sit between two words of the text, so the ``itertext()`` flatten that is correct
for the US Code splices a marginal note between "United" and "States". There are
5,293 sidenotes in volume 1 alone and 3,718 in volume 117, so this is not an edge
case -- it is most of the corpus, and it is silent: the output is fluent English
that says something the law does not.

The rest cover the two upstream traps that cost real volumes. govinfo serves
three of the 137 volumes as an HTML error page at HTTP 200 unless the request
carries ``Accept: application/xml``, and git refuses outright to store a
timestamp from 1789.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import date

import pytest

from uscongress import config
from uscongress.gitbuild import GitRepo
from uscongress.jobs.statutes import (
    EPOCH,
    Volume,
    VolumeUnavailable,
    _write_gaps,
    commit_date,
    commit_message,
    discover,
    fetch_volume,
    looks_like_xml,
    seed,
)
from uscongress.statutetext import render_volume, to_file_map

_ROOT = (
    '<statutesAtLarge xmlns="http://schemas.gpo.gov/xml/uslm" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
)


def _volume(body: str, meta: str = "<volume>1</volume>") -> bytes:
    """Wrap law fragments in a minimal Statutes at Large document."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"{_ROOT}<meta>{meta}</meta><main>{body}</main></statutesAtLarge>"
    ).encode()


def _law(
    body: str,
    number: str = "I",
    doc_type: str = "Chapter",
    congress: str = "1",
    session: str = "1",
    scope: str = "public",
    cites: str = "<citableAs>1 Stat. 23</citableAs>",
    tag: str = "pLaw",
) -> str:
    """One law element, with the meta block GPO actually writes."""
    return (
        f"<component><{tag}><meta>"
        f"<dc:type>{doc_type}</dc:type><docNumber>{number}</docNumber>{cites}"
        f"<congress>{congress}</congress><session>{session}</session>"
        f"<publicPrivate>{scope}</publicPrivate>"
        f"</meta><main>{body}</main></{tag}></component>"
    )


#: Section 1 of the 1789 oath act, 1 Stat. 23, trimmed to the words either side
#: of the marginal note. The note opens mid-word-boundary between "United" and
#: "States"; nothing in the markup separates it from the sentence.
_OATH = _law(
    '<longTitle><docTitle>An Act </docTitle>'
    "<officialTitle>to regulate the Time and Manner of administering certain "
    "Oaths.</officialTitle>"
    '<sidenote><p><approvedDate date="1789-06-01">June 1, 1789</approvedDate>.</p>'
    "</sidenote></longTitle>"
    '<section><num value="1">Sec. 1. </num><content>'
    "<enactingFormula>Be it enacted by the Senate and Representatives of the "
    "United States of America in Congress assembled,</enactingFormula>"
    " That the oath required by the sixth article of the Constitution of the "
    "United<sidenote><p>Form of the oath or affirmation to support the "
    "Constitution of the United States.</p></sidenote> States, shall be "
    "administered in the form following"
    "<centerRunningHead>FIRST CONGRESS. Sess. I. Ch. 2. 1789.</centerRunningHead>"
    '<page identifier="/us/stat/1/24">24</page>, to wit.'
    "</content></section>"
)


def test_a_marginal_note_is_not_part_of_the_sentence() -> None:
    """This is the whole reason USLM 2.0 gets its own renderer.

    ``<sidenote>`` sits between two words of the text -- here between "United"
    and "States" -- so ``itertext()`` produces "...the Constitution of the United
    Form of the oath or affirmation... States, shall be administered...". That is
    fluent, plausible English which is not what the law says, and there are 5,293
    sidenotes in volume 1 alone for it to happen in.
    """
    (law,) = render_volume(_volume(_OATH), 1).laws

    assert "Constitution of the United States, shall be administered" in law.markdown
    assert "United Form of the oath" not in law.markdown


def test_page_numbers_and_running_heads_are_not_text() -> None:
    """A page break is printed at the fold, not spoken in the sentence.

    ``<page>`` and ``<centerRunningHead>`` interrupt clauses exactly as sidenotes
    do, so an unfiltered flatten drops "24 FIRST CONGRESS. Sess. I. Ch. 2. 1789."
    into the middle of the oath.
    """
    (law,) = render_volume(_volume(_OATH), 1).laws

    assert "FIRST CONGRESS. Sess. I." not in law.markdown
    assert "in the form following, to wit." in law.markdown


def test_the_marginal_notes_are_kept_rather_than_discarded() -> None:
    """Moving the notes out of the text must not mean losing them.

    They carry the classification of the provision -- ``26 USC 3304 note`` -- and
    the originating bill, which is the only automatic join between an enacted law
    and the measure that produced it.
    """
    (law,) = render_volume(_volume(_OATH), 1).laws

    assert "## Marginal notes" in law.markdown
    assert "Form of the oath or affirmation to support the Constitution" in law.markdown


#: PL 108-1's subsection heading, verbatim: GPO opens a sidenote in the middle of
#: the words "In General".
_SPLIT_HEADING = _law(
    '<section><num value="1">SECTION 1. </num><heading>EXTENSION.</heading>'
    '<subsection><num value="a">(a) </num>'
    "<heading>In <sidenote><p>26 USC 3304 note.</p></sidenote>General.—</heading>"
    "<content>Section 208 is amended.</content></subsection></section>",
    number="1",
    doc_type="Public Law",
    congress="108",
    cites="<citableAs>Public Law 108–1</citableAs><citableAs>117 Stat. 3</citableAs>",
)


def test_a_heading_split_by_a_sidenote_reads_correctly() -> None:
    """Headings are hit as often as prose, and the damage is more visible.

    ``In <sidenote>26 USC 3304 note.</sidenote>General.—`` flattens to
    "In 26 USC 3304 note. General.—", which reads as a heading naming a US Code
    section the subsection does not amend.
    """
    (law,) = render_volume(_volume(_SPLIT_HEADING), 117).laws

    assert "*In General.—*" in law.markdown
    assert "In 26 USC 3304 note. General" not in law.markdown


def test_a_line_break_separates_words() -> None:
    """``<br/>`` carries no text, so without a substitute the lines run together.

    Volume 1 prints the Declaration's heading as ``THE UNANIMOUS DECLARATION OF
    THE THIRTEEN<br/>UNITED STATES OF AMERICA``, which concatenates to
    "THIRTEENUNITED".
    """
    body = "<content><heading>THE THIRTEEN<br/>UNITED STATES</heading></content>"
    (law,) = render_volume(_volume(_law(body)), 1).laws

    assert "THE THIRTEEN UNITED STATES" in law.markdown


def test_the_originating_bill_is_lifted_into_the_frontmatter() -> None:
    """Every one of volume 117's 198 public laws links the bill it came from.

    ``/us/bill/108/s/23`` is exactly branch ``s-23`` of ``us-congress-bills-108``,
    so recording it turns two repositories into one navigable set. Buried in a
    sidenote it is unusable.
    """
    body = (
        "<longTitle><officialTitle>An Act</officialTitle>"
        '<sidenote><p>[<ref href="/us/bill/108/s/23">S. 23</ref>]</p></sidenote>'
        "</longTitle><content>That it is so.</content>"
    )
    (law,) = render_volume(_volume(_law(body)), 117).laws

    assert law.bills == ("108/s-23",)
    assert "bills: 108/s-23" in law.markdown


def test_a_law_with_no_section_still_has_a_body() -> None:
    """1 Stat. 573 writes its whole operative text as ``<content>`` under ``<main>``.

    Inside a subsection a ``<content>`` is that subsection's own prose and is
    folded into its line; directly under ``<main>`` it is the entire act. Treating
    it the same way in both places left the 1798 act punishing frauds on the Bank
    of the United States with a title, an enacting formula and no law.
    """
    body = (
        "<enactingFormula>Be it enacted,</enactingFormula>"
        "<content>That if any person shall forge any bill of the Bank of the "
        "United States, every such person shall be deemed guilty of felony."
        "</content>"
    )
    (law,) = render_volume(_volume(_law(body)), 1).laws

    assert "shall be deemed guilty of felony" in law.markdown


def test_quoted_text_keeps_its_structure_and_its_closing_punctuation() -> None:
    """A ``<quotedContent>`` is the words a law inserts, not an instruction.

    Collapsed to one line its internal structure disappears; and its tail -- the
    ``.`` closing the sentence the quotation interrupted -- belongs on the end of
    the quote, not dangling after the lead as "to read as follows:."
    """
    body = (
        "<section><content>Section 208 is amended to read as follows:"
        "<quotedContent><section><num value=\"208\">“SEC. 208. </num>"
        "<heading>APPLICABILITY.</heading>"
        "<content>An agreement shall apply.”</content></section>"
        "</quotedContent>.</content></section>"
    )
    (law,) = render_volume(_volume(_law(body)), 117).laws

    assert "to read as follows:" in law.markdown
    assert "to read as follows:." not in law.markdown
    assert "> ## § 208. APPLICABILITY." in law.markdown
    assert "> An agreement shall apply.”." in law.markdown


def test_a_table_stays_a_table() -> None:
    """Volume 65 alone carries 12,778 table cells, nearly all appropriations.

    Flattened into a sentence, a schedule of accounts and amounts becomes an
    unreadable run of figures with nothing saying which sum belongs to which
    account.
    """
    body = (
        "<content>The following rates apply:"
        '<table xmlns="http://www.w3.org/1999/xhtml">'
        "<tr><th>Distilled Spirits</th><th>$9 per gallon.</th></tr>"
        "<tr><td>Still Wines</td><td>15 cents per gallon.</td></tr>"
        "</table></content>"
    )
    (law,) = render_volume(_volume(_law(body)), 65).laws

    assert "| Distilled Spirits | $9 per gallon. |" in law.markdown
    assert "| Still Wines | 15 cents per gallon. |" in law.markdown


def test_a_contents_entry_keeps_its_title() -> None:
    """The Revenue Act of 1951's contents are 400 designator-plus-label pairs.

    ``<designator>`` holds "Sec. 101." and ``<label>`` holds what section 101
    does. Reading only the designator renders the whole table as a bare list of
    section numbers with every title dropped.
    """
    body = (
        "<content><toc><referenceItem><designator>Sec. 101.</designator>"
        "<label>Increase in surtax for 1951.</label></referenceItem></toc></content>"
    )
    (law,) = render_volume(_volume(_law(body)), 65).laws

    assert "- Sec. 101. Increase in surtax for 1951." in law.markdown


def test_the_date_comes_from_the_attribute_not_only_the_meta() -> None:
    """Volumes 1 to 63 put no ``<approvedDate>`` in ``<meta>`` at all.

    The date is on the ``date`` attribute of the ``<approvedDate>`` printed in the
    margin, which covers 479 of volume 1's 493 laws. Reading only ``<meta>`` would
    leave every law before 1950 undated.
    """
    body = (
        "<longTitle><sidenote><p>"
        '<approvedDate date="1789-06-01">June 1, 1789</approvedDate>.'
        "</p></sidenote></longTitle><content>That it is so.</content>"
    )
    (law,) = render_volume(_volume(_law(body)), 1).laws

    assert law.approved == date(1789, 6, 1)


def test_an_impossible_date_is_rejected_rather_than_published() -> None:
    """Four laws in the corpus carry a date that cannot be true.

    Volume 32 dates one to 16 April 1110 and volume 34 dates three to 1007, in
    volumes covering 1901-1903 and 1905-1907. Taken as fact they would set the
    volume's own date and, with it, the order of the whole history.
    """
    body = (
        "<longTitle><sidenote><p>"
        '<approvedDate date="1007-01-30">January 30, 1907</approvedDate>'
        "</p></sidenote></longTitle><content>That it is so.</content>"
    )
    render = render_volume(_volume(_law(body)), 34)

    assert render.laws[0].approved is None
    assert render.latest is None
    assert render.undated == 1


def test_duplicate_citations_get_a_file_each() -> None:
    """Volume 1 prints two Chapter IIIs in the 1st Congress's first session.

    Volume 65 does the same with House Concurrent Resolution 98. Both are law, so
    neither may be dropped -- and a dict keyed on the citation would silently keep
    only the second.
    """
    document = _volume(
        _law("<content>The first.</content>", number="III")
        + _law("<content>The second.</content>", number="III")
    )
    laws = render_volume(document, 1).laws
    files = to_file_map(laws)

    assert len(files) == 2
    assert sorted(files) == [
        "volume-001/public/chapter-1-1-iii-2.md",
        "volume-001/public/chapter-1-1-iii.md",
    ]
    assert "The first." in files["volume-001/public/chapter-1-1-iii.md"]


def test_private_laws_are_filed_apart_from_public_ones() -> None:
    """A private act relieves one named person; it is not general law.

    In the volumes sampled they outnumber the public acts 6,238 to 3,011, so
    filing them together would bury the general law inside the relief acts.
    """
    document = _volume(
        _law("<content>Public.</content>", number="1", doc_type="Public Law")
        + _law(
            "<content>Private.</content>",
            number="1",
            doc_type="Private Law",
            scope="private",
        )
        + _law(
            "<content>Resolved.</content>",
            number="8",
            doc_type="House Concurrent Resolution",
            scope="",
            tag="resolution",
        )
    )
    paths = sorted(to_file_map(render_volume(document, 65).laws))

    assert paths == [
        "volume-065/private/private-law-1-1.md",
        "volume-065/public/public-law-1-1.md",
        "volume-065/resolutions/house-concurrent-resolution-1-8.md",
    ]


def test_treaties_are_counted_and_left_out() -> None:
    """Volumes 7 and 8 contain nothing but treaties -- no session law at all.

    13,387 ``<presidentialDoc>`` elements exist across the corpus. A treaty is
    made by Senate ratification, not by bicameral passage and presentment, so it
    is a different instrument; but an unexplained hole at volumes 7 and 8 reads as
    a build that failed, which is why the count is returned rather than ignored.
    """
    document = _volume(
        '<collection role="appendix"><component>'
        "<presidentialDoc><main><content>A treaty.</content></main>"
        "</presidentialDoc></component></collection>"
    )
    render = render_volume(document, 7)

    assert render.laws == ()
    assert render.presidential == 1


def test_a_byte_order_mark_is_not_a_reason_to_reject_a_volume() -> None:
    """51 of the 137 volumes begin ``ef bb bf`` before ``<?xml``.

    ``bytes.lstrip()`` does not remove a BOM, because a BOM is not whitespace, so
    the prefix check the bills job uses would reject over a third of this
    collection as "not XML" and leave 137 volumes looking like 86.
    """
    assert looks_like_xml(b"\xef\xbb\xbf<?xml version='1.0'?><statutesAtLarge/>")
    assert looks_like_xml(b"\n<?xml version='1.0'?><statutesAtLarge/>")
    assert not looks_like_xml(b"<!DOCTYPE html>\n<html><body>Error</body></html>")


class _ErrorPageClient:
    """govinfo answering a bulk-data failure the way it really does."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str] | None] = []

    async def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        self.headers.append(headers)
        return b"<!DOCTYPE html>\n<title>Govinfo Bulkdata Service Error</title>"


def test_an_error_page_is_rejected_rather_than_cached(tmp_path, monkeypatch) -> None:
    """``STATUTE/107`` answers 200 with 67,225 bytes of HTML, not a 404.

    The directory listing for the same path advertises 13.7 MB of
    ``application/xml``, so the status code and the listing both say the file is
    there. Cached unchecked, that poisons three volumes and 664 laws permanently,
    and the build reports success.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    client = _ErrorPageClient()

    with pytest.raises(VolumeUnavailable, match="not XML"):
        asyncio.run(fetch_volume(client, Volume(107, "https://example.invalid/x")))

    assert not (tmp_path / "statutes" / "STATUTE-107.xml").exists()
    # The header is what makes those three volumes resolve at all.
    assert client.headers == [{"Accept": "application/xml"}]


def test_a_poisoned_cache_entry_is_discarded_and_refetched(tmp_path, monkeypatch) -> None:
    """A cache written before the check existed must not be trusted for ever.

    The cache is keyed only on the volume number, so one bad write is permanent:
    every later run reads the error page back off disk and never asks govinfo
    again.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    poisoned = tmp_path / "statutes" / "STATUTE-107.xml"
    poisoned.parent.mkdir(parents=True)
    poisoned.write_bytes(b"<!DOCTYPE html>\n<title>Govinfo Bulkdata Service Error</title>")

    class _Client:
        async def get_bytes(self, url: str, headers=None) -> bytes:
            return b"<?xml version='1.0'?><statutesAtLarge/>"

    payload = asyncio.run(fetch_volume(_Client(), Volume(107, "https://example.invalid/x")))

    assert payload.startswith(b"<?xml")
    assert poisoned.read_bytes().startswith(b"<?xml")


def test_the_resources_folder_is_not_a_volume() -> None:
    """The listing returns 138 entries for 137 volumes.

    The extra one is ``resources``, holding ``readme.html`` and ``lockss.html``.
    Taken as a volume it would be fetched as ``STATUTE-resources.xml``, which does
    not exist, and reported as a missing volume for ever.
    """

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Client:
        async def list_bulkdata(self, path: str):
            return [_Entry("137"), _Entry("resources"), _Entry("1")]

    volumes = asyncio.run(discover(_Client()))

    assert [v.number for v in volumes] == [1, 137]
    assert volumes[1].url.endswith("/STATUTE/137/STATUTE-137.xml")
    assert volumes[0].tag == "stat-001"


def _render_of(latest: date | None):
    """A VolumeRender stub carrying only the dates a commit needs."""
    return render_volume(
        _volume(
            _law(
                "<longTitle><sidenote><p>"
                f'<approvedDate date="{latest.isoformat()}">x</approvedDate>'
                "</p></sidenote></longTitle><content>That it is so.</content>"
            )
            if latest
            else _law("<content>That it is so.</content>")
        ),
        1,
    )


def test_pre_epoch_dates_are_clamped_because_git_refuses_them() -> None:
    """git will not store 1799-03-03. It exits 128, ``invalid date format``.

    ``fast-import`` accepts a negative timestamp only for ``git log --date=iso``
    to render it as an empty string, which is worse -- a blank date reads as a
    broken build. 82 of the 137 volumes close before 1970, so this decides most of
    the history.
    """
    assert commit_date(_render_of(date(1799, 3, 3))) == EPOCH
    assert commit_date(_render_of(date(2003, 12, 19))) == date(2003, 12, 19)
    assert commit_date(_render_of(None)) == EPOCH


def test_the_subject_line_carries_the_years_the_commit_cannot() -> None:
    """With the timestamp clamped, ``git log --oneline`` is the only chronology.

    A reader running ``git log --date=short`` over volumes 1 to 82 sees
    1970-01-01 eighty-two times, so the years have to be somewhere a clamp cannot
    reach.
    """
    message = commit_message(
        Volume(1, "https://example.invalid/x"), _render_of(date(1799, 3, 3))
    )

    assert message.startswith("Statutes at Large, volume 1 (1799)")
    assert "Dated:    1970-01-01, not 1799-03-03" in message
    assert "Approved: 1799-03-03 to 1799-03-03" in message


class _VolumeClient:
    """Serves fixed volume bytes, and records what was asked for."""

    def __init__(self, bodies: dict[int, bytes]) -> None:
        self._bodies = bodies
        self.fetched: list[int] = []

    class _Entry:
        def __init__(self, name: str) -> None:
            self.name = name

    async def list_bulkdata(self, path: str):
        return [self._Entry(str(n)) for n in sorted(self._bodies)]

    async def get_bytes(self, url: str, headers=None) -> bytes:
        number = int(url.rsplit("STATUTE-", 1)[1].removesuffix(".xml"))
        self.fetched.append(number)
        return self._bodies[number]


def _bodies() -> dict[int, bytes]:
    """Two volumes: one with a law, one that prints only treaties."""
    return {
        1: _volume(_law("<content>That it is so.</content>")),
        7: _volume(
            "<component><presidentialDoc><main><content>A treaty.</content>"
            "</main></presidentialDoc></component>"
        ),
    }


def test_a_volume_with_no_session_laws_is_neither_committed_nor_tagged(
    tmp_path, monkeypatch
) -> None:
    """Volumes 7 and 8 print treaties and nothing else.

    Committing an empty tree would add a commit that says nothing; tagging without
    committing would leave ``stat-007`` pointing at volume 6's text, which is a
    straightforwardly false claim about what 7 Stat. contains.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    repo = asyncio.run(
        seed(_VolumeClient(_bodies()), repo_path=tmp_path / "us-congress-statutes")
    )

    tags = repo._run("tag").split()  # noqa: SLF001 - the tag list is the assertion
    assert tags == ["stat-001"]
    assert repo.list_files("main") == {"volume-001/public/chapter-1-1-i.md", "GAPS.md"}


def test_a_second_run_builds_nothing_and_fetches_nothing(tmp_path, monkeypatch) -> None:
    """Resumption keys on the tag, exactly as ``seed-code`` does.

    A finished corpus is 2.3 GB and 101,975 laws; re-running has to cost one
    listing call, not a rebuild. The check must come before the fetch, or a
    resume downloads everything to discover it has nothing to do.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    path = tmp_path / "us-congress-statutes"
    asyncio.run(seed(_VolumeClient(_bodies()), repo_path=path))

    client = _VolumeClient(_bodies())
    repo = asyncio.run(seed(client, repo_path=path))

    # Volume 7 has no tag to skip on, so it is re-read; volume 1 is not.
    assert 1 not in client.fetched
    assert repo.commit_count() == 2  # the volume, and the GAPS record


def test_a_second_run_makes_no_commit(tmp_path, monkeypatch) -> None:
    """``GAPS.md`` must be a pure function of what was found, or it churns.

    Rewritten with a different byte on every run it would add a commit every day
    for ever, and each one would show up as a change to a repository nothing had
    actually changed.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    path = tmp_path / "us-congress-statutes"
    asyncio.run(seed(_VolumeClient(_bodies()), repo_path=path))
    before = GitRepo(path)._run("rev-parse", "HEAD")  # noqa: SLF001

    repo = asyncio.run(seed(_VolumeClient(_bodies()), repo_path=path))

    assert repo._run("rev-parse", "HEAD") == before  # noqa: SLF001


def test_gaps_describe_the_whole_corpus_not_only_this_run(tmp_path) -> None:
    """A resumed build that touched three volumes must not shrink ``GAPS.md``.

    The counts come from a sidecar recording every volume ever read, kept beside
    the repository like ``uscode``'s tree manifest, because a build that stops at
    volume 40 and restarts would otherwise publish a gap record claiming the
    corpus holds 40 volumes.
    """
    repo = GitRepo(tmp_path / "us-congress-statutes")
    repo.init()
    state = {
        "7": {"laws": 0, "presidential": 262, "undated": 0, "repair": ""},
        "117": {"laws": 239, "presidential": 113, "undated": 41, "repair": ""},
    }

    assert _write_gaps(repo, state)
    repo.commit("Record gaps", when=None)
    text = subprocess.run(
        ["git", "-C", str(repo.path), "show", "main:GAPS.md"],
        capture_output=True,
        text=True,
    ).stdout

    assert "375" in text  # 262 + 113 treaties, across both volumes
    assert "| 7 | 262 |" in text
    assert "41 laws carry no usable date" in text
    # And rewriting the same state must change nothing.
    assert not _write_gaps(repo, state)


def test_the_sidecar_records_every_volume_seen(tmp_path, monkeypatch) -> None:
    """The count of laws rendered and the count of files on ``main`` are checked.

    They are arrived at independently -- one from the renderer, one from git -- so
    a law that renders but never lands, or a file that lands twice, shows up as a
    mismatch instead of as a smaller repository nobody counted.
    """
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    path = tmp_path / "us-congress-statutes"
    asyncio.run(seed(_VolumeClient(_bodies()), repo_path=path))

    state = json.loads(
        (path.parent / ".us-congress-statutes.volumes.json").read_text(encoding="utf-8")
    )

    assert state["1"]["laws"] == 1
    assert state["7"] == {
        "laws": 0,
        "presidential": 1,
        "undated": 0,
        "repair": "",
        "buckets": {},
    }
