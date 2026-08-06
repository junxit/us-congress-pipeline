"""Render legacy Congressional bill XML into stable, diff-friendly Markdown.

Bills are not USLM. govinfo serves them under the DTD
``-//US Congress//DTDs/bill.dtd//EN``, with no namespace and a different
vocabulary from :mod:`uscongress.render`: ``<enum>`` where USLM writes ``<num>``,
``<header>`` where it writes ``<heading>``, ``<text>`` where it writes ``<p>``.
GPO does publish a parallel USLM 2.0 tree, but measured across the BILLS
collection it covers 1.8% of versions -- 2,443 of 134,013 -- so the legacy
format is the one that matters.

Two root elements occur: ``<bill>`` and ``<resolution>``. Resolutions carry
``<whereas>`` preambles that bills do not.

The diff-stability rule from :mod:`uscongress.render` applies here for the same
reason. A bill branch's value is that ``git diff hr-1234~1 hr-1234`` shows how
the text changed between versions, which only holds if unchanged text renders to
identical bytes. Every element carries a generated ``id``; ``<text>`` carries a
presentational ``display-inline``; ``<quoted-block>`` carries ``style``. None of
it is semantic and all of it is discarded.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .xmlrepair import repair

#: Elements that introduce a nested, numbered level of legal structure. A
#: ``section`` is deliberately absent: it always renders as a heading, wherever
#: it appears, including inside an amendment's replacement text.
_LEVELS = (
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "subitem",
)

#: Elements that hold structure but contribute no text of their own. An
#: engrossed amendment keeps its entire substance inside these, so failing to
#: descend through them drops the whole document body.
_CONTAINERS = ("amendment", "amendment-block")

#: Elements rendered as their own block of prose.
_BLOCKS = ("text", "chapeau", "continuation")

#: Document roots govinfo serves for a bill text version. An engrossed
#: amendment is a distinct document type, not a bill carrying amendments.
_ROOTS = ("bill", "resolution", "amendment-doc")

_WS = re.compile(r"\s+")

#: A space between the letters of a chamber prefix, as in ``H. R. 588``.
_PREFIX_SPACE = re.compile(r"(?<=\.)\s+(?=[A-Z]\.)")


def _normalise_legis_num(value: str) -> str:
    """Collapse a measure number to one spelling.

    govinfo is inconsistent between versions of the same bill: the introduced
    text of H.R. 588 writes ``H. R. 588`` and its engrossed Senate amendment
    writes ``H.R. 588``. Left alone that flips the frontmatter on the very
    commits whose diffs matter most, burying the text change in noise.

    Args:
        value: Number as printed.

    Returns:
        The number with intra-prefix spacing removed.
    """
    return _PREFIX_SPACE.sub("", value)


def _tag(element: ET.Element) -> str:
    """Return an element's local tag name, namespace stripped."""
    return element.tag.rpartition("}")[2]


def _texts(element: ET.Element):
    """Yield an element's text fragments, restoring quotation marks.

    ``<quote>`` is not decoration: it delimits the exact words a bill strikes or
    inserts. Flattening it away turns *striking the "and" after the semicolon*
    into *striking the and after the semicolon*, which reads as gibberish and
    loses the boundary of the quoted term. The marks are markup rather than
    characters in the source, so they have to be put back.

    Args:
        element: Element to walk.

    Yields:
        Text fragments in document order.
    """
    if element.text:
        yield element.text
    for child in element:
        if _tag(child) == "quote":
            yield "“"
            yield from _texts(child)
            yield "”"
        else:
            yield from _texts(child)
        if child.tail:
            yield child.tail


def _flatten(element: ET.Element) -> str:
    """Collapse an element's full text content to a single normalised string.

    Inline markup -- ``external-xref``, ``term``, ``short-title`` -- carries no
    meaning worth preserving as markup, so it is flattened; ``quote`` is the
    exception, see :func:`_texts`. Bill XML is hard-wrapped at inconsistent
    widths, so whitespace is normalised to keep a reflow from registering as a
    textual change.

    Args:
        element: Element to flatten.

    Returns:
        Normalised text.
    """
    return _WS.sub(" ", "".join(_texts(element))).strip()


