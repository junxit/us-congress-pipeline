"""Render OLRC USLM 1.0 XML into stable, diff-friendly Markdown.

The whole project rests on `git diff` between release points showing exactly
what changed in the law. That only works if identical text renders to identical
bytes, so this module is deliberately conservative about what it emits.

**The trap that would silently ruin everything:** USLM elements carry an ``id``
attribute holding a generated UUID, and OLRC regenerates it on every release
point *even when the text is unchanged*. Measured on Title 1 between
``118-22u1`` and ``119-102``: 39 of 40 sections had a different ``id`` and
byte-identical text. Emitting ``id`` would mark almost every section modified in
every commit.

So only semantic content is rendered: the stable ``identifier``, the section
number and heading, the text, the source credit, and notes. Every generated
``id``, presentational ``style`` and layout ``class`` is discarded.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring as _safe_fromstring

USLM_1_0 = "http://xml.house.gov/schemas/uslm/1.0"
_NS = f"{{{USLM_1_0}}}"

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

#: Elements rendered as their own block of prose.
_BLOCKS = ("p", "chapeau", "continuation", "quotedContent")

_WS = re.compile(r"\s+")


def _tag(element: ET.Element) -> str:
    """Return an element's local tag name, namespace stripped."""
    return element.tag.rpartition("}")[2]


def _flatten(element: ET.Element) -> str:
    """Collapse an element's full text content to a single normalized string.

    Inline markup (``ref``, ``inline``, ``span``, emphasis) carries no meaning we
    preserve, so it is flattened. Whitespace is normalized so that reflowed
    source XML does not register as a textual change.

    Args:
        element: Element to flatten.

    Returns:
        Normalized text.
    """
    return _WS.sub(" ", "".join(element.itertext())).strip()


@dataclass(frozen=True)
class Section:
    """One rendered section of the US Code.

    Attributes:
        identifier: Stable USLM identifier, e.g. ``/us/usc/t1/s2``.
        title: Title number as written, e.g. ``1``.
        chapter: Chapter number as written, or empty if the section has none.
        num: Section number, e.g. ``2``.
        heading: Section heading.
        markdown: Full rendered document.
    """

    identifier: str
    title: str
    chapter: str
    num: str
    heading: str
    markdown: str

    @property
    def path(self) -> str:
        """Repository-relative file path for this section."""
        chapter = _slug(self.chapter)
        stem = _slug(self.num) or _slug(self.identifier.rpartition("/")[2])
        folder = title_folder(self.title)
        if chapter:
            folder = f"{folder}/chapter-{chapter}"
        return f"{folder}/sec-{stem}.md"


def _slug(value: str) -> str:
    """Reduce a legal number to a filesystem-safe token.

    Section numbers are not always integers -- ``1395x``, ``3161 nt`` and
    ``2000e-2`` all occur -- so this keeps alphanumerics and dashes only.

    Args:
        value: Raw number text.

    Returns:
        A lowercase, filesystem-safe token.
    """
    cleaned = value.strip().lstrip("§").strip().rstrip(".")
    cleaned = cleaned.replace("—", "-").replace("–", "-")
    return re.sub(r"[^A-Za-z0-9.-]+", "-", cleaned).strip("-").lower()


def _pad_title(title: str) -> str:
    """Zero-pad a title number, preserving any appendix letter.

    Appendix titles are written ``5a``/``11a``/``50a``. Padding only the numeric
    part keeps ``title-05a`` sorting beside ``title-05`` instead of drifting to
    the end of the listing.

    Args:
        title: Title token, e.g. ``5``, ``26``, ``5a``.

    Returns:
        The padded token, e.g. ``05``, ``26``, ``05a``.
    """
    match = re.fullmatch(r"(\d+)([a-z]*)", title)
    if not match:
        return title
    number, suffix = match.groups()
    return f"{number.zfill(2)}{suffix}"


