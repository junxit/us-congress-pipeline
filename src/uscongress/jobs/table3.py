"""Ingest OLRC Table III: which US Code sections each public law touched.

This is what makes per-law attribution possible without executing a single
amendatory instruction. A release point closes over roughly five or six public
laws at once, so a release-point commit is not the effect of one law -- but
Table III maps ``public law section -> US Code section`` directly, so each
commit can carry trailers naming exactly which law changed what.

The archive is ~38 MB holding ~48,857 per-act XML files, laid out as
``{release-point}/{congress}/{act}.xml``.

**Table III lags the Code.** The bundle is published as of a release point
(``119-18`` at time of writing) while the Code itself is current through
``119-102``, roughly a year ahead. Attribution is therefore unavailable for the
most recent laws; the HTML Classification Tables are current and can fill the
gap later.

**Table III records present-day classification, not classification as of any
past snapshot.** PL 113-40 (2013) is listed under Title 54, which did not exist
until PL 113-287 created it in December 2014. A trailer therefore answers "where
do this law's provisions live now", not "what did this release point change".
Do not cross-check the two as though they measured the same thing; for what a
commit changed, read its diff.
"""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from defusedxml.ElementTree import fromstring

from .. import config
from ..govinfo import GovInfoClient

ARCHIVE_URL = "https://uscode.house.gov/table3/table3-xml-acts.zip"


@dataclass(frozen=True)
class Classification:
    """One public-law-section to US-Code-section mapping.

    Attributes:
        act_section: Section of the public law, e.g. ``1(a)``. Empty when Table
            III records the act as a whole.
        usc_title: US Code title, e.g. ``12``.
        usc_section: US Code section, e.g. ``1454`` or ``1721 nt`` for a note.
    """

    act_section: str
    usc_title: str
    usc_section: str

    @property
    def citation(self) -> str:
        """Human-readable citation, e.g. ``12 USC 1454``."""
        return f"{self.usc_title} USC {self.usc_section}"


def _text(element, tag: str) -> str:
    """Return a child element's stripped text, or an empty string.

    Args:
        element: Parent element.
        tag: Child tag name.

    Returns:
        The text content.
    """
    found = element.find(tag)
    return (found.text or "").strip() if found is not None else ""


def parse_act(xml_bytes: bytes) -> tuple[str, list[Classification]]:
    """Parse one Table III act file.

    Args:
        xml_bytes: Raw XML of a single ``<act>`` document.

    Returns:
        A ``(law_id, classifications)`` pair, where ``law_id`` looks like
        ``100-200``.
    """
    act = fromstring(xml_bytes)
    law_id = _text(act, "num") or act.get("search-key", "")
    entries = [
        Classification(
            act_section=_text(record, "act-section"),
            usc_title=_text(record, "united-states-code-title"),
            usc_section=_text(record, "united-states-code-section"),
        )
        for record in act.iter("record")
    ]
    return law_id, [e for e in entries if e.usc_title and e.usc_section]


async def fetch_archive(client: GovInfoClient) -> bytes:
    """Download the Table III bundle, caching it on disk.

    Args:
        client: HTTP client.

    Returns:
        Raw zip bytes.
    """
    cached = config.RAW_DIR / "table3" / "table3-xml-acts.zip"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_bytes()
    payload = await client.get_bytes(ARCHIVE_URL)
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(payload)
    tmp.rename(cached)
    return payload


def build_index(archive: bytes) -> dict[str, list[Classification]]:
    """Build a ``law_id -> classifications`` index from the bundle.

    Args:
        archive: Raw zip bytes.

    Returns:
        Mapping of law id (``118-2``) to every US Code section it touched.
    """
    index: dict[str, list[Classification]] = defaultdict(list)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for name in bundle.namelist():
            if not name.endswith(".xml"):
                continue
            law_id, entries = parse_act(bundle.read(name))
            if law_id:
                index[law_id].extend(entries)
    return dict(index)


def trailers(index: dict[str, list[Classification]], law_ids: list[str]) -> list[str]:
    """Render commit trailers attributing changes to specific laws.

    Args:
        index: Output of :func:`build_index`.
        law_ids: Laws this commit closes over, e.g. ``["119-4", "119-5"]``.

    Returns:
        Trailer lines, one per law, listing the US Code sections it touched.
        Laws absent from Table III are reported as such rather than omitted --
        silence would read as "changed nothing".
    """
    lines: list[str] = []
    for law_id in law_ids:
        entries = index.get(law_id)
        if entries is None:
            lines.append(f"Classified-By-PL-{law_id}: not yet in Table III")
            continue
        citations = sorted({e.citation for e in entries})
        lines.append(f"Classified-By-PL-{law_id}: {', '.join(citations)}")
    return lines


def summarize(index: dict[str, list[Classification]]) -> str:
    """Describe the index for logging.

    Args:
        index: Output of :func:`build_index`.

    Returns:
        A short human-readable summary.
    """
    total = sum(len(v) for v in index.values())
    with_section = sum(1 for v in index.values() for e in v if e.act_section)
    congresses = sorted({k.split("-")[0] for k in index if "-" in k}, key=int)
    return (
        f"{len(index):,} public laws, {total:,} classification records, "
        f"{with_section / max(total, 1):.1%} carry an act-section; "
        f"congresses {congresses[0]}-{congresses[-1]}"
    )


def load_index(path: Path | None = None) -> dict[str, list[Classification]] | None:
    """Load a previously cached index.

    Parsing the 48,857-file bundle takes long enough that repeating it on every
    build is wasteful, and the bundle only changes when OLRC republishes it.

    Args:
        path: Source; defaults to ``data/raw/table3/index.json``.

    Returns:
        The index, or None if no cache exists.
    """
    import json

    target = path or config.RAW_DIR / "table3" / "index.json"
    if not target.is_file():
        return None
    payload = json.loads(target.read_text(encoding="utf-8"))
    return {
        law: [
            Classification(e["act_section"], e["title"], e["section"]) for e in entries
        ]
        for law, entries in payload.items()
    }


def cache_index(index: dict[str, list[Classification]], path: Path | None = None) -> Path:
    """Persist the index as JSON for reuse by the build.

    Args:
        index: Output of :func:`build_index`.
        path: Destination; defaults to ``data/raw/table3/index.json``.

    Returns:
        The path written.
    """
    import json

    target = path or config.RAW_DIR / "table3" / "index.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        law: [
            {"act_section": e.act_section, "title": e.usc_title, "section": e.usc_section}
            for e in entries
        ]
        for law, entries in sorted(index.items())
    }
    target.write_text(json.dumps(payload, indent=0, sort_keys=True), encoding="utf-8")
    return target
