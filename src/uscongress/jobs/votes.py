"""Fetch the roll-call votes a measure's BILLSTATUS names.

This runs *inside* the measure build rather than as a pass of its own, and the
reason is the daily loop. ``uscongress update`` runs on GitHub Actions, which
holds no copy of ``data/``: it fetches what it needs, rebuilds the measures
govinfo reports as changed, and pushes only the branches whose SHA moved. If
votes came from a cache that only a local ``seed`` pass filled, CI would render
every measure's commits without them, get different bytes, and force-push the
voteless version over the good one -- every day, for ever. Nothing would report
an error, because rebuilding a measure and pushing it is exactly what that job
is supposed to do.

So the cache here is an optimisation and never state, which is the same rule
``data/raw`` follows everywhere else. A warm cache makes a rebuild of 160,190
branches cost no network; a cold one costs about 17,000 documents, and both
produce identical commits.

A published roll call does not change, so a cached one is never refetched. The
Senate does stamp a ``<modify_date>`` and does occasionally correct a vote after
publication; BILLSTATUS gives no signal when that happens, and refetching every
vote daily to catch it would spend 17,000 requests a day to find almost nothing.
Deleting the cached file is the way to pick a correction up.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx

from .. import config
from ..govinfo import GovInfoClient
from ..votes import RecordedVote, RollCall, parse

#: Status codes that mean the document is not there, as opposed to not there
#: *yet*. These are answered with a rendered marker rather than a failure; any
#: other error propagates, because a measure that silently loses a vote to a
#: timeout would be committed without it and published as complete.
_ABSENT = frozenset({403, 404, 410})


class VoteUnavailable(Exception):
    """A roll call BILLSTATUS names is not published where it says it is."""


def _cache_path(vote: RecordedVote) -> Path:
    """Local cache path for one roll call.

    Args:
        vote: The vote reference.

    Returns:
        Path under ``data/raw/votes/``.
    """
    return config.RAW_DIR / "votes" / vote.congress / f"{vote.key}.xml"


def looks_like_xml(payload: bytes) -> bool:
    """Report whether a payload is the roll call it claims to be.

    Both chambers serve a real 404 for a roll call that does not exist, so
    unlike govinfo the status code *is* evidence here. The body is checked
    anyway, for the same reason it is checked everywhere else in this project:
    the House Clerk's 404 body is 1,245 bytes of XHTML beginning
    ``<!DOCTYPE html``, which is close enough to a document to be cached under
    an ``.xml`` name and then fail to parse for ever afterwards.

    A House roll call carries its own DOCTYPE, but only after the XML
    declaration -- ``<?xml … ?>`` then ``<!DOCTYPE rollcall-vote …>`` -- so
    testing the declaration distinguishes the two cleanly.

    Args:
        payload: The response body.

    Returns:
        True if the payload begins an XML document.
    """
    return payload.removeprefix(b"\xef\xbb\xbf").lstrip()[:512].startswith(b"<?xml")


async def fetch(client: GovInfoClient, vote: RecordedVote) -> bytes:
    """Fetch one roll call, caching it on disk.

    Args:
        client: HTTP client.
        vote: The vote reference from BILLSTATUS.

    Returns:
        The raw XML bytes.

    Raises:
        VoteUnavailable: If the chamber does not publish the vote where
            BILLSTATUS says it does, or serves something that is not XML.
    """
    cached = _cache_path(vote)
    if cached.is_file() and cached.stat().st_size > 0:
        payload = cached.read_bytes()
        if looks_like_xml(payload):
            return payload
        # A poisoned entry, from a fetch made before this check existed or from
        # a run that was killed mid-write.
        cached.unlink()

    try:
        payload = await client.get_bytes(vote.url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in _ABSENT:
            raise VoteUnavailable(
                f"{vote.url} answered HTTP {exc.response.status_code}"
            ) from exc
        raise

    if not looks_like_xml(payload):
        raise VoteUnavailable(
            f"{vote.url} served {len(payload):,} bytes that are not XML"
        )

    cached.parent.mkdir(parents=True, exist_ok=True)
    # A unique temporary name, not `cached.with_suffix(".tmp")`. Measures are
    # built forty at a time and two of them can name the same roll call -- a
    # vote on a special rule belongs to the resolution and to the bill it
    # governs -- so a shared temporary name lets two writers interleave into
    # one file and rename the wreckage into place.
    handle, temporary = tempfile.mkstemp(dir=cached.parent, suffix=".tmp")
    try:
        with open(handle, "wb") as stream:
            stream.write(payload)
        Path(temporary).replace(cached)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return payload


async def load(
    client: GovInfoClient, references: tuple[RecordedVote, ...]
) -> tuple[tuple[RollCall, ...], tuple[tuple[RecordedVote, str], ...]]:
    """Fetch and parse every roll call named for one measure.

    Failures are returned rather than raised so that they can be *rendered*.
    A vote BILLSTATUS names and the chamber does not publish is an upstream
    fact, and it has to reach the commit as an explicit marker: if it were
    dropped instead, the same measure would render differently depending on
    whether a fetch happened to succeed, which is the one thing that must not
    vary between a local build and the daily loop.

    Transient failures do not come back here at all. They propagate, the
    measure is not rebuilt, and the watermark stays where it is -- advancing
    past a measure whose vote timed out is how a gap becomes permanent.

    Args:
        client: HTTP client.
        references: Votes named by BILLSTATUS.

    Returns:
        The votes that were retrieved, ordered as BILLSTATUS named them, and
        each vote that was not, paired with why. The reference is kept rather
        than only its description because it carries the date the marker is
        placed by.
    """
    rolls: list[RollCall] = []
    missing: list[tuple[RecordedVote, str]] = []
    for reference in references:
        try:
            payload = await fetch(client, reference)
            rolls.append(parse(payload, reference.chamber))
        except (VoteUnavailable, ValueError) as exc:
            missing.append((reference, str(exc)))
    return tuple(rolls), tuple(missing)
