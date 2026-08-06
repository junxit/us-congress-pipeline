"""Tests for rendering legacy Congressional bill XML.

Fixtures are trimmed from real govinfo documents -- H.R. 588 of the 113th
Congress and its engrossed Senate amendment -- because the defects worth
guarding against were all found by rendering real bills and reading the output,
not by reasoning about the DTD.
"""

from __future__ import annotations

import pytest

from uscongress.billtext import render_bill


def _bill(body: str, form: str = "<legis-num>H. R. 588</legis-num>") -> bytes:
    """Wrap a legislative body in a minimal bill document."""
    return (
        f"<bill><form>{form}</form>"
        f"<legis-body>{body}</legis-body></bill>"
    ).encode()


def test_quote_marks_are_restored() -> None:
    """``<quote>`` delimits the exact words a bill strikes or inserts.

    Flattening it away turns "striking the 'and' after the semicolon" into
    "striking the and after the semicolon", which loses the boundary of the
    quoted term and reads as gibberish.
    """
    doc = render_bill(
        _bill(
            "<section><enum>1.</enum><header>Amendment</header>"
            "<text>Strike the <quote>and</quote> after the semicolon.</text>"
            "</section>"
        )
    )
    assert "Strike the “and” after the semicolon." in doc.markdown


def test_quoted_block_keeps_its_structure() -> None:
    """A quoted block carries the same nested levels as the bill around it.

    Flattened to one line, a long insertion is unreadable and its internal
    structure vanishes from the diff.
    """
    doc = render_bill(
        _bill(
            "<section><enum>2.</enum><header>Insert</header>"
            "<text>is amended by inserting the following:</text>"
            "<quoted-block><paragraph><enum>(7)</enum><text>Donor contributions.</text>"
            "<subparagraph><enum>(A)</enum><text>In general.</text></subparagraph>"
            "</paragraph></quoted-block>"
            "<after-quoted-block>.</after-quoted-block>"
            "</section>"
        )
    )
    assert "> - **(7)** Donor contributions." in doc.markdown
    assert ">   - **(A)** In general.." in doc.markdown  # trailer appended


def test_after_quoted_block_punctuation_is_attached() -> None:
    """The trailing punctuation closes the sentence the quotation interrupted.

    Emitted on its own line it reads as a stray full stop.
    """
    doc = render_bill(
        _bill(
            "<section><enum>1.</enum><text>amended:</text>"
            "<quoted-block><paragraph><enum>(1)</enum><text>text</text></paragraph>"
            "</quoted-block><after-quoted-block>.</after-quoted-block></section>"
        )
    )
    assert "\n.\n" not in doc.markdown


def test_amendment_containers_are_descended() -> None:
    """An engrossed amendment keeps its whole substance inside ``<amendment>``.

    Treating that element as unknown dropped the entire replacement text: the
    rendered H.R. 588 Senate amendment came out at 376 characters against 2,978
    once these containers were walked.
    """
    xml = (
        "<amendment-doc><engrossed-amendment-form>"
        "<legis-num>H.R. 588</legis-num></engrossed-amendment-form>"
        "<engrossed-amendment-body>"
        "<amendment><amendment-instruction><text>Strike all after the enacting "
        "clause and insert the following:</text></amendment-instruction>"
        "<amendment-block><section><enum>1.</enum><header>Donor contributions</header>"
        "<text>Section 8905(b) is amended.</text></section></amendment-block>"
        "</amendment></engrossed-amendment-body></amendment-doc>"
    ).encode()
    doc = render_bill(xml)
    assert "*Strike all after the enacting clause and insert the following:*" in doc.markdown
    assert "## § 1. Donor contributions" in doc.markdown
    assert "Section 8905(b) is amended." in doc.markdown


