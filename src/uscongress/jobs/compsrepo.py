"""Publish the Statute Compilations snapshots as a git repository.

The snapshots existed only under ``data/comps/``, which is gitignored, so the
one genuinely irrecoverable thing this project holds lived on a single disk with
no copy anywhere. govinfo replaces these packages in place and keeps no version
archive: once a compilation is superseded the previous text is gone from the
internet, and a lost snapshot is lost history rather than a rebuild.

Publishing it fixes three things at once. The snapshots get an off-machine copy;
CI gains something to check freshness against, so the daily job can finally be
scheduled rather than remembered; and the collection becomes diffable.

**Files are named for the compilation they hold, not for its hash.** The local
store is content-addressed because it has to deduplicate 633 MB across
snapshots; git already does that on its own, so naming files by SHA-256 here
would buy nothing and cost everything -- every commit would be an unreadable
churn of hex, and the question these snapshots exist to answer is *what changed
in this compilation*. One commit per snapshot day, named paths, so a diff reads.

The working-tree path is used rather than fast-import, deliberately.
``fast-import``'s ``deleteall`` sets a commit's whole tree, so extending the
history would mean holding all 633 MB in memory per commit. Writing the tree and
letting git diff it keeps memory flat, and git is also the only record of the
previous snapshot that survives a scheduled runner -- see :func:`_materialise`,
which is where assuming otherwise went wrong.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import config
from ..gitbuild import GitRepo

REPO_NAME = "us-congress-comps"

#: The branch the snapshots land on. ``main`` carries the README, the license
#: and GAPS.md, exactly as it does in every other generated repository.
SNAPSHOTS = "snapshots"


def _blob(digest: str) -> Path:
    """Return the local store path for a blob.

    Args:
        digest: Hex SHA-256.

    Returns:
        Path under the objects directory.
    """
    return config.COMPS_OBJECTS_DIR / digest[:2] / digest


def manifests() -> list[tuple[date, Path]]:
    """Return every snapshot manifest, oldest first.

    Returns:
        ``(snapshot date, path)`` pairs. Ordered because the repository is a
        history: committing them out of order would date commits backwards.
    """
    found = []
    for path in config.COMPS_SNAPSHOTS_DIR.glob("*.json"):
        try:
            found.append((date.fromisoformat(path.stem), path))
        except ValueError:
            continue
    return sorted(found)


def _files_for(path: Path) -> tuple[dict[str, str], int]:
    """Read one snapshot's packages out of the local blob store.

    Args:
        path: Manifest to read.

    Returns:
        A ``{filename: content}`` mapping and the number of packages the
        manifest recorded as failing, which are not written but are counted.

    Raises:
        FileNotFoundError: If a blob the manifest names is not in the store.
            The manifest is the index and the store is the data; an index
            pointing at nothing is a damaged snapshot, not an empty one.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    files: dict[str, str] = {}
    failed = 0
    for package_id, entry in sorted((payload.get("packages") or {}).items()):
        digest = entry.get("sha256")
        if not digest:
            failed += 1
            continue
        blob = _blob(str(digest))
        if not blob.is_file():
            raise FileNotFoundError(f"{path.name}: blob missing for {package_id}")
        files[f"{package_id}.xml"] = blob.read_text(encoding="utf-8", errors="replace")
    return files, failed


def built_snapshots(repo: GitRepo) -> set[date]:
    """Return the snapshot days the repository already holds.

    Read from the commit subjects rather than the tree: every commit carries the
    whole collection, so there is no per-day path to look for.

    Args:
        repo: The repository.

    Returns:
        Every snapshot day already committed.
    """
    if SNAPSHOTS not in repo.branches():
        return set()
    days = set()
    for line in repo._run("log", "--format=%s", SNAPSHOTS).splitlines():  # noqa: SLF001
        _, _, tail = line.rpartition(" ")
        try:
            days.add(date.fromisoformat(tail.strip()))
        except ValueError:
            continue
    return days