def title_folder(title: str) -> str:
    """Repository directory holding one title, e.g. ``title-05a``.

    Exposed because a title whose sections have all been retired renders no
    sections at all, so its directory name cannot be recovered from them.

    Args:
        title: Title token as OLRC writes it, e.g. ``5``, ``26``, ``50A``.

    Returns:
        The directory name.
    """
    return f"title-{_pad_title(_slug(title) or '0')}"


def _title_from_identifier(identifier: str) -> str:
    """Extract a title token from a USLM identifier.

    Args:
        identifier: e.g. ``/us/usc/t18a/pl/96/456/s12`` or ``/us/usc/t5a``.

    Returns:
        The title token (``18a``, ``5a``), or an empty string.
    """
    match = re.match(r"/us/usc/t(\w+)", identifier or "")
    return match.group(1).lower() if match else ""


def _num_of(element: ET.Element) -> str:
    """Return an element's ``num`` text, preferring its machine-readable value.

    Args:
        element: Element that may carry a ``num`` child.

    Returns:
        The number, or an empty string.
    """
    num = element.find(f"{_NS}num")
    if num is None:
        return ""
    return (num.get("value") or _flatten(num).lstrip("§").strip().rstrip(".")).strip()


def _render_level(element: ET.Element, depth: int, out: list[str]) -> None:
    """Recursively render a nested structural level.

    Args:
        element: A subsection, paragraph, clause and so on.
        depth: Current nesting depth, used for indentation.
        out: Accumulator of Markdown lines.
    """
    indent = "  " * depth
    num = _num_of(element)
    heading = element.find(f"{_NS}heading")
    label = f"**({num})**" if num else ""
    if heading is not None:
        label = f"{label} *{_flatten(heading)}*".strip()

    chapeau = element.find(f"{_NS}chapeau")
    lead = _flatten(chapeau) if chapeau is not None else ""

    content = element.find(f"{_NS}content")
    if content is not None and not lead:
        lead = _flatten(content)

    if label or lead:
        out.append(f"{indent}- {label} {lead}".rstrip())

    for child in element:
        if _tag(child) in _LEVELS:
            _render_level(child, depth + 1, out)


def render_section(element: ET.Element, title: str, chapter: str) -> Section:
    """Render a single ``<section>`` element to Markdown.

    Args:
        element: The USLM ``section`` element.
        title: Title number this section belongs to.
        chapter: Chapter number, or empty string.

    Returns:
        The rendered section.
    """
    identifier = element.get("identifier", "")
    num = _num_of(element)
    heading_el = element.find(f"{_NS}heading")
    heading = _flatten(heading_el) if heading_el is not None else ""

    out: list[str] = [
        "---",
        f"identifier: {identifier}",
        f"title: {title}",
        *( [f"chapter: {chapter}"] if chapter else [] ),
        f"section: {num}",
        "---",
        "",
        f"# § {num}. {heading}".rstrip(". ").rstrip() if num else f"# {heading}",
        "",
    ]

    # Direct prose, before any nested levels.
    for child in element:
        if _tag(child) in _BLOCKS:
            text = _flatten(child)
            if text:
                out += [text, ""]
        elif _tag(child) == "content":
            has_levels = any(_tag(g) in _LEVELS for g in child)
            if not has_levels:
                text = _flatten(child)
                if text:
                    out += [text, ""]
            else:
                for grandchild in child:
                    if _tag(grandchild) in _LEVELS:
                        _render_level(grandchild, 0, out)
                out.append("")
        elif _tag(child) in _LEVELS:
            _render_level(child, 0, out)

    if out and out[-1] != "":
        out.append("")

    credit = element.find(f"{_NS}sourceCredit")
    if credit is not None:
        out += ["## Source credit", "", _flatten(credit), ""]

    notes = [n for n in element.iter(f"{_NS}note")]
    if notes:
        out += ["## Notes", ""]
        for note in notes:
            topic = note.get("topic", "").strip()
            note_heading = note.find(f"{_NS}heading")
            label = _flatten(note_heading) if note_heading is not None else topic
            text = _flatten(note)
            if label and text.startswith(label):
                text = text[len(label) :].strip()
            if label:
                out.append(f"### {label}")
                out.append("")
            if text:
                out += [text, ""]

    markdown = "\n".join(out).rstrip() + "\n"
    return Section(
        identifier=identifier,
        title=title,
        chapter=chapter,
        num=num,
        heading=heading,
        markdown=markdown,
    )


