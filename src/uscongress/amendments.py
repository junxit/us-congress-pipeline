"""Read a bill's amendatory instructions, and execute the ones that can be.

A bill is not a diff. It is a list of instructions *about* existing law --
*strike subsection (k) and insert the following* -- and turning those into the
resulting text is an unsolved problem. This module does the part that is
solvable and says plainly where it stops.

**What the markup actually looks like**, measured rather than assumed, because
the obvious reading of it is wrong:

* ``<amendment-instruction>`` is **not** where instructions live. It appears in
  1 document in 400 of the 119th Congress, and holds the instruction attached
  to an engrossed *amendment*, not a bill's amendatory text. Every ordinary
  instruction is prose in a ``<text>``, ``<chapeau>`` or ``<continuation>``.
* The target is an ``<external-xref legal-doc="usc" parsable-cite="usc/42/2000e-2">``
  inside the instruction, or inside the chapeau above it. Sub-instructions
  inherit it: *"Section 703 ... (42 U.S.C. 2000e-2) is amended— (1) by striking
  ...; (2) by inserting ..."* names the section once for all of them.
* The text a bill strikes or inserts is delimited by ``<quote>`` when it is a
  literal, and described in prose when it is not. Both forms occur constantly
  and they are not interchangeable: *striking ``; and``* is executable and
  *striking subsection (k)* is not, because the subsection's words are in the
  US Code and not in the bill.

Measured over 6,433 instructions in 998 documents sampled from the 108th,
110th, 113th, 116th and 119th Congresses, **24.0% can be executed**. Of the
rest: 36.9% refer to the law by structure rather than quoting it, 21.4% name no
machine-readable section at all, and 17.7% quote one side of a substitution and
describe the other.

**How much of that is upstream rather than inherent** is the more useful
number, and it is most of it. An instruction can only be placed if GPO tagged
the citation it names, and whether they did is a fact about the year: sampled
at 1,500 documents per Congress, 64% of the 108th's carry a machine-readable US
Code citation, 55% of the 113th's -- and 5% of the 111th's and 5% of the
112th's. So the share of instructions carried out runs from about 1% in the
112th to about 23% in the 108th with no change in the reading of them at all.

That 78.6% of instructions *do* carry a machine-readable reference sits oddly
beside the ~49% this project has quoted since the beginning, which came from
seven bills. The two are not the same measurement and the older one is not
reproducible from here -- it does not say what it counted as an instruction,
and the denominator is what decides the answer. Both are recorded rather than
one quietly replacing the other. The conclusion is unchanged either way, and
if anything firmer: carrying a citation is not the hard part, and four
instructions in five still cannot be carried out.

**Only the executable shape is executed here**, and the reason is the daily loop.
Reading the target section out of ``us-congress-code`` would divide the build in
two: the loop runs on GitHub Actions, which holds no copy of the corpus, so CI
would render *unapplied* where a local build rendered a result, and force-push
the degraded version over the good one every day with nothing reporting an
error. Everything under ``derived/`` is therefore a pure function of the bill
document, and the executed subset is exactly the subset that can be checked
against the bill itself.

Nothing here is authoritative. It is marked derived wherever it is written.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring as _safe_fromstring

from .xmlrepair import repair

#: Elements that carry an instruction's prose.
_PROSE = ("text", "chapeau", "continuation")

#: Where a walk up the tree looking for a citation stops. A bill amends many
#: sections of law, each under its own ``<section>``; without a boundary the
#: search would hand section 4's citation to an instruction in section 9.
_BOUNDARY = ("section", "bill", "resolution", "amendment-doc", "legis-body")

#: Marks a ``<quote>``'s boundaries while an element is flattened, so a literal
#: can be recovered afterwards. Both are unprintable and cannot occur in
#: federal text.
_OPEN, _CLOSE = "\x01", "\x02"

#: An element is an amendatory instruction only if it names an *operation*.
#:
#: A bare "is amended" is not one. Congress writes *"Section 205 (16 U.S.C.
#: 7125) is amended—"* and then lists the operations beneath it, so the opening
#: line declares the target and does nothing itself. Counting it as an
#: instruction added one unapplied row per amendment whose reason -- "refers to
#: the law by structure" -- was not true of it, because it does not refer to
#: anything: it is a heading. Whole-section replacement is matched explicitly,
#: since *"is amended to read as follows"* really is an operation.
_OPERATION = re.compile(
    r"\bby (striking|inserting|adding|redesignating|amending|repealing)\b"
    r"|\b(is|are) repealed\b"
    r"|\b(is|are) amended to read\b",
    re.IGNORECASE,
)

#: ``striking "A" and inserting "B"`` -- the executable shape.
#:
#: A short qualifier is allowed between the two literals, and it has to be:
#: *striking "2011" **each place it appears** and inserting "2012"* is one of
#: the commonest instructions Congress writes, and requiring "and inserting" to
#: follow the first quotation immediately dropped every one of them into the
#: unapplied pile with a reason that was not true of them. The gap may not
#: contain a quotation itself, which is what stops the match running from one
#: instruction into the next one's literals.
_REPLACE = re.compile(
    rf"strik\w*\s*{_OPEN}(.*?){_CLOSE}"
    rf"[^{_OPEN}{_CLOSE}]{{0,90}}?"
    rf"\band\s+insert\w*\s*(?:in lieu thereof\s*)?{_OPEN}(.*?){_CLOSE}",
    re.DOTALL | re.IGNORECASE,
)

_STRIKE_LITERAL = re.compile(rf"strik\w*\s*{_OPEN}(.*?){_CLOSE}", re.DOTALL | re.IGNORECASE)
_INSERT_LITERAL = re.compile(rf"insert\w*\s*(?:in lieu thereof\s*)?{_OPEN}(.*?){_CLOSE}", re.DOTALL | re.IGNORECASE)

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Target:
    """A place in the US Code an instruction names.

    Attributes:
        title: Title number, e.g. ``42``.
        section: Section identifier, e.g. ``2000e-2``.
    """

    title: str
    section: str

    @property
    def label(self) -> str:
        """The citation as it is ordinarily written."""
        return f"{self.title} U.S.C. § {self.section}"

    @property
    def path(self) -> str:
        """Where the section lives in ``us-congress-code``.

        Written down so a reader can follow the reference into the repository
        that holds the law itself, which is the only place the surrounding text
        exists.
        """
        return f"title-{self.title}/sec-{self.section}.md"


@dataclass(frozen=True)
class Instruction:
    """One amendatory instruction, and what could be made of it.

    Attributes:
        text: The instruction as the bill writes it, whitespace collapsed.
        target: The US Code section it names, or None when the bill names none
            a machine can read.
        operation: What it does -- ``replace``, ``strike``, ``insert``,
            ``add-at-end``, ``redesignate``, ``repeal`` or ``amend``.
        struck: The exact text removed, when the bill quotes it.
        inserted: The exact text added, when the bill quotes it.
        applied: Whether the result follows from the bill alone.
        reason: Why it does not, when it does not.
    """

    text: str
    target: Target | None
    operation: str
    struck: str
    inserted: str
    applied: bool
    reason: str

    @property
    def result(self) -> str:
        """The amended fragment, for an instruction that could be executed."""
        return self.inserted if self.applied else ""


def _tag(element: ET.Element) -> str:
    """Return an element's local name.

    Args:
        element: Any element.

    Returns:
        The tag without its namespace.
    """
    return element.tag.rsplit("}", 1)[-1]


def _flatten(element: ET.Element) -> str:
    """Render an element's text with ``<quote>`` boundaries preserved.

    ``itertext`` loses where a quotation started and stopped, and that boundary
    is the whole difference between *striking ``; and``* -- which this module
    can execute -- and *striking the period at the end*, which it cannot.

    Args:
        element: The element to flatten.

    Returns:
        Its text, with quotations wrapped in sentinels.
    """
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if _tag(node) == "quote":
            parts.append(_OPEN + "".join(node.itertext()) + _CLOSE)
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if node.tail:
            parts.append(node.tail)

    if element.text:
        parts.append(element.text)
    for child in element:
        walk(child)
    return "".join(parts)


def _clean(value: str) -> str:
    """Collapse whitespace and restore the quotation marks.

    The marks are markup rather than characters in the source, so flattening
    drops them -- and *striking “; and”* becomes *striking ; and*, which reads
    as gibberish and loses the boundary of the quoted term. They are put back
    the way :func:`uscongress.billtext._texts` puts them back, so an instruction
    reproduced here and the same instruction in ``bill.md`` read alike.

    Args:
        value: Flattened text.

    Returns:
        Readable text.
    """
    restored = value.replace(_OPEN, "“").replace(_CLOSE, "”")
    return _WS.sub(" ", restored).strip()


def _literal(value: str) -> str:
    """Collapse whitespace in a quoted literal, without adding marks.

    The captured text sits between the marks rather than inside them, so it is
    rendered bare -- in a table cell that is already delimited.

    Args:
        value: The text between two quote sentinels.

    Returns:
        The literal.
    """
    return _WS.sub(" ", value.replace(_OPEN, "“").replace(_CLOSE, "”")).strip()


def _target_of(
    element: ET.Element, parents: dict[ET.Element, ET.Element]
) -> Target | None:
    """Find the US Code section an instruction applies to.

    The citation is written once, in the chapeau that opens the amendment, and
    every sub-instruction beneath it inherits it. So the search walks up -- but
    stops at the enclosing ``<section>``, because a bill amends many sections
    of law and without a boundary an instruction would inherit the citation of
    whichever amendment happened to come before it.

    Args:
        element: The instruction's element.
        parents: Child-to-parent map for the document.

    Returns:
        The target, or None when nothing in scope names one.
    """
    node: ET.Element | None = element
    while node is not None:
        for xref in node.iter():
            if _tag(xref) != "external-xref":
                continue
            if xref.get("legal-doc") != "usc":
                continue
            cite = (xref.get("parsable-cite") or "").split("/")
            if len(cite) >= 3 and cite[0] == "usc" and cite[1] and cite[2]:
                return Target(title=cite[1], section=cite[2])
        if _tag(node) in _BOUNDARY:
            return None
        node = parents.get(node)
    return None


def _classify(body: str) -> str:
    """Name the operation an instruction performs.

    Args:
        body: The instruction's flattened prose.

    Returns:
        A short operation name.
    """
    lowered = body.lower()
    if _REPLACE.search(body):
        return "replace"
    if "redesignat" in lowered:
        return "redesignate"
    if "repeal" in lowered:
        return "repeal"
    if "adding at the end" in lowered or "add at the end" in lowered:
        return "add-at-end"
    if "strik" in lowered and "insert" in lowered:
        return "replace"
    if "strik" in lowered:
        return "strike"
    if "insert" in lowered:
        return "insert"
    return "amend"


def read_instructions(xml_bytes: bytes) -> tuple[Instruction, ...]:
    """Read every amendatory instruction in one bill document.

    Args:
        xml_bytes: Raw bill XML.

    Returns:
        The instructions, in document order. Empty for a bill that amends
        nothing -- a resolution congratulating a team, or an original Act that
        adds law without touching any.

    Raises:
        ValueError: If the document cannot be parsed at all.
    """
    root = _safe_fromstring(repair(xml_bytes)[0])
    parents = {child: parent for parent in root.iter() for child in parent}

    found: list[Instruction] = []
    for element in root.iter():
        if _tag(element) not in _PROSE:
            continue
        body = _flatten(element)
        if not _OPERATION.search(_clean(body)):
            continue

        text = _clean(body)
        target = _target_of(element, parents)
        operation = _classify(body)

        replace = _REPLACE.search(body)
        struck = _literal(replace.group(1)) if replace else ""
        inserted = _literal(replace.group(2)) if replace else ""
        if not replace:
            strike_only = _STRIKE_LITERAL.search(body)
            insert_only = _INSERT_LITERAL.search(body)
            struck = _literal(strike_only.group(1)) if strike_only else ""
            inserted = _literal(insert_only.group(1)) if insert_only else ""

        applied, reason = _verdict(target, replace is not None, struck, inserted)
        found.append(
            Instruction(
                text=text,
                target=target,
                operation=operation,
                struck=struck,
                inserted=inserted,
                applied=applied,
                reason=reason,
            )
        )
    return tuple(found)


def _verdict(
    target: Target | None, is_replacement: bool, struck: str, inserted: str
) -> tuple[bool, str]:
    """Decide whether an instruction's result follows from the bill alone.

    The bar is deliberately high and the reasons are specific. "Could not be
    applied" on its own tells a reader nothing about whether the pipeline is
    weak or the bill is simply not written in a form anything could execute.

    Args:
        target: The section named, if any.
        is_replacement: Whether the bill quotes both sides of a substitution.
        struck: The literal removed.
        inserted: The literal added.

    Returns:
        Whether it was executed, and why not when it was not.
    """
    if target is None:
        return False, "the bill names no machine-readable US Code section"
    if is_replacement and struck and inserted:
        return True, ""
    if struck and inserted:
        # Both sides are quoted but not as one substitution -- two separate
        # operations in one sentence, or a strike and an insertion at different
        # places. Saying "refers to the law by structure" here would be false.
        return (
            False,
            (
                "the bill quotes text on both sides, but not as a single "
                "substitution this could carry out"
            ),
        )
    if struck and not inserted:
        return False, "the bill quotes the text struck but describes what replaces it"
    if inserted and not struck:
        return False, "the bill quotes the text inserted but describes where it goes"
    return (
        False,
        (
            "the instruction refers to the law by structure rather than quoting "
            "it, so the words it changes are in the US Code and not in this bill"
        ),
    )


def derived_markdown(
    citation: str, congress: str, version: str, instructions: tuple[Instruction, ...]
) -> str:
    """Render ``derived/amendments.md`` for one version of a measure.

    Args:
        citation: The measure, e.g. ``H.R. 588``.
        congress: Congress number.
        version: Version label.
        instructions: What the bill instructs.

    Returns:
        Markdown, or an empty string when the bill amends nothing -- in which
        case no file is written at all, rather than one saying nothing.
    """
    if not instructions:
        return ""

    applied = [i for i in instructions if i.applied]
    lines = [
        "---",
        f"measure: {citation}",
        f"congress: {congress}",
        f"version: {version}",
        "derived: true",
        "---",
        "",
        f"# What {citation} would do to existing law",
        "",
        "> **Derived, unofficial, and not law.** This file is generated from the",
        "> bill's own amendatory instructions. It is not published by any",
        "> government body, it has not been reviewed, and it is wrong wherever",
        "> the instruction was more subtle than the reading of it. Nothing here",
        "> should be relied on; read `bill.md` beside it, and the US Code for the",
        "> text being amended.",
        "",
        (
            f"{len(instructions):,} amendatory instruction"
            f"{'s' if len(instructions) != 1 else ''}. "
            f"{len(applied):,} executed, "
            f"{len(instructions) - len(applied):,} stated and not applied."
        ),
        "",
        "An instruction is executed here only when the bill states **both** the",
        "text removed and the text inserted, so the result follows from this",
        "document alone. Where a bill says *strike subsection (k)*, the words it",
        "removes are in the US Code and not in the bill, and no attempt is made",
        "to guess them.",
        "",
    ]

    if applied:
        lines += ["## Executed", ""]
        for instruction in applied:
            where = instruction.target.label if instruction.target else "(no citation)"
            lines += [
                f"### {where}",
                "",
                f"> {instruction.text}",
                "",
                "| | |",
                "|---|---|",
                f"| Removed | `{instruction.struck}` |",
                f"| Inserted | `{instruction.inserted}` |",
                "",
            ]

    unapplied = [i for i in instructions if not i.applied]
    if unapplied:
        lines += [
            "## Stated, not applied",
            "",
            "Each of these is reproduced as the bill writes it, with the reason",
            "it was not executed. They are listed rather than dropped: an",
            "instruction that vanished would leave this file reading as a",
            "complete account of the bill's effect, which it is not.",
            "",
            "| Target | Operation | Instruction | Why not applied |",
            "|---|---|---|---|",
        ]
        for instruction in unapplied:
            where = f"`{instruction.target.label}`" if instruction.target else "—"
            text = instruction.text.replace("|", "/")
            if len(text) > 240:
                text = text[:237] + "…"
            lines.append(
                f"| {where} | {instruction.operation} | {text} | {instruction.reason} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
