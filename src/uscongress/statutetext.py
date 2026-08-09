"""Render GPO USLM 2.0 XML into stable, diff-friendly Markdown (Statutes at Large).

Two mutually incompatible USLM schemas are in production. OLRC emits v1.0.15 for
the codified US Code and :mod:`uscongress.render` reads it; GPO emits v2.0 for
the Statutes at Large -- measured across the 137 volumes, four minor versions are
live at once (2.0.10, 2.0.12, 2.0.13 and 2.0.17), all under a different namespace
(``http://schemas.gpo.gov/xml/uslm``, not ``http://xml.house.gov/schemas/uslm/1.0``).

**This is a sibling of** :mod:`uscongress.render`, **not an extension of it**, and
the reason is not the namespace. It is that ``render._flatten`` collapses an
element with ``itertext()``, which is correct for the US Code and catastrophic
here, because a printed Statutes at Large page interleaves marginalia *inside*
the sentence:

* ``<sidenote>`` -- the marginal note printed beside the text. 5,293 of them in
  volume 1 alone, 3,718 in volume 117. Chapter I of 1789 reads ``...required by
  the sixth article of the Constitution of the United<sidenote>Form of the oath
  ...</sidenote> States, shall be administered...``, so ``itertext()`` splices a
  whole marginal note between "United" and "States". Headings are hit too: PL
  108-1's ``<heading>`` is ``In <sidenote>26 USC 3304 note.</sidenote>General.—``,
  which flattens to *"In 26 USC 3304 note. General.—"*.
* ``<page>`` and ``<centerRunningHead>`` -- page numbers and the running head
  printed at the top of each page, also mid-sentence, so a section acquires
  ``23 FIRST CONGRESS. Sess. I. Ch. 2. 1789. 24`` in the middle of a clause.

None of that exists in USLM 1.0, so the fix belongs here rather than in a shared
flatten. The decisive argument against changing ``render.py`` is that it renders
a repository that is already built and published: altering its flatten semantics
would rewrite all 383 commits of ``us-congress-code`` for no gain.

Marginalia are not discarded, only moved. Sidenotes carry the classification
notes (``26 USC 3304 note``) and, in every one of the 198 public laws of volume
117, the originating bill (``/us/bill/108/s/23`` -> branch ``s-23`` of
``us-congress-bills-108``), so they are collected and emitted as their own
section instead of being spliced into the prose.

The diff-stability rule from :mod:`uscongress.render` applies unchanged: only
semantic content is emitted. Every ``class``, every ``style`` -- GPO writes
91,448 of them into volume 117, all of them line codes like
``-uslm-lc:I658120`` -- and every ``id`` is discarded, because none of it is law
and all of it can move when GPO reprocesses a volume.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .xmlrepair import repair

USLM_2_0 = "http://schemas.gpo.gov/xml/uslm"
DUBLIN_CORE = "http://purl.org/dc/elements/1.1/"
_NS = f"{{{USLM_2_0}}}"
_DC = f"{{{DUBLIN_CORE}}}"

#: Elements that hold one enacted instrument. ``pLaw`` covers both public and
#: private acts and, before 1957, the numbered chapters; ``resolution`` covers
#: concurrent and public resolutions; ``document`` covers the four organic laws
#: reprinted at the front of volume 1 (Declaration, Articles, Constitution,
#: amendments).
LAW_TAGS = ("pLaw", "resolution", "document")

#: Printed in the margin or at the fold, not in the sentence. See the module
#: docstring: these are the reason this renderer exists.
_MARGINALIA = ("sidenote", "page", "centerRunningHead", "footnote", "notation")

#: Elements that introduce a nested, numbered level of legal structure.
_LEVELS = (
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "subitem",
)

#: Elements that head a division of an act rather than numbering a clause.
#: ``appropriations`` is GPO's wrapper for the account headings in an
#: appropriations act -- 1,922 of them in volume 117 -- and ``level`` is the
#: generic one used where the printed volume gives a heading no other name.
_HEADED = (
    "title",
    "subtitle",
    "chapter",
    "subchapter",
    "part",
    "subpart",
    "division",
    "subdivision",
    "article",
    "level",
    "appropriations",
)

#: Blocks that carry a level's *own* prose and are therefore folded into its
#: line rather than rendered separately. ``proviso`` is the *Provided, That...*
#: clause -- 2,523 of them in volume 117 -- which continues the sentence
#: ``content`` started, so splitting them apart would break it in two.
_LEAD = (
    "content",
    "chapeau",
    "continuation",
    "proviso",
    "recital",
    "listContent",
)

#: Blocks that are each their own line. ``p`` has to be one: the Declaration of
#: Independence is 32 consecutive ``<p>`` elements inside a single ``<block>``,
#: and folding them into one lead would print it as a single paragraph.
_PARA = ("p", "preamble", "heading")

#: Wrappers that contribute nothing themselves and are walked straight through.
_TRANSPARENT = ("block", "list", "toc", "groupItem", "headingItem", "notes", "main")

#: Everything the walker emits. Anything not here and not walked through is
#: deliberately dropped -- ``meta``, ``preface``, ``legislativeHistory`` and
#: ``action`` are all rendered from elsewhere in the document.
_NODES = (
    set(_LEVELS)
    | set(_HEADED)
    | set(_PARA)
    | {
        "section",
        "quotedContent",
        "table",
        "referenceItem",
        "listItem",
        "signatures",
    }
)

#: Rendered from the law's own ``<meta>`` or header, so emitting them again
#: from the body would duplicate them.
_HEADER_ONLY = (
    "longTitle",
    "docTitle",
    "officialTitle",
    "meta",
    "preface",
    "legislativeHistory",
    "action",
    "backMatter",
)

#: Labels belonging to the element that carries them, not to its prose.
_LABELS = ("num", "heading", "subheading")

_WS = re.compile(r"\s+")

#: A plausible enactment date. GPO's transcription carries four impossible ones
#: across the whole corpus -- ``1110-04-16`` in volume 32 and three ``1007-..``
#: in volume 34, both volumes covering the 1900s -- and an unfiltered maximum or
#: minimum over a volume would take them as fact.
_EARLIEST = date(1776, 1, 1)
_LATEST = date(2030, 1, 1)


def _tag(element: ET.Element) -> str:
    """Return an element's local tag name, namespace stripped.

    Args:
        element: Any element.

    Returns:
        The local name, e.g. ``section``.
    """
    return element.tag.rpartition("}")[2]


def _texts(element: ET.Element):
    """Yield an element's text fragments, skipping printed marginalia.

    This is the whole difference from ``render._flatten``. A ``<sidenote>``,
    ``<page>`` or ``<centerRunningHead>`` sits between two words of a sentence,
    so ``itertext()`` splices the margin into the text; its *tail* still belongs
    to the sentence and is kept.

    ``<br/>`` carries no text at all, so without a substitute the two lines it
    separates run together: volume 1's Declaration heading is ``THE UNANIMOUS
    DECLARATION OF THE THIRTEEN<br/>UNITED STATES OF AMERICA``.

    Args:
        element: Element to walk.

    Yields:
        Text fragments in document order.
    """
    if element.text:
        yield element.text
    for child in element:
        name = _tag(child)
        if name == "br":
            yield " "
        elif name not in _MARGINALIA:
            yield from _texts(child)
            if name == "p":
                # Two <p> siblings are two printed lines. Without a separator a
                # sidenote's date and bill number run together as
                # "Jan. 8, 2003[S. 23]".
                yield " "
        if child.tail:
            yield child.tail


def _flatten(element: ET.Element) -> str:
    """Collapse an element's prose to a single normalized string.

    Args:
        element: Element to flatten.

    Returns:
        Normalized text, marginalia removed.
    """
    return _WS.sub(" ", "".join(_texts(element))).strip()


def _prose_parts(element: ET.Element, top: bool):
    """Yield an element's own prose, stopping at anything rendered separately.

    Flattening a ``<content>`` wholesale would swallow the paragraphs and quoted
    text nested inside it and then print them twice, once folded into the parent
    line and once as their own structure. So the walk stops at every name in
    :data:`_NODES` -- but keeps its *tail*, because that is where the punctuation
    closing the interrupted sentence lives: the ``.`` after an inserted block.

    Args:
        element: A level or a lead block.
        top: True for the element itself, where ``num`` and ``heading`` are its
            label rather than its prose. False once inside a lead block, where a
            ``heading`` is genuinely part of the printed text.

    Yields:
        Text fragments in document order.
    """
    yield element.text or ""
    for child in element:
        name = _tag(child)
        if name == "br":
            yield " "
        elif name in _NODES or name in _MARGINALIA or name in _HEADER_ONLY:
            pass
        elif top and name in _LABELS:
            pass
        elif name in _LEAD or name in _TRANSPARENT:
            yield from _prose_parts(child, top=False)
        else:
            yield from _texts(child)
        if name != "quotedContent":
            # A quoted block's tail is the punctuation closing the sentence the
            # quotation interrupted, so it belongs on the end of the quote, not
            # dangling after the lead as "to read as follows:."
            yield child.tail or ""


def _prose(element: ET.Element) -> str:
    """Flatten only an element's own prose.

    Args:
        element: A level, section or lead block.

    Returns:
        The element's own prose, or an empty string.
    """
    return _WS.sub(" ", "".join(_prose_parts(element, top=True))).strip()


def _walk(element: ET.Element, top: bool = True):
    """Yield the renderable nodes under an element, in document order.

    The walk descends through lead blocks and wrappers so that a ``<paragraph>``
    buried in ``<content><block>`` is still found, and stops at every node, so no
    node is ever emitted twice.

    Args:
        element: Parent element.
        top: True for the element itself; see :func:`_prose_parts`.

    Yields:
        Elements to render.
    """
    for child in element:
        name = _tag(child)
        if name in _MARGINALIA or name in _HEADER_ONLY:
            continue
        if top and name in _LABELS:
            continue
        if name in _NODES:
            yield child
        elif name in _LEAD or name in _TRANSPARENT:
            yield from _walk(child, top=False)


def _num_of(element: ET.Element) -> str:
    """Return an element's number, preferring its machine-readable value.

    Args:
        element: Element that may carry a ``num`` child.

    Returns:
        The number, or an empty string.
    """
    num = element.find(f"{_NS}num")
    if num is None:
        return ""
    return (num.get("value") or _flatten(num)).strip().lstrip("§").strip(" .")


def _heading_of(element: ET.Element) -> str:
    """Return an element's heading text.

    Args:
        element: Element that may carry a ``heading`` child.

    Returns:
        The heading, or an empty string.
    """
    heading = element.find(f"{_NS}heading")
    return _flatten(heading) if heading is not None else ""


def _cell(element: ET.Element) -> str:
    """Render one table cell, escaping the Markdown column separator."""
    return _flatten(element).replace("|", "\\|") or " "


def _render_table(element: ET.Element) -> list[str]:
    """Render an XHTML table as a Markdown pipe table.

    Tables are not decoration in this corpus: the appropriations acts carry
    12,778 cells in volume 65 alone, and flattening a table of accounts and
    amounts into one line makes it unreadable and loses which figure belongs to
    which account.

    Args:
        element: An ``html:table`` element.

    Returns:
        Markdown lines, or an empty list if the table has no cells.
    """
    rows: list[list[str]] = []
    for row in element.iter():
        if _tag(row) != "tr":
            continue
        cells = [_cell(c) for c in row if _tag(c) in ("td", "th")]
        if cells:
            rows.append(cells)
    if not rows:
        return []

    width = max(len(r) for r in rows)
    rows = [r + [" "] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return out + [""]


def _render_reference(element: ET.Element, out: list[str]) -> None:
    """Render one table-of-contents entry as a bullet.

    Args:
        element: A ``referenceItem``.
        out: Accumulator of Markdown lines.
    """
    # All three parts matter and only the first is always present. The Revenue
    # Act of 1951's contents are 400 entries of designator plus <label>, so
    # reading the designator alone renders the whole table as a bare list of
    # "Sec. 101." with every section title dropped.
    parts = [
        _flatten(part)
        for name in ("designator", "label", "target")
        for part in element.findall(f"{_NS}{name}")
    ]
    line = " ".join(p for p in parts if p) or _flatten(element)
    if line:
        out.append(f"- {line}")


def _render_signatures(element: ET.Element, out: list[str]) -> None:
    """Render the block of signatures that closes a resolution or ratification.

    Args:
        element: A ``signatures`` element.
        out: Accumulator of Markdown lines.
    """
    lines = [_flatten(sig) for sig in element if _tag(sig) == "signature"]
    lines = [line for line in lines if line]
    if lines:
        out += ["", *[f"> {line}" for line in lines], ""]


def _render_quoted(element: ET.Element, depth: int) -> list[str]:
    """Render amendatory quoted text as a blockquote, keeping its structure.

    A ``<quotedContent>`` holds the words a law inserts into existing law -- the
    one part of an amendatory act that is not an instruction *about* law but the
    law itself. Collapsing a long insertion to a single line would make its
    internal structure invisible, so it is rendered structurally and indented.

    Args:
        element: The ``quotedContent`` element.
        depth: Nesting depth, used for indentation.

    Returns:
        Markdown lines.
    """
    inner: list[str] = []
    lead = _prose(element)
    if lead:
        inner.append(lead)
    for child in _walk(element):
        _render_node(child, 0, inner)
    while inner and inner[-1] == "":
        inner.pop()
    if not inner:
        return []
    trailer = _WS.sub(" ", element.tail or "").strip()
    if trailer:
        inner[-1] = f"{inner[-1]}{trailer}"
    indent = "  " * depth
    return [f"{indent}> {line}" if line else f"{indent}>" for line in inner] + [""]


def _render_level(element: ET.Element, depth: int, out: list[str]) -> None:
    """Render one nested, numbered level.

    Args:
        element: A subsection, paragraph, clause and so on.
        depth: Current nesting depth.
        out: Accumulator of Markdown lines.
    """
    num = _num_of(element)
    heading = _heading_of(element)
    label = f"**({num})**" if num else ""
    if heading:
        label = f"{label} *{heading}*".strip()
    lead = _prose(element)
    if label or lead:
        out.append(f"{'  ' * depth}- {label} {lead}".rstrip())
    for child in _walk(element):
        _render_node(child, depth + 1, out)


def _render_section(element: ET.Element, depth: int, out: list[str]) -> None:
    """Render one ``<section>``.

    Args:
        element: The section element.
        depth: Current nesting depth.
        out: Accumulator of Markdown lines.
    """
    num = _num_of(element)
    heading = _heading_of(element)
    if out and out[-1] != "":
        out.append("")
    if num and heading:
        out += [f"## § {num}. {heading}", ""]
    elif num:
        out += [f"## § {num}.", ""]
    elif heading:
        out += [f"## {heading}", ""]
    lead = _prose(element)
    if lead:
        out += [lead, ""]
    for child in _walk(element):
        _render_node(child, depth, out)
    if out and out[-1] != "":
        out.append("")


def _render_headed(element: ET.Element, depth: int, out: list[str]) -> None:
    """Render a division that carries a heading rather than a clause number.

    Args:
        element: A title, chapter, level, appropriations block and so on.
        depth: Current nesting depth.
        out: Accumulator of Markdown lines.
    """
    # The printed number, not the machine-readable one. The Civil Rights Act of
    # 1964 writes ``<num value="I">TITLE I—</num>``, so taking @value renders the
    # heading as "I VOTING RIGHTS" -- and in an act carrying TITLE I, PART A and
    # CHAPTER 1, all three collapse to a bare letter with nothing to say which is
    # which. Levels and sections still take @value; there the printed form is
    # only "(a) " or "Sec. 101. ", which the rendering supplies itself.
    printed = element.find(f"{_NS}num")
    num = _flatten(printed).rstrip("—–- ") if printed is not None else ""
    heading = _heading_of(element)
    subheading = element.find(f"{_NS}subheading")
    parts = [p for p in (num, heading) if p]
    if parts:
        if out and out[-1] != "":
            out.append("")
        # Peer of a section heading rather than below it: a division contains
        # sections, so nesting it deeper would invert the containment, and
        # nesting it shallower would leave every ordinary law -- which has no
        # divisions at all -- with orphaned third-level headings.
        out += [f"## {' — '.join(parts)}", ""]
    if subheading is not None and _flatten(subheading):
        out += [f"**{_flatten(subheading)}**", ""]
    lead = _prose(element)
    if lead:
        out += [lead, ""]
    for child in _walk(element):
        _render_node(child, depth, out)
    if out and out[-1] != "":
        out.append("")


def _render_node(node: ET.Element, depth: int, out: list[str]) -> None:
    """Render one node the walker yielded.

    Args:
        node: The element to render.
        depth: Nesting depth, used for indentation.
        out: Accumulator of Markdown lines.
    """
    name = _tag(node)
    if name == "section":
        _render_section(node, depth, out)
    elif name in _LEVELS:
        _render_level(node, depth, out)
    elif name in _HEADED:
        _render_headed(node, depth, out)
    elif name == "quotedContent":
        out += _render_quoted(node, depth)
    elif name == "table":
        out += _render_table(node)
    elif name == "referenceItem":
        _render_reference(node, out)
    elif name == "signatures":
        _render_signatures(node, out)
    elif name == "listItem":
        text = _prose(node)
        if text:
            out.append(f"{'  ' * depth}- {text}")
        for child in _walk(node):
            _render_node(child, depth + 1, out)
    elif name in _PARA or name in _LEAD or name in _TRANSPARENT:
        text = _prose(node)
        if text:
            out += ([f"{'  ' * depth}- {text}"] if depth else [text, ""])
        # top=False: a <heading> inside a lead block is printed text, not the
        # label of a level. 1 Stat. 1 puts the Declaration's "In Congress, July
        # 4, 1776." inside <content>, and skipping it as a label loses it.
        for child in _walk(node, top=False):
            _render_node(child, depth, out)


def _render_body(body: ET.Element, out: list[str]) -> None:
    """Render a law's ``<main>`` element.

    Different from :func:`_walk` at every deeper level, and it has to be. Inside
    a subsection a ``<content>`` is that subsection's own prose and is folded
    into its line; directly under ``<main>`` it is the whole operative text of
    the act, and 1 Stat. 573 -- the 1798 act punishing frauds on the Bank of the
    United States -- writes it exactly that way, with no ``<section>`` at all.
    Treating that ``<content>`` as something to walk through rather than render
    left the law with its title, its enacting formula and no body.

    Args:
        body: The ``main`` element, or the law element if it has none.
        out: Accumulator of Markdown lines.
    """
    for child in body:
        name = _tag(child)
        if name in _MARGINALIA or name in _HEADER_ONLY or name in _LABELS:
            continue
        if name in _NODES or name in _LEAD or name in _TRANSPARENT:
            _render_node(child, 0, out)


def _sidenotes(law: ET.Element) -> list[str]:
    """Collect the marginal notes printed beside a law, in document order.

    Args:
        law: The law element.

    Returns:
        Note texts, deduplicated but order-preserving.
    """
    seen: dict[str, None] = {}
    for element in law.iter():
        if _tag(element) != "sidenote":
            continue
        text = _flatten(element)
        if text:
            seen.setdefault(text, None)
    return list(seen)


def _bill_refs(law: ET.Element) -> tuple[str, ...]:
    """Return the measures a law came from, as branch names.

    Every one of the 198 public laws in volume 117 carries a ``/us/bill/`` link
    in its sidenote or its legislative history, which is exactly the branch name
    used by ``us-congress-bills-{congress}``. It is the only automatic join
    between enacted law and the bill that produced it, so it is worth lifting out
    of the markup.

    Args:
        law: The law element.

    Returns:
        ``(congress, branch)`` pairs rendered as ``108/s-23``, deduplicated.
    """
    found: dict[str, None] = {}
    for element in law.iter():
        href = element.get("href", "") if _tag(element) == "ref" else ""
        match = re.fullmatch(r"/us/bill/(\d+)/([a-z]+)/(\d+)", href)
        if match:
            found.setdefault(
                f"{match.group(1)}/{match.group(2)}-{match.group(3)}", None
            )
    return tuple(found)


def _legislative_history(law: ET.Element, out: list[str]) -> None:
    """Render the legislative history block that closes a modern public law.

    GPO prints one for each of the 198 public laws in volume 117: the bill it
    came from, its committee reports, and the dates the Congressional Record
    records it being considered and passed. That is the join to phase 2 and
    phase 6, so it is kept rather than dropped as front matter.

    Args:
        law: The law element.
        out: Accumulator of Markdown lines.
    """
    blocks = [e for e in law.iter() if _tag(e) == "legislativeHistory"]
    if not blocks:
        return
    out += ["## Legislative history", ""]
    for block in blocks:
        for child in block:
            if _tag(child) == "heading":
                text = _flatten(child)
                if text:
                    out += [f"**{text}**", ""]
            elif _tag(child) == "note":
                heading = _heading_of(child)
                if heading:
                    out += [f"*{heading}*", ""]
                for line in child:
                    if _tag(line) in ("p", "content"):
                        text = _flatten(line)
                        if text:
                            out.append(f"- {text}")
                out.append("")


def _slug(value: str) -> str:
    """Reduce a citation fragment to a filesystem-safe token.

    Args:
        value: Raw text, e.g. ``Public Law`` or ``XLVIII``.

    Returns:
        A lowercase, filesystem-safe token.
    """
    cleaned = value.strip().replace("—", "-").replace("–", "-")
    return re.sub(r"[^A-Za-z0-9.-]+", "-", cleaned).strip("-.").lower()


def _approved(law: ET.Element) -> date | None:
    """Return a law's date of approval.

    The date is written in three places and only one of them can be relied on.
    ``<meta><approvedDate>`` is absent from volumes 1 to 63 entirely, and
    ``<meta><dc:date>`` is sometimes a year and sometimes a printed date. The
    ``date`` *attribute* on the ``<approvedDate>`` set in the margin is there
    throughout -- 479 of volume 1's 493 laws, and all of most later volumes --
    so it is read first and the ``<meta>`` fields are the fallback.

    Args:
        law: The law element.

    Returns:
        The date, or None if none is recorded or the one recorded is impossible.
    """
    stamps: list[str] = []
    for element in law.iter():
        if _tag(element) == "approvedDate" and element.get("date"):
            stamps.append(element.get("date", ""))
            break
    meta = law.find(f"{_NS}meta")
    if meta is not None:
        stamps += [
            (meta.findtext(f"{_NS}approvedDate") or "").strip(),
            (meta.findtext(f"{_DC}date") or "").strip(),
        ]
    for stamp in stamps:
        try:
            parsed = date.fromisoformat(stamp[:10])
        except ValueError:
            continue
        if _EARLIEST <= parsed < _LATEST:
            return parsed
    return None


@dataclass(frozen=True)
class Law:
    """One session law as printed in the Statutes at Large.

    Attributes:
        volume: Statutes at Large volume number.
        kind: Source element, one of :data:`LAW_TAGS`.
        doc_type: Type as GPO writes it, e.g. ``Public Law``, ``Chapter``.
        number: Document number as printed, e.g. ``1`` or ``XLVIII``.
        congress: Congress number, or empty when GPO records none.
        session: Session number, or empty.
        scope: ``public``, ``private`` or empty.
        title: Official title as printed.
        citations: Every ``citableAs`` the law carries, e.g.
            ``("Public Law 108-1", "117 Stat. 3")``.
        page: First page of the law in its volume, or empty.
        approved: Date of approval, or None.
        bills: Originating measures as ``congress/branch``, e.g. ``108/s-23``.
        role: ``role`` of the enclosing ``<component>``, e.g.
            ``declarationIndependence``. The only name the four organic laws at
            the front of volume 1 carry -- they have no docNumber and no
            ``dc:type`` at all.
        order: Position in the volume, 0 first. Used only to break filename ties.
        markdown: The rendered document.
    """

    volume: int
    kind: str
    doc_type: str
    number: str
    congress: str
    session: str
    scope: str
    title: str
    citations: tuple[str, ...]
    page: str
    approved: date | None
    bills: tuple[str, ...]
    role: str
    order: int
    markdown: str

    @property
    def bucket(self) -> str:
        """Directory within the volume: which kind of instrument this is.

        A private act is not general law -- it relieves one named person or
        firm -- and in the older volumes it outnumbers public law heavily: 6,238
        private chapters against 3,011 public ones across the volumes sampled.
        Filing them together would bury the general law inside the relief acts.
        """
        if self.kind == "document":
            return "organic"
        if self.scope == "private":
            return "private"
        if self.kind == "resolution":
            return "resolutions"
        return "public"

    @property
    def key(self) -> str:
        """Filename stem, derived from how the law is cited.

        A stem must not encode position in the volume: GPO reprocesses volumes
        whole -- volume 1 was re-digitised on 2025-11-03 -- and an inserted law
        would then renumber every file after it, turning one correction into a
        rewrite of the volume.
        """
        kind = _slug(self.doc_type) or _slug(self.role) or _slug(self.kind)
        parts = [p for p in (kind, self.congress) if p]
        if kind == "chapter" and self.session:
            # Chapter numbers restart every session, so congress alone is not
            # enough: the 1st Congress numbered a Chapter I in each of its three.
            parts.append(self.session)
        if self.number:
            parts.append(_slug(self.number))
        elif self.page:
            # 33 laws in the volumes sampled carry no docNumber at all; the page
            # they start on is the only thing left that cites them.
            parts.append(f"p{self.page}")
        stem = "-".join(p for p in parts if p)
        return stem or _slug(self.title)[:60] or "law"

    @property
    def path(self) -> str:
        """Repository-relative file path for this law."""
        return f"volume-{self.volume:03d}/{self.bucket}/{self.key}.md"

    @property
    def citation(self) -> str:
        """Shortest citation that identifies the law, e.g. ``Public Law 108-1``."""
        for cite in self.citations:
            if "Stat." not in cite:
                return cite
        if self.doc_type and self.number:
            return f"{self.doc_type} {self.number}"
        return self.citations[0] if self.citations else self.title[:60]

    @property
    def stat_cite(self) -> str:
        """Page citation, e.g. ``117 Stat. 3``, or an empty string."""
        return next((c for c in self.citations if "Stat." in c), "")


def _meta_text(law: ET.Element, name: str, namespace: str = _NS) -> str:
    """Read one field from a law's ``<meta>`` block.

    Args:
        law: The law element.
        name: Child tag name.
        namespace: Namespace of the child, defaulting to USLM.

    Returns:
        The text, stripped, or an empty string.
    """
    meta = law.find(f"{_NS}meta")
    if meta is None:
        return ""
    return (meta.findtext(f"{namespace}{name}") or "").strip()


def render_law(law: ET.Element, volume: int, order: int, role: str = "") -> Law:
    """Render one law element to Markdown.

    Args:
        law: A ``pLaw``, ``resolution`` or ``document`` element.
        volume: Statutes at Large volume number.
        order: Position of this law in the volume, 0 first.
        role: ``role`` of the enclosing ``<component>``, if it has one.

    Returns:
        The rendered law.
    """
    citations = tuple(
        text
        for text in (
            _flatten(c) for c in law.iter() if _tag(c) == "citableAs"
        )
        if text
    )
    page_match = re.search(r"\bStat\.\s*(\S+)", " | ".join(citations))
    # officialTitle is preferred over dc:title because GPO prefixes the latter
    # with the citation -- "Public Law 108-1: To provide for a 5-month
    # extension..." -- which the heading above it already carries.
    # "An Act" and "to regulate the Time and Manner..." are two elements of one
    # printed sentence, so they are joined; on their own the second reads as a
    # fragment beginning with a lowercase word.
    parts = []
    for name in ("docTitle", "officialTitle"):
        found = law.find(f".//{_NS}{name}")
        if found is not None and _flatten(found):
            parts.append(_flatten(found))
    title = " ".join(parts)
    if not title:
        title = re.sub(r"^[^:]{0,40}:\s*", "", _meta_text(law, "title", _DC))

    approved = _approved(law)
    doc_type = _meta_text(law, "type", _DC)
    number = _meta_text(law, "docNumber")
    congress = _meta_text(law, "congress")
    session = _meta_text(law, "session")
    scope = _meta_text(law, "publicPrivate")
    bills = _bill_refs(law)
    stat_cite = next((c for c in citations if "Stat." in c), "")

    header = [
        "---",
        f"volume: {volume}",
        *([f"citation: {stat_cite}"] if stat_cite else []),
        *([f"type: {doc_type}"] if doc_type else []),
        *([f"number: {number}"] if number else []),
        *([f"congress: {congress}"] if congress else []),
        *([f"session: {session}"] if session else []),
        *([f"scope: {scope}"] if scope else []),
        *([f"approved: {approved.isoformat()}"] if approved else []),
        *([f"bills: {', '.join(bills)}"] if bills else []),
        "---",
        "",
    ]

    label = next(
        (c for c in citations if "Stat." not in c),
        f"{doc_type} {number}".strip() if doc_type else "",
    )
    heading = label or title[:80] or "Untitled"
    out = [*header, f"# {heading}", ""]
    if title and title != heading:
        out += [f"> {title}", ""]

    body = law.find(f"{_NS}main")
    body = body if body is not None else law

    # Only a formula that is a direct child of <main> is hoisted. Before 1874
    # each section carries its own -- "And be it further enacted," opens every
    # section of the 1789 oath act -- and hoisting one of those would print it
    # twice and detach it from the section it belongs to.
    for name in ("enactingFormula", "resolvingClause"):
        formula = body.find(f"{_NS}{name}")
        if formula is not None and _flatten(formula):
            out += [f"*{_flatten(formula)}*", ""]

    _render_body(body, out)

    action = law.find(f".//{_NS}action")
    if action is not None and _flatten(action):
        out += ["", f"*{_flatten(action)}*", ""]

    notes = _sidenotes(law)
    if notes:
        out += [
            "## Marginal notes",
            "",
            "Printed in the margin of the volume beside the text above, not in it.",
            "",
            *[f"- {note}" for note in notes],
            "",
        ]
    _legislative_history(law, out)

    return Law(
        volume=volume,
        kind=_tag(law),
        doc_type=doc_type,
        number=number,
        congress=congress,
        session=session,
        scope=scope,
        title=title,
        citations=citations,
        page=page_match.group(1) if page_match else "",
        approved=approved,
        bills=bills,
        role=role,
        order=order,
        markdown="\n".join(out).rstrip() + "\n",
    )


def _collect_roles(element: ET.Element, role: str, out: dict[int, str]) -> None:
    """Record the ``<component role=...>`` each organic law sits inside.

    The four documents at the front of volume 1 -- the Declaration, the Articles
    of Confederation, the Constitution and its amendments -- carry no
    ``docNumber`` and no ``dc:type``, so the component's role is the only name
    they have. There are 47 of them in the whole corpus.

    The walk stops at every law element and at the volume's front and back
    matter, which is where the bulk of the bytes are: building a full parent map
    instead would mean indexing all of volume 44's 71 MB to answer 7 questions.

    Args:
        element: Element to descend into.
        role: Role inherited from the nearest enclosing component.
        out: Accumulator, keyed by element identity.
    """
    for child in element:
        name = _tag(child)
        if name == "document":
            out[id(child)] = role
        elif name in LAW_TAGS or name in ("meta", "preface", "backMatter"):
            continue
        else:
            _collect_roles(child, child.get("role") or role, out)


@dataclass(frozen=True)
class VolumeRender:
    """Everything one volume yielded.

    Attributes:
        volume: Volume number.
        laws: Rendered session laws, in printed order.
        presidential: Count of treaties, proclamations and executive agreements
            found and deliberately not rendered; see
            :func:`uscongress.jobs.statutes.seed`.
        undated: Count of laws GPO records no usable approval date for.
        repair: One-line description of any repair the document needed.
    """

    volume: int
    laws: tuple[Law, ...]
    presidential: int
    undated: int
    repair: str

    @property
    def latest(self) -> date | None:
        """Latest plausible approval date in the volume, or None."""
        stamps = [law.approved for law in self.laws if law.approved]
        return max(stamps) if stamps else None


def render_volume(xml_bytes: bytes, volume: int) -> VolumeRender:
    """Render every session law in one Statutes at Large volume.

    ``<presidentialDoc>`` is counted and skipped rather than rendered. Volumes 7
    and 8 hold nothing else -- they are the Indian and foreign treaty volumes,
    and contain no session law at all -- and 13,387 such documents exist across
    the corpus. They are a different instrument, made by ratification rather than
    by bicameral passage and presentment, so they are outside what this
    repository claims to hold; the count is returned so the omission can be
    stated rather than left as an unexplained hole at volumes 7 and 8.

    Args:
        xml_bytes: Raw contents of one ``STATUTE-N.xml``.
        volume: Volume number.

    Returns:
        What the volume yielded.
    """
    repaired, report = repair(xml_bytes)
    root = _safe_fromstring(repaired)

    roles: dict[int, str] = {}
    _collect_roles(root, "", roles)

    laws: list[Law] = []
    presidential = 0
    for element in root.iter():
        name = _tag(element)
        if name == "presidentialDoc":
            presidential += 1
        elif name in LAW_TAGS:
            laws.append(
                render_law(element, volume, len(laws), roles.get(id(element), ""))
            )

    return VolumeRender(
        volume=volume,
        laws=tuple(laws),
        presidential=presidential,
        undated=sum(1 for law in laws if law.approved is None),
        repair=report.describe() if report.changed else "",
    )


def to_file_map(laws: tuple[Law, ...] | list[Law]) -> dict[str, str]:
    """Lay laws out as files, disambiguating genuine duplicate numbers.

    Duplicate citations are real, not a parsing error. Volume 1 prints two
    Chapter IIIs in the first session of the 1st Congress and volume 65 two House
    Concurrent Resolution 98s. Both are law, so neither may be dropped: the later
    one takes an ordinal suffix, exactly as :func:`uscongress.render.to_file_map`
    does for the two 5 U.S.C. 3598s.

    Printed order is stable across reprocessing, so the assignment does not churn.

    Args:
        laws: Rendered laws, in printed order.

    Returns:
        Mapping of repository-relative path to contents.
    """
    files: dict[str, str] = {}
    used: dict[str, int] = {}
    for law in laws:
        path = law.path
        if path in used:
            used[path] += 1
            stem, _, suffix = path.rpartition(".")
            path = f"{stem}-{used[path]}.{suffix}"
        else:
            used[path] = 1
        files[path] = law.markdown
    return files