def _direct_text(element: ET.Element) -> str:
    """Flatten only an element's own prose, excluding nested levels.

    Flattening a ``<section>`` wholesale would swallow every nested paragraph,
    so prose and structure have to be walked separately.

    Args:
        element: A structural level.

    Returns:
        The level's own prose, or an empty string.
    """
    parts = [_flatten(child) for child in element if _tag(child) in _BLOCKS]
    return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class BillDoc:
    """One rendered bill text version.

    Attributes:
        legis_num: Number as printed, e.g. ``H. R. 588``.
        congress: Congress as printed, e.g. ``113th CONGRESS``.
        session: Session as printed, e.g. ``1st Session``.
        chamber: Originating chamber line, e.g. ``IN THE HOUSE OF REPRESENTATIVES``.
        official_title: The "To amend..." long title.
        short_title: Popular name, if the bill gives itself one.
        sponsor: Sponsor as printed.
        cosponsors: Cosponsors as printed, in document order.
        markdown: Full rendered document.
    """

    legis_num: str
    congress: str
    session: str
    chamber: str
    official_title: str
    short_title: str
    sponsor: str
    cosponsors: tuple[str, ...]
    markdown: str


def _render_level(element: ET.Element, depth: int, out: list[str]) -> None:
    """Recursively render a nested structural level.

    Args:
        element: A subsection, paragraph, clause and so on.
        depth: Current nesting depth, used for indentation.
        out: Accumulator of Markdown lines.
    """
    indent = "  " * depth
    enum = element.find("enum")
    num = _flatten(enum).strip(".() ") if enum is not None else ""
    header = element.find("header")

    label = f"**({num})**" if num else ""
    if header is not None:
        heading = _flatten(header)
        if heading:
            label = f"{label} *{heading}*".strip()

    lead = _direct_text(element)
    if label or lead:
        out.append(f"{indent}- {label} {lead}".rstrip())

    _render_children(element, depth + 1, out)


def _render_children(element: ET.Element, depth: int, out: list[str]) -> None:
    """Render an element's structural children in document order.

    ``<quoted-block>`` and ``<after-quoted-block>`` are siblings that belong
    together -- the second holds the punctuation closing the sentence the first
    interrupted -- so they are paired here rather than emitted separately.

    Args:
        element: Parent element.
        depth: Nesting depth for the children.
        out: Accumulator of Markdown lines.
    """
    children = list(element)
    for index, child in enumerate(children):
        tag = _tag(child)
        if tag == "section":
            _render_section(child, out)
        elif tag in _LEVELS:
            _render_level(child, depth, out)
        elif tag in _CONTAINERS:
            _render_children(child, depth, out)
        elif tag == "amendment-instruction":
            instruction = _flatten(child)
            if instruction:
                out += [f"*{instruction}*", ""]
        elif tag == "quoted-block":
            following = children[index + 1] if index + 1 < len(children) else None
            trailer = (
                _flatten(following)
                if following is not None and _tag(following) == "after-quoted-block"
                else ""
            )
            out += _render_quoted_block(child, depth, trailer)


def _render_quoted_block(element: ET.Element, depth: int, trailer: str = "") -> list[str]:
    """Render amendatory quoted text as a blockquote, keeping its structure.

    A ``<quoted-block>`` holds text a bill proposes to insert into existing law.
    Keeping it visually distinct matters because it is the one part of a bill
    that is not instructions *about* law but the words of the law itself.

    It carries the same nested levels as the bill around it, so it is rendered
    structurally rather than flattened -- a long insertion collapsed to one line
    is unreadable and its internal amendments become invisible in a diff.

    Args:
        element: The ``quoted-block`` element.
        depth: Nesting depth, used for indentation.
        trailer: Text of any following ``after-quoted-block``, usually the
            punctuation closing the sentence the quotation interrupted.

    Returns:
        Markdown lines.
    """
    inner: list[str] = []
    lead = _direct_text(element)
    if lead:
        inner.append(lead)
    _render_children(element, 0, inner)

    while inner and inner[-1] == "":
        inner.pop()
    if not inner:
        return []
    if trailer:
        inner[-1] = f"{inner[-1]}{trailer}"

    indent = "  " * depth
    return [f"{indent}> {line}" if line else f"{indent}>" for line in inner] + [""]


