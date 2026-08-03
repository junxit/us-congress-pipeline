"""Repair structurally broken USLM documents from OLRC.

Some official release-point archives contain XML that is simply not well
formed. In ``xml_uscAll@113-46``, ``usc16.xml`` emits this at the top of a
26 MB file::

    <note ...>            <- opened
    ...quoted sections...
    </quotedContent>      <- never opened
    </p>                  <- never opened
    </note>               <- matches
    </notes>              <- never opened
    </section>            <- never opened

The generator emitted closing tags for elements it never opened. A strict
parser stops at the first mismatch; ``lxml``'s ``recover=True`` is worse than
useless here -- it salvaged 14 sections out of ~5,100, silently discarding the
rest of the file.

So instead of tolerating truncation, the tag balance is repaired directly:
scan the document, track the open-element stack, and drop only end tags that
cannot match it. Nothing else is touched, so no textual content is lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tags, comments, CDATA, declarations and processing instructions. Attribute
# values may contain '>', so they are matched explicitly rather than with [^>]*.
_TOKEN = re.compile(
    rb"""
    <!--.*?-->                     # comment
  | <!\[CDATA\[.*?\]\]>            # CDATA
  | <\?.*?\?>                      # processing instruction
  | <!DOCTYPE[^>\[]*(\[.*?\])?[^>]*>   # doctype
  | <\s*/\s*(?P<close>[\w:.-]+)\s*>    # end tag
  | <\s*(?P<open>[\w:.-]+)             # start tag name
      (?:"[^"]*"|'[^']*'|[^>"'])*      # attributes, quotes respected
      >
    """,
    re.S | re.X,
)


@dataclass(frozen=True)
class RepairReport:
    """What a repair pass changed.

    Attributes:
        dropped_end_tags: Names of unmatched end tags that were removed.
        closed_implicitly: Names of unclosed elements that were closed.
        changed: Whether the document was modified at all.
    """

    dropped_end_tags: tuple[str, ...]
    closed_implicitly: tuple[str, ...]
    changed: bool

    def describe(self) -> str:
        """Return a one-line summary for logging."""
        if not self.changed:
            return "well formed"
        parts = []
        if self.dropped_end_tags:
            counts: dict[str, int] = {}
            for name in self.dropped_end_tags:
                counts[name] = counts.get(name, 0) + 1
            detail = ", ".join(f"</{k}>x{v}" for k, v in sorted(counts.items()))
            parts.append(f"dropped {len(self.dropped_end_tags)} unmatched end tags: {detail}")
        if self.closed_implicitly:
            parts.append(f"closed {len(self.closed_implicitly)} unclosed elements")
        return "; ".join(parts)


def repair(xml_bytes: bytes) -> tuple[bytes, RepairReport]:
    """Drop end tags that cannot match the open-element stack.

    Only unmatched end tags are removed. Start tags, attributes, text, comments
    and CDATA are passed through untouched, so no content is lost.

    Args:
        xml_bytes: Raw XML, possibly malformed.

    Returns:
        A ``(repaired_bytes, report)`` pair. If the document was already
        balanced, the original bytes are returned unchanged.
    """
    out: list[bytes] = []
    stack: list[str] = []
    dropped: list[str] = []
    closed: list[str] = []
    cursor = 0

    for match in _TOKEN.finditer(xml_bytes):
        out.append(xml_bytes[cursor : match.start()])
        cursor = match.end()
        token = match.group(0)

        close = match.group("close")
        if close is not None:
            name = close.decode("utf-8", "replace")
            if stack and stack[-1] == name:
                stack.pop()
                out.append(token)
            elif name in stack:
                # An intervening element was left unclosed. Close the strays
                # implicitly so the document stays balanced.
                while stack and stack[-1] != name:
                    stray = stack.pop()
                    closed.append(stray)
                    out.append(f"</{stray}>".encode())
                stack.pop()
                out.append(token)
            else:
                # Nothing to close -- this is the OLRC defect. Drop it.
                dropped.append(name)
            continue

        open_name = match.group("open")
        if open_name is not None:
            # Self-closing tags must not be pushed. USLM uses them heavily --
            # this file alone has 32 <content/> plus <col/>, <td/>, <num/> and
            # <br/>. Detect them from the token itself: letting the attribute
            # pattern decide swallows the slash and drifts the whole stack.
            if not token.rstrip().endswith(b"/>"):
                stack.append(open_name.decode("utf-8", "replace"))
        out.append(token)

    out.append(xml_bytes[cursor:])

    if not dropped and not closed:
        return xml_bytes, RepairReport((), (), False)
    return b"".join(out), RepairReport(tuple(dropped), tuple(closed), True)
