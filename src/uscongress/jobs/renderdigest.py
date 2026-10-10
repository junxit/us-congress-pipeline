"""Hash everything the pipeline renders from the cache, to compare two toolchains.

A changed byte in a rendered file is a changed SHA on a published commit, so a
new interpreter or dependency is safe only if it renders what the old one did.
This is how the move to Python 3.15 was decided on 2026-10-10: the cache was
rendered under 3.14 and under 3.15 and every output hashed -- 520,091 files from
all 137 Statutes volumes, two whole US Code release points, every roll call, and
samples of the bills and the Record -- and not one differed, down to the
parser's error text for five malformed upstream bills.

Run it once per environment, then compare::

    uv run uscongress render-digest /tmp/a
    uv --directory <worktree> run uscongress render-digest /tmp/b --raw <here>/data/raw
    uv run uscongress render-compare /tmp/a /tmp/b

It reads ``data/raw`` and nothing else. A cache miss is recorded, never fetched:
a fetch would hand the two runs different inputs, and the comparison would then
measure govinfo rather than the toolchain.

What is hashed is what a commit carries -- each bill version's ``bill.md``,
``metadata.md``, derived documents, vote documents and commit message; every US
Code file at both granularities ``seed-code`` offers; every session law; every
roll call; each Record granule's text and each issue day's MODS enrichment. The
Record's issue files are not, because building an ``Issue`` needs the network
listing, and what sits on top of these two is string formatting.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

from .. import amendments, config, statutetext
from .. import votes as votes_text
from ..billtext import render_bill
from ..render import to_file_map
from . import bills, record, uscode
from . import votes as votes_job

#: Everything this can render, in the order it renders them: cheapest first, so
#: a run stopped early has still produced something comparable.
CORPORA = ("votes", "statutes", "uscode", "bills", "record")


class NotCached(Exception):
    """A vote the cache does not hold. Fetching it is refused; see the module."""


def digest(value: object) -> str:
    """Hash text, bytes, or a JSON-serializable structure.

    Args:
        value: What to hash. Structures are serialized with sorted keys, so two
            equal mappings hash alike whatever order they were built in.

    Returns:
        The hex SHA-256.
    """
    if isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = json.dumps(value, sort_keys=True, default=repr, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def _failure(exc: BaseException) -> str:
    """Record an exception as a value two runs can compare.

    Args:
        exc: What was raised.

    Returns:
        Its type and message. An input that fails identically on both sides is
        as much a match as one that renders identically.
    """
    return f"ERROR {type(exc).__name__}: {exc}"[:300]


def every(items, step: int) -> list:
    """Sample every ``step``-th item in sorted order.

    Deterministic, and nested: every 50th item is also every 5th, so a quick run
    can be checked against a thorough one on the items they share.

    Args:
        items: What to sample.
        step: Keep one in this many.

    Returns:
        The sample.
    """
    return sorted(items)[::step]


def _cached_rolls(references) -> tuple[tuple, tuple]:
    """Load a measure's roll calls from the cache, as ``votes.load`` would.

    Args:
        references: The votes BILLSTATUS names.

    Returns:
        ``(rolls, missing)`` in the shape ``votes.load`` returns.

    Raises:
        NotCached: If a vote is not in the cache. ``votes.load`` would fetch it.
    """
    rolls = []
    missing = []
    for reference in references:
        path = votes_job._cache_path(reference)  # noqa: SLF001
        payload = path.read_bytes() if path.is_file() else b""
        if not votes_job.looks_like_xml(payload):
            raise NotCached(reference.key)
        try:
            rolls.append(votes_text.parse(payload, reference.chamber))
        except ValueError as exc:
            missing.append((reference, str(exc)))
    return tuple(rolls), tuple(missing)


def bills_corpus(raw: Path, step: int) -> dict[str, str]:
    """Hash each sampled measure's files, built as ``bills._render_measure`` builds them.

    Args:
        raw: The cache root.
        step: Keep every ``step``-th measure of each Congress.

    Returns:
        Output key to digest, or to a recorded failure.
    """
    out: dict[str, str] = {}
    root = raw / "bills"
    if not root.is_dir():
        return out
    for congress in sorted(p for p in root.iterdir() if p.is_dir()):
        for status in every(congress.glob("BILLSTATUS-*.xml"), step):
            key = status.stem
            try:
                measure = bills.parse_status(status.read_bytes())
            except Exception as exc:  # noqa: BLE001 - recorded, so both sides can match it
                out[key] = _failure(exc)
                continue
            try:
                rolls, unavailable = _cached_rolls(measure.recorded_votes)
            except NotCached as exc:
                out[key] = f"SKIP vote not cached: {exc}"
                continue
            measure = replace(measure, rolls=rolls, votes_unavailable=unavailable)
            rendered = []
            derived = None
            for version in measure.versions:
                text = congress / "text" / version.url.rsplit("/", 1)[-1]
                vkey = f"{key}/{text.name}"
                if not text.is_file():
                    out[vkey] = "MISSING"
                    continue
                body = text.read_bytes()
                try:
                    doc = render_bill(body, legis_num=measure.citation)
                except Exception as exc:  # noqa: BLE001
                    out[vkey] = _failure(exc)
                    continue
                try:
                    instructions = amendments.read_instructions(body)
                except Exception:  # noqa: BLE001 - as bills does: the text still renders
                    instructions = ()
                derived = bills._count_instructions(instructions)  # noqa: SLF001
                files = {
                    "bill.md": doc.markdown,
                    "metadata.md": bills.metadata_markdown(measure, version),
                    **bills.derived_documents(measure, version, instructions),
                    **bills.vote_documents(measure, version),
                }
                rendered.append((vkey, version, files))
            # The commit message reads the measure's derived counts, which bills
            # sets only after every version has rendered.
            if rendered:
                measure = replace(measure, derived=derived)
            for vkey, version, files in rendered:
                for name, content in files.items():
                    out[f"{vkey}/{name}"] = digest(content)
                out[f"{vkey}/COMMIT_MESSAGE"] = digest(bills.commit_message(measure, version))
    return out


def _release_point_order(name: str) -> tuple:
    """Order release point archives by Congress, then law, then any suffix.

    Args:
        name: Archive file name, e.g. ``xml_uscAll@119-102.zip``.

    Returns:
        A sort key.
    """
    match = re.search(r"@(\d+)-(\d+)(.*)\.zip$", name)
    return (int(match[1]), int(match[2]), match[3]) if match else (0, 0, name)


def uscode_corpus(raw: Path, archives: list[str] | None = None) -> dict[str, str]:
    """Hash every file release point archives render to, at both granularities.

    Args:
        raw: The cache root.
        archives: Archive file names. Defaults to the oldest and the newest in
            the cache, which between them span both USLM generations.

    Returns:
        Output key to digest.
    """
    out: dict[str, str] = {}
    root = raw / "uscode"
    if not root.is_dir():
        return out
    if not archives:
        cached = sorted((p.name for p in root.glob("*.zip")), key=_release_point_order)
        archives = sorted({cached[0], cached[-1]}, key=_release_point_order) if cached else []
    for name in archives:
        sections, damage, retired = uscode.render_archive((root / name).read_bytes())
        for path, content in to_file_map(sections).items():
            out[f"{name}/section/{path}"] = digest(content)
        for path, content in uscode._group_by_chapter(sections).items():  # noqa: SLF001
            out[f"{name}/chapter/{path}"] = digest(content)
        out[f"{name}/DAMAGE"] = digest(damage)
        out[f"{name}/RETIRED"] = digest(sorted(retired))
        out[f"{name}/SECTIONS"] = digest([s.identifier for s in sections])
    return out


def statutes_corpus(raw: Path) -> dict[str, str]:
    """Hash every session law in every cached Statutes volume.

    Args:
        raw: The cache root.

    Returns:
        Output key to digest.
    """
    out: dict[str, str] = {}
    root = raw / "statutes"
    for path in every(root.glob("STATUTE-*.xml"), 1) if root.is_dir() else []:
        try:
            render = statutetext.render_volume(path.read_bytes(), int(path.stem.split("-")[1]))
        except Exception as exc:  # noqa: BLE001
            out[path.stem] = _failure(exc)
            continue
        for name, content in statutetext.to_file_map(render.laws).items():
            out[f"{path.stem}/{name}"] = digest(content)
        out[f"{path.stem}/META"] = digest([render.presidential, render.undated, render.repair])
    return out


def votes_corpus(raw: Path) -> dict[str, str]:
    """Hash every cached roll call, rendered as a bill branch writes it.

    Args:
        raw: The cache root.

    Returns:
        Output key to digest.
    """
    out: dict[str, str] = {}
    root = raw / "votes"
    chambers = {"house": votes_text.HOUSE, "senate": votes_text.SENATE}
    for path in every(root.glob("*/*.xml"), 1) if root.is_dir() else []:
        try:
            roll = votes_text.parse(path.read_bytes(), chambers[path.name.split("-")[0]])
            out[path.stem] = digest(votes_text.roll_markdown(roll))
        except Exception as exc:  # noqa: BLE001
            out[path.stem] = _failure(exc)
    return out


def record_corpus(raw: Path, step: int) -> dict[str, str]:
    """Hash granule text and MODS enrichment for each sampled issue day.

    Args:
        raw: The cache root.
        step: Keep every ``step``-th issue day of each Congress.

    Returns:
        Output key to digest.
    """
    out: dict[str, str] = {}
    root = raw / "record"
    if not root.is_dir():
        return out
    for congress in sorted(p for p in root.iterdir() if (p / "html").is_dir()):
        for day in every((congress / "html").iterdir(), step):
            for page in sorted(day.glob("*.htm")):
                try:
                    out[f"{day.name}/{page.name}"] = digest(record.granule_text(page.read_bytes()))
                except Exception as exc:  # noqa: BLE001
                    out[f"{day.name}/{page.name}"] = _failure(exc)
            mods = congress / "mods" / f"{day.name}.xml"
            if mods.is_file():
                try:
                    out[f"{day.name}/MODS"] = digest(record.parse_mods(mods.read_bytes()))
                except Exception as exc:  # noqa: BLE001
                    out[f"{day.name}/MODS"] = _failure(exc)
    return out


def run(
    out: Path,
    raw: Path | None = None,
    corpora: tuple[str, ...] = CORPORA,
    bills_step: int = 5,
    record_step: int = 10,
    archives: list[str] | None = None,
) -> dict[str, dict]:
    """Render the selected corpora and write one manifest per corpus into ``out``.

    Args:
        out: Directory for ``<corpus>.json`` manifests and ``meta.json``.
        raw: Cache to read. Defaults to this checkout's ``data/raw``; a second
            environment in a worktree points it back here, since a worktree has
            no ``data/`` of its own.
        corpora: Which of :data:`CORPORA` to render.
        bills_step: Keep every Nth measure of each Congress.
        record_step: Keep every Nth Record issue day of each Congress.
        archives: US Code archives to render; see :func:`uscode_corpus`.

    Returns:
        What ``meta.json`` records: the interpreter, and per corpus how many
        outputs it produced and in how long.
    """
    raw = raw or config.RAW_DIR
    out.mkdir(parents=True, exist_ok=True)
    plan = {
        "votes": lambda: votes_corpus(raw),
        "statutes": lambda: statutes_corpus(raw),
        "uscode": lambda: uscode_corpus(raw, archives),
        "bills": lambda: bills_corpus(raw, bills_step),
        "record": lambda: record_corpus(raw, record_step),
    }
    meta: dict[str, dict] = {"python": {"version": sys.version.split()[0]}}
    # Vote paths are derived from config.RAW_DIR inside votes.py, so it is
    # pointed at the cache being read and put back afterwards.
    previous = config.RAW_DIR
    config.RAW_DIR = raw
    try:
        for name in corpora:
            started = time.perf_counter()
            manifest = plan[name]()
            seconds = round(time.perf_counter() - started, 1)
            (out / f"{name}.json").write_text(json.dumps(manifest, sort_keys=True))
            meta[name] = {"outputs": len(manifest), "seconds": seconds}
            print(f"{name}: {len(manifest):,} outputs in {seconds}s", flush=True)
            if not manifest:
                # Two empty manifests compare as identical, which proves nothing.
                print(f"  WARNING: nothing cached for {name} under {raw}", flush=True)
    finally:
        config.RAW_DIR = previous
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def compare(left: Path, right: Path) -> int:
    """Compare two runs corpus by corpus and print what differs.

    Args:
        left: One run's output directory.
        right: The other's.

    Returns:
        How many outputs differ, counting one present on only one side.
    """
    differing = 0
    names = ({p.name for p in left.glob("*.json")} | {p.name for p in right.glob("*.json")}) - {
        "meta.json"
    }
    for name in sorted(names):
        manifest, other = left / name, right / name
        if not (manifest.is_file() and other.is_file()):
            print(f"{Path(name).stem}: only in {left if manifest.is_file() else right}", flush=True)
            differing += 1
            continue
        x = json.loads(manifest.read_text())
        y = json.loads(other.read_text())
        keys = x.keys() | y.keys()
        differ = sorted(k for k in keys if x.get(k) != y.get(k))
        failures = sum(1 for v in x.values() if v.startswith(("ERROR", "SKIP", "MISSING")))
        differing += len(differ)
        print(
            f"{manifest.stem}: {len(keys):,} outputs, {len(keys) - len(differ):,} identical, "
            f"{len(differ):,} differ ({failures:,} recorded failures)",
            flush=True,
        )
        for key in differ[:5]:
            print(f"  {key}\n    {x.get(key)}\n    {y.get(key)}", flush=True)
    return differing