def _render_section(element: ET.Element, out: list[str]) -> None:
    """Render one top-level ``<section>`` of a bill.

    Args:
        element: The ``section`` element.
        out: Accumulator of Markdown lines.
    """
    enum = element.find("enum")
    num = _flatten(enum).strip(".() ") if enum is not None else ""
    header = element.find("header")
    heading = _flatten(header) if header is not None else ""

    if num and heading:
        out += [f"## § {num}. {heading}", ""]
    elif num:
        out += [f"## § {num}.", ""]
    elif heading:
        out += [f"## {heading}", ""]

    lead = _direct_text(element)
    if lead:
        out += [lead, ""]

    _render_children(element, 0, out)

    if out and out[-1] != "":
        out.append("")


def _form_field(form: ET.Element | None, name: str) -> str:
    """Return one flattened field from the ``<form>`` block.

    Args:
        form: The ``form`` element, or None.
        name: Child tag to read.

    Returns:
        The field text, or an empty string.
    """
    if form is None:
        return ""
    found = form.find(name)
    return _flatten(found) if found is not None else ""


def render_bill(xml_bytes: bytes, legis_num: str = "") -> BillDoc:
    """Render one bill text version to Markdown.

    The frontmatter deliberately holds only bill identity, not which version
    this is. Version and date live in the commit message instead, so that a
    diff between two commits on a bill branch shows the text that changed rather
    than a frontmatter header that changes every time.

    Args:
        xml_bytes: Raw contents of one ``BILLS-*.xml`` document.
        legis_num: Measure number to fall back on when the document does not
            state its own. House amendments to Senate amendments carry a
            ``legis-type`` instead of a ``legis-num``, so left alone they render
            with an empty heading and a blank identity on one commit of an
            otherwise complete branch.

    Returns:
        The rendered document.

    Raises:
        ValueError: If the root element is not a recognised bill document.
    """
    # govinfo publishes bill text that is not always well formed -- bare
    # ampersands in titles are the common case -- so the same repair pass the
    # US Code build relies on is applied first.
    repaired, _ = repair(xml_bytes)
    root = _safe_fromstring(repaired)
    if _tag(root) not in _ROOTS:
        raise ValueError(f"unrecognised bill document root: <{_tag(root)}>")

    # The three document types name their parts in parallel -- form/legis-body,
    # form/resolution-body, engrossed-amendment-form/engrossed-amendment-body --
    # so they are found by suffix rather than by an exhaustive list of names.
    form = next((c for c in root if _tag(c).endswith("form")), None)
    stated = _normalise_legis_num(_form_field(form, "legis-num"))
    legis_num = stated or _normalise_legis_num(legis_num)
    congress = _form_field(form, "congress")
    session = _form_field(form, "session")
    chamber = _form_field(form, "current-chamber")
    official_title = _form_field(form, "official-title")

    short_el = root.find(".//short-title")
    short_title = _flatten(short_el) if short_el is not None else ""

    sponsor_el = root.find(".//sponsor")
    sponsor = _flatten(sponsor_el) if sponsor_el is not None else ""
    cosponsors = tuple(_flatten(c) for c in root.iter("cosponsor") if _flatten(c))

    out: list[str] = [
        "---",
        f"legis-num: {legis_num}",
        f"congress: {congress}",
        f"session: {session}",
        f"chamber: {chamber}",
        "---",
        "",
        f"# {legis_num}" if legis_num else "# (unnumbered measure)",
        "",
    ]
    if official_title:
        out += [f"> {official_title}", ""]

    # Resolutions state their rationale in <whereas> clauses before the
    # resolving text; bills have no equivalent.
    whereas = [_flatten(w) for w in root.iter("whereas")]
    whereas = [w for w in whereas if w]
    if whereas:
        out += ["## Preamble", ""]
        out += [f"- {w}" for w in whereas]
        out.append("")

    body = next((c for c in root if _tag(c).endswith("body")), None)
    if body is not None:
        lead = _direct_text(body)
        if lead:
            out += [lead, ""]
        _render_children(body, 0, out)

    markdown = "\n".join(out).rstrip() + "\n"
    return BillDoc(
        legis_num=legis_num,
        congress=congress,
        session=session,
        chamber=chamber,
        official_title=official_title,
        short_title=short_title,
        sponsor=sponsor,
        cosponsors=cosponsors,
        markdown=markdown,
    )
