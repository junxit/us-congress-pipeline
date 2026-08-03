"""Tests for the USLM to Markdown renderer.

The load-bearing property is *diff stability*: identical law must render to
identical bytes across release points, or every commit shows spurious changes
and the repository is worthless.
"""

from __future__ import annotations

from uscongress.render import USLM_1_0, render_title

_SECTION = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{ns}">
  <main>
    <title>
      <num value="1">Title 1—</num>
      <chapter>
        <num value="1">CHAPTER 1—</num>
        <section {sid} identifier="/us/usc/t1/s2" style="-uslm-lc:I80">
          <num value="2">&#167; 2.</num>
          <heading> "County" as including "parish"</heading>
          <content>
            <p class="indent0" style="-uslm-lc:I11">The word "county" includes
            a parish.</p>
          </content>
          <sourceCredit>(July 30, 1947, ch. 388, 61 Stat. 633.)</sourceCredit>
        </section>
      </chapter>
    </title>
  </main>
</uscDoc>
"""


def _doc(section_id: str) -> bytes:
    """Build a one-section USLM document carrying the given generated id."""
    return _SECTION.format(ns=USLM_1_0, sid=f'id="{section_id}"').encode()


def test_generated_ids_do_not_affect_output() -> None:
    """A changed ``id`` UUID must not change a single byte of output.

    OLRC regenerates these on every release point even when the text is
    unchanged -- measured at 39 of 40 sections in Title 1. If they leaked into
    the Markdown, nearly every section would look modified in every commit.
    """
    first = render_title(_doc("idac4b642b-47c4-11f1-8df3-c75a02f6a58e"))
    second = render_title(_doc("ide0bf2aff-259a-11ee-829b-a5a403e3639c"))
    assert first[0].markdown == second[0].markdown
    assert "idac4b642b" not in first[0].markdown


def test_whitespace_reflow_is_normalised() -> None:
    """Re-wrapped source XML must not register as a textual change."""
    reflowed = _SECTION.format(
        ns=USLM_1_0, sid='id="x"'
    ).replace('The word "county" includes\n            a parish.',
              'The word "county" includes a parish.')
    baseline = render_title(_doc("x"))
    assert render_title(reflowed.encode())[0].markdown == baseline[0].markdown


def test_section_metadata_and_path() -> None:
    """Identifier, numbering and file path come out as expected."""
    (section,) = render_title(_doc("x"))
    assert section.identifier == "/us/usc/t1/s2"
    assert section.title == "1"
    assert section.chapter == "1"
    assert section.num == "2"
    assert section.path == "title-01/chapter-1/sec-2.md"
    assert "identifier: /us/usc/t1/s2" in section.markdown
    assert "## Source credit" in section.markdown


def test_presentational_attributes_are_dropped() -> None:
    """``style`` and ``class`` are layout, not law, and must not be emitted."""
    (section,) = render_title(_doc("x"))
    assert "uslm-lc" not in section.markdown
    assert "indent0" not in section.markdown


def test_non_numeric_section_numbers_are_slugged() -> None:
    """Section numbers such as ``1395x`` and ``2000e-2`` occur and must be safe."""
    doc = _SECTION.format(ns=USLM_1_0, sid='id="x"').replace(
        '<num value="2">&#167; 2.</num>', '<num value="2000e-2">&#167; 2000e-2.</num>'
    )
    (section,) = render_title(doc.encode())
    assert section.path.endswith("sec-2000e-2.md")
    assert "/" not in section.path.rpartition("/")[2]


def test_quoted_sections_in_notes_are_not_emitted() -> None:
    """Notes reproduce other statutes; that text is not US Code sections.

    Emitting them invents files, and walking their ancestry picks up the quoted
    act's "TITLE I" as if it were a Code title.
    """
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}">
  <main><title><num value="5">Title 5</num><chapter><num value="35">CHAPTER 35</num>
    <section identifier="/us/usc/t5/s3591"><num value="3591">&#167; 3591.</num>
      <heading>Real section</heading>
      <notes><note><p><quotedContent>
        <title><num value="I">TITLE I</num>
          <section><num value="1">SEC. 1.</num><heading>SHORT TITLE.</heading></section>
        </title>
      </quotedContent></p></note></notes>
    </section>
  </chapter></title></main>
</uscDoc>"""
    sections = render_title(doc.encode())
    assert len(sections) == 1
    assert sections[0].identifier == "/us/usc/t5/s3591"
    assert all(not s.path.startswith("title-i") for s in sections)