@pytest.mark.parametrize(
    ("root", "form", "body"),
    [
        ("bill", "form", "legis-body"),
        ("resolution", "form", "resolution-body"),
        ("amendment-doc", "engrossed-amendment-form", "engrossed-amendment-body"),
    ],
)
def test_all_three_document_roots_render(root: str, form: str, body: str) -> None:
    """govinfo serves three document types, named in parallel.

    They are resolved by suffix rather than an exhaustive list, so a variant
    spelling does not silently produce an empty document.
    """
    xml = (
        f"<{root}><{form}><legis-num>H.R. 1</legis-num></{form}>"
        f"<{body}><section><enum>1.</enum><header>Title</header>"
        f"<text>Body text.</text></section></{body}></{root}>"
    ).encode()
    doc = render_bill(xml)
    assert doc.legis_num == "H.R. 1"
    assert "Body text." in doc.markdown


def test_legis_num_spelling_is_normalised() -> None:
    """govinfo spells the same measure two ways across its own versions.

    The introduced text of H.R. 588 writes ``H. R. 588`` and its engrossed
    Senate amendment writes ``H.R. 588``. Left alone the frontmatter flips on
    exactly the commits whose diffs matter most.
    """
    assert render_bill(_bill("<section/>")).legis_num == "H.R. 588"


def test_legis_num_falls_back_when_absent() -> None:
    """A House amendment to a Senate amendment carries no ``legis-num``.

    It states a ``legis-type`` instead, so without a fallback one commit of an
    otherwise complete branch renders with a blank identity.
    """
    xml = (
        "<amendment-doc><engrossed-amendment-form>"
        "<legis-type>HOUSE AMENDMENT TO SENATE AMENDMENT:</legis-type>"
        "</engrossed-amendment-form><engrossed-amendment-body/></amendment-doc>"
    ).encode()
    assert render_bill(xml, legis_num="H. R. 588").legis_num == "H.R. 588"


def test_stated_number_wins_over_the_fallback() -> None:
    """The document's own statement is authoritative where it makes one."""
    assert render_bill(_bill("<section/>"), legis_num="S. 999").legis_num == "H.R. 588"


def test_resolution_preamble_is_rendered() -> None:
    """Resolutions state their rationale in ``<whereas>`` clauses."""
    xml = (
        "<resolution><form><legis-num>H. Res. 5</legis-num></form>"
        "<resolution-body><whereas><text>Whereas the sky is blue;</text></whereas>"
        "<section><enum>1.</enum><text>Resolved.</text></section>"
        "</resolution-body></resolution>"
    ).encode()
    doc = render_bill(xml)
    assert "## Preamble" in doc.markdown
    assert "- Whereas the sky is blue;" in doc.markdown


def test_nested_levels_indent_under_their_section() -> None:
    """Sections are headings; everything below them is a nested list."""
    doc = render_bill(
        _bill(
            "<section><enum>2.</enum><header>Changes</header><text>amended—</text>"
            "<paragraph><enum>(1)</enum><text>first;</text>"
            "<subparagraph><enum>(A)</enum><text>inner;</text></subparagraph>"
            "</paragraph></section>"
        )
    )
    assert "## § 2. Changes" in doc.markdown
    assert "- **(1)** first;" in doc.markdown
    assert "  - **(A)** inner;" in doc.markdown


def test_unrecognised_root_is_rejected() -> None:
    """A PLAW or other collection document must not render as an empty bill."""
    with pytest.raises(ValueError, match="unrecognised bill document root"):
        render_bill(b"<pLaw><form/></pLaw>")


def test_unescaped_ampersand_still_renders() -> None:
    """Bill text is repaired before parsing, as the US Code build does.

    Six measures in the 113th Congress alone are unparseable as published.
    """
    xml = (
        "<bill><form><legis-num>S. 1339</legis-num></form><legis-body>"
        "<section><enum>1.</enum><header>Lawrence, Denny, & Scarbrough</header>"
        "<text>Smith & Wesson.</text></section></legis-body></bill>"
    ).encode()
    doc = render_bill(xml)
    assert "Lawrence, Denny, & Scarbrough" in doc.markdown
    assert "Smith & Wesson." in doc.markdown
