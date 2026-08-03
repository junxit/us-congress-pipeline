"""Tests for repairing structurally broken OLRC archives.

Fixtures mirror the real defect in ``usc16.xml`` at release point 113-46.
"""

from __future__ import annotations

from uscongress.xmlrepair import repair


def test_well_formed_input_is_untouched() -> None:
    """A valid document must be returned byte-identical."""
    data = b"<a><b>text</b></a>"
    out, report = repair(data)
    assert out == data
    assert report.changed is False
    assert report.describe() == "well formed"


def test_unmatched_end_tags_are_dropped() -> None:
    """OLRC emits closers for elements it never opened."""
    data = b"<a><b>text</b></quotedContent></p></notes></a>"
    out, report = repair(data)
    assert out == b"<a><b>text</b></a>"
    assert report.changed is True
    assert set(report.dropped_end_tags) == {"quotedContent", "p", "notes"}


def test_self_closing_tags_do_not_drift_the_stack() -> None:
    """USLM uses <content/>, <col/>, <td/>, <num/> and <br/> heavily.

    Treating a self-closing tag as an opener desynchronises the stack and turns
    every later valid end tag into a false mismatch -- which is what made the
    first version of this repair fail 5,900 lines further into the file.
    """
    data = b'<a><content/><br/><col x="1"/><b>t</b></a>'
    out, report = repair(data)
    assert out == data
    assert report.changed is False


def test_attributes_containing_angle_brackets() -> None:
    """A '>' inside a quoted attribute must not end the tag."""
    data = b'<a title="x > y"><b>t</b></a>'
    out, report = repair(data)
    assert out == data
    assert report.changed is False


def test_comments_and_cdata_are_preserved() -> None:
    """Markup inside comments and CDATA is not real markup."""
    data = b"<a><!-- </b> --><![CDATA[ </c> ]]>text</a>"
    out, report = repair(data)
    assert out == data
    assert report.changed is False


def test_unclosed_element_is_closed_implicitly() -> None:
    """A stray opener must not swallow the rest of the document."""
    data = b"<a><b><c>text</b></a>"
    out, report = repair(data)
    assert b"</c>" in out
    assert out.endswith(b"</a>")
