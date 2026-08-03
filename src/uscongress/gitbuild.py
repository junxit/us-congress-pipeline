"""Construct generated git repositories.

Commit dates are set to the upstream publication date so ``git log`` reflects
legal chronology rather than when the pipeline happened to run. The *author* is
always this pipeline: the repository is a derived artifact, and attributing
commits to Congress or to a person would misrepresent what they are.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

AUTHOR_NAME = "us-congress-pipeline"
AUTHOR_EMAIL = "pipeline@junxit.invalid"


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

    def replace_tree(self, subdirs: list[str]) -> None:
        """Delete the given top-level subdirectories before rewriting them.

        A release point is a full snapshot, so stale files -- sections that were
        repealed -- must disappear rather than linger. Removing the directories
        and letting ``git add -A`` observe the deletions is the honest way to
        represent a repeal.

        Args:
            subdirs: Top-level directory names to clear.
        """
        for name in subdirs:
            target = self.path / name
            if target.exists():
                shutil.rmtree(target)

    def write(self, relative_path: str, content: str) -> None:
        """Write one file, creating parent directories.

        Args:
            relative_path: Path relative to the repository root.
            content: File contents.
        """
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

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

    def size_bytes(self) -> int:
        """Return the repository's on-disk size after packing.

        Returns:
            Size of the ``.git`` directory in bytes.
        """
        self._run("gc", "--quiet", "--aggressive")
        return sum(f.stat().st_size for f in (self.path / ".git").rglob("*") if f.is_file())

    def commit_count(self) -> int:
        """Return the number of commits on the current branch."""
        try:
            return int(self._run("rev-list", "--count", "HEAD").strip())
        except subprocess.CalledProcessError:
            return 0
