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
from datetime import date
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
