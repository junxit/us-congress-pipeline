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
