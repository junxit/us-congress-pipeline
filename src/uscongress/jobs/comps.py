"""Snapshot the govinfo Statute Compilations (COMPS) collection.

COMPS holds non-codified law "as amended through Public Law N" -- the Social
Security Act, for instance, which bills amend by act section rather than by US
Code citation.

**govinfo replaces these packages in place and keeps no version archive.** Once
a compilation is superseded, the previous text is gone from the internet. Every
day without a snapshot is history that cannot be recovered later, which is why
this is the first job in the project.

Storage is content-addressed: blobs live under ``data/comps/objects/`` keyed by
SHA-256, and each run writes a manifest under ``data/comps/snapshots/``. Running
twice on an unchanged collection costs one manifest, not another 270 MB.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..govinfo import BulkFile, GovInfoClient


def _blob_path(digest: str) -> Path:
    """Return the content-addressed path for a blob.

    Args:
        digest: Hex SHA-256 of the content.

    Returns:
        Path under the objects directory, fanned out by the first two hex chars.
    """
    return config.COMPS_OBJECTS_DIR / digest[:2] / digest


def _store(content: bytes) -> tuple[str, bool]:
    """Write content to the blob store if not already present.

    Args:
        content: Raw file bytes.

    Returns:
        A ``(digest, is_new)`` pair, where ``is_new`` is False if an identical
        blob already existed.
    """
    digest = hashlib.sha256(content).hexdigest()
    path = _blob_path(digest)
    if path.exists():
        return digest, False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so an interrupted run never leaves a partial blob that a
    # later run would trust.
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.rename(path)
    return digest, True


async def _fetch_one(
    client: GovInfoClient,
    entry: BulkFile,
    manifest: dict[str, dict[str, object]],
    lock: asyncio.Lock,
    counters: dict[str, int],
) -> None:
    """Fetch a single COMPS package and record it in the manifest.

    Args:
        client: Shared govinfo client.
        entry: The bulk listing entry to fetch.
        manifest: Mutable manifest being built, keyed by package id.
        lock: Guards manifest and counter mutation.
        counters: Mutable tallies for progress reporting.
    """
    package_id = entry.name.removesuffix(".xml")
    try:
        content = await client.get_bytes(entry.url)
    except Exception as exc:  # noqa: BLE001 - one bad package must not kill the run
        async with lock:
            counters["failed"] += 1
            manifest[package_id] = {"error": f"{type(exc).__name__}: {exc}"}
        return

    digest, is_new = _store(content)
    async with lock:
        counters["fetched"] += 1
        counters["new"] += int(is_new)
        counters["bytes"] += len(content)
        manifest[package_id] = {
            "sha256": digest,
            "size": len(content),
            "url": entry.url,
        }


async def snapshot(resume: bool = True) -> Path:
    """Fetch every COMPS package and write a dated snapshot manifest.

    Args:
        resume: If True and today's manifest already exists, skip packages it
            already records successfully. Makes an interrupted run cheap to restart.

    Returns:
        Path to the written manifest.
    """
    config.ensure_dirs()
    stamp = datetime.now(UTC).date().isoformat()
    manifest_path = config.COMPS_SNAPSHOTS_DIR / f"{stamp}.json"

    manifest: dict[str, dict[str, object]] = {}
    if resume and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = {k: v for k, v in existing.get("packages", {}).items() if "sha256" in v}
        print(f"resuming: {len(manifest)} packages already recorded")

    async with GovInfoClient() as client:
        entries = [e for e in await client.list_bulkdata("COMPS") if not e.is_folder]
        todo = [e for e in entries if e.name.removesuffix(".xml") not in manifest]
        print(f"COMPS: {len(entries)} packages listed, {len(todo)} to fetch")

        counters = {"fetched": 0, "new": 0, "failed": 0, "bytes": 0}
        lock = asyncio.Lock()
        tasks = [_fetch_one(client, e, manifest, lock, counters) for e in todo]

        done = 0
        for chunk_start in range(0, len(tasks), 200):
            await asyncio.gather(*tasks[chunk_start : chunk_start + 200])
            done = min(chunk_start + 200, len(tasks))
            print(
                f"  {done}/{len(todo)}  new={counters['new']}  "
                f"failed={counters['failed']}  {counters['bytes'] / 1e6:.0f} MB",
                flush=True,
            )

    payload = {
        "collection": "COMPS",
        "snapshot_date": stamp,
        "captured_at": datetime.now(UTC).isoformat(),
        "package_count": len(manifest),
        "packages": dict(sorted(manifest.items())),
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")

    failures = sum(1 for v in manifest.values() if "error" in v)
    print(
        f"\nwrote {manifest_path.relative_to(config.REPO_ROOT)}\n"
        f"  packages : {len(manifest)}\n"
        f"  new blobs: {counters['new']}\n"
        f"  failed   : {failures}\n"
        f"  fetched  : {counters['bytes'] / 1e6:.1f} MB"
    )
    return manifest_path