def seed(repo_path: Path | None = None) -> GitRepo:
    """Build the snapshots repository from the local store.

    Resumable and idempotent in the same way every other job here is: a snapshot
    day already committed is skipped before anything is read, so re-running a
    finished build touches nothing.

    Args:
        repo_path: Override the repository location.

    Returns:
        The repository.
    """
    repo = GitRepo(repo_path or config.REPOS_DIR / REPO_NAME)
    repo.init()
    existing = built_snapshots(repo)

    pending = [(day, path) for day, path in manifests() if day not in existing]
    print(
        f"COMPS: {len(existing)} snapshot(s) already present, {len(pending)} to build",
        flush=True,
    )
    if not pending:
        return repo

    if SNAPSHOTS in repo.branches():
        repo._run("checkout", "--quiet", SNAPSHOTS)  # noqa: SLF001
    else:
        repo._run("checkout", "--quiet", "-B", SNAPSHOTS)  # noqa: SLF001

    for day, path in pending:
        files, failed = _files_for(path)
        # Recorded in the tree so that a day on which nothing changed still
        # produces a commit. Without it the tree is unchanged, `commit` finds
        # nothing staged and returns False, and the day leaves no trace --
        # making "checked, and the collection was identical" indistinguishable
        # from "never checked". For a repository whose entire purpose is to be
        # the surviving record of what existed on a given day, that is the one
        # ambiguity it cannot afford. Counts describe the collection, not this
        # repository, so they do not churn against their own commit.
        files["snapshot.json"] = (
            json.dumps(
                {
                    "collection": "COMPS",
                    "snapshot_date": day.isoformat(),
                    "package_count": len(files),
                    "packages_unavailable": failed,
                },
                indent=2,
            )
            + "\n"
        )
        compilations = len(files) - 1  # snapshot.json is not a compilation
        changed, withdrawn = _materialise(repo, files)
        note = f", {failed} package(s) govinfo would not serve" if failed else ""
        repo.commit(
            f"Statute Compilations — {day.isoformat()}\n\n"
            f"{compilations:,} compilations, {changed:,} changed and "
            f"{withdrawn:,} withdrawn since the previous snapshot{note}.\n\n"
            "govinfo replaces these packages in place and keeps no archive, so "
            "this commit is the only remaining record of the collection as it "
            "stood on this day.",
            when=day,
        )
        print(
            f"  {day}: {compilations:,} compilations, "
            f"{changed:,} changed, {withdrawn:,} withdrawn",
            flush=True,
        )
    return repo


def _materialise(repo: GitRepo, files: dict[str, str]) -> tuple[int, int]:
    """Make the working tree exactly match ``files`` and report what moved.

    Counted by asking git rather than by counting writes. :meth:`GitRepo.sync_tree`
    counts writes against a manifest kept *outside* the repository, which is
    right for the US Code -- built on one machine that keeps its ``data/`` -- and
    wrong here: this job runs on a scheduled runner that starts with nothing, so
    the manifest was absent every single time. Two consequences, both live for
    nineteen days before anyone looked.

    Every commit message claimed all 2,682 compilations had changed, when the
    real diff was one file. The message is the interface -- ``git log`` answering
    *what changed in this compilation, and when* is the entire reason these files
    are named for compilations rather than hashes -- so a message that says
    everything changed every day destroys the thing the layout was chosen for.

    And nothing was ever deleted. The removal pass iterates the manifest, so an
    empty manifest withdraws nothing: a compilation govinfo dropped would have
    stayed in the tree for ever while the message reported "0 withdrawn". That
    had not happened yet, which is luck rather than design.

    The repository is the state that survives, so it is the state to compare
    against, and git already computes this exactly.

    Args:
        repo: The repository, checked out on the snapshots branch.
        files: The full tree this snapshot should have.

    Returns:
        ``(changed, withdrawn)``, excluding ``snapshot.json``, which moves every
        day by construction and is not a compilation.
    """
    for name, content in files.items():
        target = repo.path / name
        # Compared before writing: identical content is the overwhelmingly
        # common case, and rewriting 633 MB to change nothing costs the runner
        # real time.
        if target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        repo.write(name, content)

    keep = set(files)
    for existing in repo.path.iterdir():
        if existing.name != ".git" and existing.name not in keep:
            existing.unlink()

    repo._run("add", "-A")  # noqa: SLF001
    changed = withdrawn = 0
    for line in repo._run("status", "--porcelain").splitlines():  # noqa: SLF001
        # Porcelain v1 is fixed width: two status characters, a space, the path.
        # Splitting on the first space instead reads " M path" as an empty
        # status, which counts a real change as nothing.
        state, name = line[:2], line[3:].strip()
        if not name or name == "snapshot.json":
            continue
        if "D" in state:
            withdrawn += 1
        else:
            changed += 1
    return changed, withdrawn