def to_file_map(sections: list[Section]) -> dict[str, str]:
    """Lay sections out as files, disambiguating genuine duplicate numbers.

    The US Code really does contain several sections that share a number --
    Congress enacted two different ``5 U.S.C. 3598``, and the Code itself says
    "Another section 3598 is set out after this one". They carry the same
    ``identifier`` too, so nothing in the source distinguishes them.

    Both are real law, so neither may be dropped. Later duplicates get an
    ordinal suffix (``sec-3598-2.md``). Document order is the printed order and
    is stable across release points, so the assignment does not churn.

    Args:
        sections: Rendered sections, in document order.

    Returns:
        Mapping of file path to contents.
    """
    files: dict[str, str] = {}
    used: dict[str, int] = {}
    for section in sections:
        path = section.path
        if path in used:
            used[path] += 1
            stem, _, suffix = path.rpartition(".")
            path = f"{stem}-{used[path]}.{suffix}"
        else:
            used[path] = 1
        files[path] = section.markdown
    return files


def render_title(xml_bytes: bytes, default_title: str = "") -> list[Section]:
    """Render every section in one title's USLM document.

    Args:
        xml_bytes: Raw contents of a ``uscNN.xml`` file.

    Returns:
        The rendered sections, in document order.
    """
    root = _safe_fromstring(xml_bytes)
    parents = {child: parent for parent in root.iter() for child in parent}

    # Each document *is* exactly one title, so the document-level answer is
    # authoritative and must win over the ancestor walk. Walking upwards finds
    # nested <title> elements that are divisions of a compiled act ("TITLE IV")
    # rather than Code titles, which is how sections end up in title-iv/.
    #
    # Appendix documents (usc05A, usc11a, usc18a, usc28a, usc50A) have no
    # <title> element at all -- they hold <appendix> under <uscDoc> -- and their
    # sections carry empty identifiers, so without this they landed in a
    # "title-00" bucket that the truncation guard then froze, since OLRC never
    # declares title 0 as affected.
    #
    # 57 of 58 documents carry a root identifier; usc50A.xml does not, which is
    # what default_title (taken from the archive member name) covers.
    doc_title = _title_from_identifier(root.get("identifier", "")) or default_title

    sections: list[Section] = []
    for element in root.iter(f"{_NS}section"):
        # Notes routinely reproduce the text of *other* statutes inside
        # <quotedContent>, and that quoted text carries its own <section> and
        # <title> elements. Those are not US Code sections: emitting them
        # invents files, and walking their ancestry picks up the quoted act's
        # "TITLE I" as a Code title, producing title-ii/ style directories.
        #
        # Note the test is ancestry, *not* presence of an identifier. OLRC omits
        # @identifier on plenty of genuine sections -- 747 of them in Title 42 at
        # release point 113-44, sitting directly under subchapter/chapter/title.
        # Filtering on the identifier would silently drop real law.
        title_num = ""
        chapter_num = ""
        quoted = False
        node = parents.get(element)
        while node is not None:
            tag = _tag(node)
            if tag in ("quotedContent", "note"):
                quoted = True
                break
            if tag == "chapter" and not chapter_num:
                chapter_num = _num_of(node)
            elif tag in ("title", "appendix") and not title_num:
                title_num = _num_of(node)
            node = parents.get(node)
        if quoted:
            continue

        # Document-level wins; ancestry is only a fallback.
        title_num = (
            doc_title
            or _title_from_identifier(element.get("identifier", ""))
            or title_num
        )

        # Sections inside appendices may legitimately lack a chapter.
        sections.append(render_section(element, title_num, chapter_num))
    return sections