def test_sections_without_identifiers_are_kept() -> None:
    """OLRC omits @identifier on plenty of genuine sections.

    Title 42 at release point 113-44 has 747 such sections sitting directly
    under subchapter/chapter/title. Filtering on the identifier drops real law.
    """
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}">
  <main><title><num value="42">Title 42</num><chapter><num value="7">CHAPTER 7</num>
    <section><num value="1305">&#167; 1305.</num><heading>No identifier here</heading></section>
  </chapter></title></main>
</uscDoc>"""
    (section,) = render_title(doc.encode())
    assert section.identifier == ""
    assert section.path == "title-42/chapter-7/sec-1305.md"


def test_duplicate_section_numbers_both_survive() -> None:
    """The Code really does contain two sections sharing a number.

    Congress enacted two 5 U.S.C. 3598, and the Code says "Another section 3598
    is set out after this one". They share an identifier too, so nothing
    distinguishes them but document order. Both are law; neither may be lost.
    """
    from uscongress.render import to_file_map

    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}">
  <main><title><num value="5">Title 5</num><chapter><num value="35">CHAPTER 35</num>
    <section identifier="/us/usc/t5/s3598"><num value="3598">&#167; 3598.</num>
      <heading>First</heading></section>
    <section identifier="/us/usc/t5/s3598"><num value="3598">&#167; 3598.</num>
      <heading>Second</heading></section>
  </chapter></title></main>
</uscDoc>"""
    files = to_file_map(render_title(doc.encode()))
    assert len(files) == 2
    assert "title-05/chapter-35/sec-3598.md" in files
    assert "title-05/chapter-35/sec-3598-2.md" in files
    assert "First" in files["title-05/chapter-35/sec-3598.md"]
    assert "Second" in files["title-05/chapter-35/sec-3598-2.md"]


def test_appendix_documents_resolve_their_title() -> None:
    """Appendix documents have no <title> element at all.

    usc05A/usc11a/usc18a/usc28a/usc50A hold <appendix> under <uscDoc>, and their
    sections carry empty identifiers. Without a document-level fallback, 69
    sections of real law landed in a title-00 bucket -- which the truncation
    guard then froze from December 2022 onward, because OLRC never declares
    title 0 as affected.
    """
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}" identifier="/us/usc/t5a">
  <main><appendix><num value="5a">Appendix</num>
    <compiledAct>
      <section><num value="16">SEC. 16.</num><heading>Effective date</heading></section>
    </compiledAct>
  </appendix></main>
</uscDoc>"""
    (section,) = render_title(doc.encode())
    assert section.title == "5a"
    assert section.path == "title-05a/sec-16.md"


def test_document_title_beats_nested_act_divisions() -> None:
    """A compiled act's "TITLE IV" is not a US Code title.

    Walking ancestors finds it first and files real law under title-iv/. Each
    document is exactly one title, so the document-level answer must win.
    """
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}" identifier="/us/usc/t18a">
  <main><appendix><num value="18a">Appendix</num>
    <compiledAct>
      <title><num value="IV">TITLE IV</num>
        <section><num value="401">SEC. 401.</num><heading>Rules</heading></section>
      </title>
    </compiledAct>
  </appendix></main>
</uscDoc>"""
    (section,) = render_title(doc.encode())
    assert section.title == "18a"
    assert section.path.startswith("title-18a/")


def test_default_title_covers_documents_without_a_root_identifier() -> None:
    """usc50A.xml is the one document whose root carries no identifier."""
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="{USLM_1_0}" identifier="">
  <main><appendix>
    <section><num value="7">SEC. 7.</num><heading>Scope</heading></section>
  </appendix></main>
</uscDoc>"""
    (section,) = render_title(doc.encode(), default_title="50a")
    assert section.path == "title-50a/sec-7.md"


def test_appendix_titles_sort_beside_their_base_title() -> None:
    """title-05a must pad like title-05, not sort to the end as title-5a."""
    from uscongress.render import _pad_title

    assert _pad_title("5") == "05"
    assert _pad_title("5a") == "05a"
    assert _pad_title("26") == "26"
    assert _pad_title("18a") == "18a"
