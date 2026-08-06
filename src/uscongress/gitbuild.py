"""Construct generated git repositories.

Commit dates are set to the upstream publication date so ``git log`` reflects
legal chronology rather than when the pipeline happened to run. The *author* is
always this pipeline: the repository is a derived artifact, and attributing
commits to Congress or to a person would misrepresent what they are.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

AUTHOR_NAME = "us-congress-pipeline"
AUTHOR_EMAIL = "pipeline@junxit.invalid"


@dataclass(frozen=True)
class TreeChange:
    """Result of syncing a working tree.

    Attributes:
        written: Files created or modified.
        removed: Files deleted, e.g. repealed sections.
        total: Files in the resulting tree.
    """

    written: int
    removed: int
    total: int


class GitRepo:
    """A generated git repository.

    Args:
        path: Directory for the repository; created if absent.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _run(self, *args: str, env: dict[str, str] | None = None) -> str:
        """Run a git command inside the repository.

        Args:
            *args: Arguments after ``git``.
            env: Extra environment variables.

        Returns:
            Captured stdout.

        Raises:
            subprocess.CalledProcessError: If git exits non-zero.
        """
        import os

        full_env = {**os.environ, **(env or {})}
        result = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
            env=full_env,
        )
        return result.stdout

    def init(self) -> None:
        """Create the repository if it does not exist."""
        if (self.path / ".git").is_dir():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(self.path), "init", "-b", "main"],
            capture_output=True,
            check=True,
        )
        self._run("config", "user.name", AUTHOR_NAME)
        self._run("config", "user.email", AUTHOR_EMAIL)
        # Generated trees are large and rewritten wholesale each commit.
        self._run("config", "core.fsmonitor", "false")
        self._run("config", "gc.auto", "0")

    def write(self, relative_path: str, content: str) -> None:
        """Write one file, creating parent directories.

        Args:
            relative_path: Path relative to the repository root.
            content: File contents.
        """
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def sync_tree(self, files: dict[str, str], manifest_path: Path) -> TreeChange:
        """Make the working tree match ``files``, touching only what changed.

        A release point is a full snapshot, so the naive implementation rewrites
        every file. That is correct but ruinously slow: the US Code is ~56,000
        sections, and consecutive release points differ by a few hundred at
        most. Rewriting everything made a single commit take minutes, which
        across 386 release points is over a day of pure filesystem churn.

        Instead a manifest of ``path -> content hash`` is kept *outside* the
        repository, so each commit writes only genuinely changed files and
        deletes only genuinely removed ones. Repeals still surface as deletions.

        Args:
            files: Desired tree, mapping repo-relative path to contents.
            manifest_path: Where to persist the hash manifest between runs.

        Returns:
            What changed.
        """
        previous: dict[str, str] = {}
        if manifest_path.is_file():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))

        current = {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in files.items()
        }

        written = 0
        for path, digest in current.items():
            if previous.get(path) != digest:
                self.write(path, files[path])
                written += 1

        removed = 0
        for path in previous:
            if path not in current:
                target = self.path / path
                if target.exists():
                    target.unlink()
                    removed += 1

        self._prune_empty_dirs()

        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
        return TreeChange(written=written, removed=removed, total=len(files))

    def _prune_empty_dirs(self) -> None:
        """Remove directories left empty after deletions, ignoring ``.git``."""
        for directory in sorted(
            (d for d in self.path.rglob("*") if d.is_dir() and ".git" not in d.parts),
            key=lambda d: len(d.parts),
            reverse=True,
        ):
            if not any(directory.iterdir()):
                directory.rmdir()

    def commit(self, message: str, when: date | None = None) -> bool:
        """Stage everything and commit.

        Args:
            message: Full commit message.
            when: Date to use for both author and committer timestamps.

        Returns:
            True if a commit was created, False if the tree was unchanged.
        """
        self._run("add", "-A")
        status = self._run("status", "--porcelain")
        if not status.strip():
            return False
        env = {}
        if when is not None:
            stamp = f"{when.isoformat()}T12:00:00+00:00"
            env = {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        self._run("commit", "-q", "-m", message, env=env)
        return True

    def tag(self, name: str) -> None:
        """Create a tag at HEAD, replacing any existing tag of that name.

        Args:
            name: Tag name.
        """
        self._run("tag", "-f", name)

    def has_tag(self, name: str) -> bool:
        """Report whether a tag already exists.

        Args:
            name: Tag name.

        Returns:
            True if present.
        """
        out = self._run("tag", "--list", name)
        return bool(out.strip())

    def branches(self) -> set[str]:
        """Return every branch name in the repository.

        Read in one call rather than per-branch: a bills repository holds tens
        of thousands of branches, and asking git about each one separately costs
        a process per question.

        Returns:
            Branch names, without the ``refs/heads/`` prefix.
        """
        out = self._run("for-each-ref", "--format=%(refname:short)", "refs/heads")
        return {line.strip() for line in out.splitlines() if line.strip()}

    def read_tree(self, branch: str) -> dict[str, str]:
        """Return every file on a branch, path to contents.

        Needed because :class:`FastImport` sets a commit's whole tree with
        ``deleteall``: adding one file without reading the others first would
        delete them.

        Args:
            branch: Branch name.

        Returns:
            The branch's files, or an empty mapping if it does not exist.
        """
        try:
            listing = self._run("ls-tree", "-r", "--name-only", branch)
        except subprocess.CalledProcessError:
            return {}
        files = {}
        for path in (line.strip() for line in listing.splitlines()):
            if path:
                files[path] = self._run("show", f"{branch}:{path}")
        return files

    def fast_import(self) -> FastImport:
        """Open a stream for writing commits without touching the working tree.

        Returns:
            A context manager; see :class:`FastImport`.
        """
        return FastImport(self)

    def size_bytes(self, repack: bool = False) -> int:
        """Return the repository's on-disk size.

        Args:
            repack: Run a normal ``git gc`` first so loose objects are packed.
                Off by default -- an aggressive repack of a repository this size
                takes many minutes and recomputes every delta, which is far too
                slow to run at the end of an ordinary build.

        Returns:
            Size of the ``.git`` directory in bytes.
        """
        if repack:
            self._run("gc", "--quiet")
        return sum(
            f.stat().st_size for f in (self.path / ".git").rglob("*") if f.is_file()
        )

    def commit_count(self) -> int:
        """Return the number of commits on the current branch."""
        try:
            return int(self._run("rev-list", "--count", "HEAD").strip())
        except subprocess.CalledProcessError:
            return 0


class FastImport:
    """Write commits into a repository via ``git fast-import``.

    :meth:`GitRepo.sync_tree` and :meth:`GitRepo.commit` operate on the working
    tree: they write files to disk, stage them, and prune empty directories.
    That is right for one linear history of large snapshots, which is what the
    US Code repository is. It is unusable for bills.

    A single Congress holds up to 19,315 measures, one branch each. Checking out
    a branch to write one file, committing, and checking out the next would move
    the working tree tens of thousands of times and spawn several processes per
    commit. fast-import instead takes one stream on stdin and never touches the
    working tree at all, so the cost is the bytes written rather than the number
    of refs.

    Commits appended to a branch that the stream has already written chain onto
    it automatically; the first commit to a branch starts it as a root. Bills do
    not descend from a common trunk, so every branch is its own root and there is
    no ``main`` for them to diverge from.

    Args:
        repo: Repository to write into. Must already exist.
    """

    def __init__(self, repo: GitRepo) -> None:
        self._repo = repo
        self._process: subprocess.Popen[bytes] | None = None
        self._existing: set[str] = set()
        self._started: set[str] = set()
        self.commits = 0

    def __enter__(self) -> FastImport:
        """Start the fast-import process.

        Returns:
            This writer.
        """
        self._existing = self._repo.branches()
        self._started = set()
        self._process = subprocess.Popen(
            ["git", "-C", str(self._repo.path), "fast-import", "--quiet", "--done"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Finish the stream and wait for git to write its objects.

        Raises:
            RuntimeError: If fast-import reported a failure.
        """
        assert self._process is not None and self._process.stdin is not None
        self._write(b"done\n")
        self._process.stdin.close()
        stderr = self._process.stderr.read() if self._process.stderr else b""
        code = self._process.wait()
        self._process = None
        if code != 0 and exc_info[0] is None:
            raise RuntimeError(
                f"git fast-import exited {code}: {stderr.decode('utf-8', 'replace')}"
            )

    def _write(self, payload: bytes) -> None:
        """Write raw bytes to the stream.

        Args:
            payload: Bytes to send.
        """
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write(payload)

    @staticmethod
    def _data(payload: bytes) -> bytes:
        """Frame a payload as a fast-import ``data`` block.

        The length is a byte count, not a character count, so text has to be
        encoded before it is measured or any non-ASCII content -- section signs,
        em dashes and typographic quotes, all of which occur in bill text --
        desynchronises the stream.

        Args:
            payload: Raw bytes.

        Returns:
            The framed block.
        """
        return b"data %d\n%s\n" % (len(payload), payload)

    def commit(
        self,
        branch: str,
        files: dict[str, str],
        message: str,
        when: date | None = None,
    ) -> None:
        """Append one commit to a branch, replacing its whole tree.

        Args:
            branch: Branch name, without ``refs/heads/``.
            files: Complete tree for this commit, path to content.
            message: Full commit message.
            when: Date for both author and committer timestamps. Defaults to the
                epoch when absent, so a missing upstream date is obvious rather
                than silently reading as the day the pipeline ran.
        """
        stamp = f"{int(_epoch_seconds(when))} +0000"
        ident = f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>".encode()

        self._write(b"commit refs/heads/%s\n" % branch.encode())
        self._write(b"author %s %s\n" % (ident, stamp.encode()))
        self._write(b"committer %s %s\n" % (ident, stamp.encode()))
        self._write(self._data(message.encode()))
        if branch not in self._started:
            if branch in self._existing:
                # A branch this stream has not written yet, but which the
                # repository already holds, needs its parent stated explicitly.
                # Without this fast-import starts a fresh root and git rejects
                # the ref update as a non-fast-forward -- so a resumed build
                # fails loudly, but only after the work is done.
                self._write(b"from refs/heads/%s^0\n" % branch.encode())
            self._started.add(branch)
        self._write(b"deleteall\n")
        for path, content in sorted(files.items()):
            body = content.encode()
            self._write(b"M 100644 inline %s\n" % path.encode())
            self._write(self._data(body))
        self._write(b"\n")
        self.commits += 1


def _epoch_seconds(when: date | None) -> int:
    """Return a date as seconds since the epoch, at midday UTC.

    Midday matches :meth:`GitRepo.commit`, which stamps ``T12:00:00+00:00`` so a
    date never lands on the wrong calendar day in a nearby timezone.

    Args:
        when: The date, or None.

    Returns:
        Seconds since the epoch.
    """
    if when is None:
        return 0
    return int(datetime(when.year, when.month, when.day, 12, tzinfo=UTC).timestamp())
