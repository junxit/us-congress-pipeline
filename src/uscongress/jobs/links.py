"""Check that every link in every generated document actually resolves.

The Markdown in this project is generated, in bulk, into repositories that are
published without their pipeline. That makes a single wrong branch in a template
multiply: linking a repository that has not been created yet put the same 404
into thirteen repositories at once, and nothing noticed until someone read one.

So the links are checked rather than assumed:

* a ``github.com/<owner>/…`` link must name a repository that exists
* a relative link must name a file that exists on the branch it is written to
* an anchor must name a heading in its own document

External links are listed but not fetched. Reaching out to third-party hosts
turns a local check into a flaky one, and the failure it would catch -- a
government site reorganizing -- is not a failure this project can fix by
editing a template.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..registry import OWNER

#: ``[label](target)``, with the target stopping at the first closing bracket.
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

#: A Markdown ATX heading, used to resolve in-document anchors.
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)


@dataclass(frozen=True)
class BrokenLink:
    """One link that does not resolve.

    Attributes:
        repo: Repository the document lives in.
        document: Path of the document within that repository.
        label: The link's visible text.
        target: The link's target.
        reason: Why it does not resolve.
    """

    repo: str
    document: str
    label: str
    target: str
    reason: str

    def __str__(self) -> str:
        """Render as one line for a report."""
        return f"{self.repo}/{self.document}: [{self.label}]({self.target}) — {self.reason}"


def _anchor(heading: str) -> str:
    """Convert a heading to the anchor GitHub generates for it.

    Args:
        heading: Heading text, without the leading hashes.

    Returns:
        The anchor, without the leading ``#``.
    """
    text = re.sub(r"[`*_]", "", heading).strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def check_document(
    body: str,
    *,
    repo: str,
    document: str,
    files: set[str],
    repos: set[str],
) -> list[BrokenLink]:
    """Check every link in one document.

    Args:
        body: The document's text.
        repo: Repository the document lives in.
        document: Path of the document within that repository.
        files: Every file present alongside it, for relative links.
        repos: Every repository known to exist, for owner links.

    Returns:
        The links that do not resolve.
    """
    anchors = {_anchor(h) for h in _HEADING.findall(body)}
    broken: list[BrokenLink] = []

    for label, raw in _LINK.findall(body):
        target = raw.strip()
        if target.startswith("#"):
            if target[1:] not in anchors:
                broken.append(
                    BrokenLink(repo, document, label, target, "no such heading")
                )
        elif target.startswith(f"https://github.com/{OWNER}/"):
            rest = target[len(f"https://github.com/{OWNER}/") :].strip("/")
            # Only the first segment names the repository. A deep link such as
            # .../us-congress-pipeline/blob/main/STATUS.md is a link into a
            # repository, not a link to one called "us-congress-pipeline/blob/
            # main/STATUS.md", and reading it as the latter reports every deep
            # link in the project as broken.
            name = rest.split("/", 1)[0]
            if "{" in name:
                broken.append(
                    BrokenLink(repo, document, label, target, "links a name template")
                )
            elif name not in repos:
                broken.append(
                    BrokenLink(repo, document, label, target, "repository does not exist")
                )
        elif target.startswith(("http://", "https://", "mailto:")):
            continue
        else:
            path = target.split("#", 1)[0]
            if path and path not in files:
                broken.append(
                    BrokenLink(repo, document, label, target, "no such file in this repo")
                )

    return broken


def _git(path: Path, *args: str) -> str:
    """Run a read-only git command, returning empty output on failure.

    Args:
        path: Repository directory.
        *args: Arguments after ``git``.

    Returns:
        Captured stdout, or an empty string.
    """
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )
    return result.stdout if result.returncode == 0 else ""


def _known_repos() -> set[str]:
    """Return every repository that exists, on disk or on GitHub.

    A repository built locally but not yet pushed still counts: the link will
    resolve by the time anyone can read the document, because both are published
    together.

    Returns:
        Repository names.
    """
    local = {
        p.name
        for p in config.REPOS_DIR.glob("us-congress-*")
        if (p / ".git").is_dir() and not p.name.endswith(".pre-fix")
    }
    return local | {config.REPO_ROOT.name}


def check_all() -> list[BrokenLink]:
    """Check every Markdown document this project generates or maintains.

    Returns:
        Every link that does not resolve, across every repository.
    """
    repos = _known_repos()
    broken: list[BrokenLink] = []

    root = config.REPO_ROOT
    # Tracked files only. Walking the tree would descend into data/, which holds
    # tens of gigabytes of raw downloads and every generated repository.
    pipeline_files = set(_git(root, "ls-files").split())
    for name in sorted(pipeline_files):
        if name.endswith(".md"):
            broken += check_document(
                (root / name).read_text(encoding="utf-8"),
                repo=root.name,
                document=name,
                files=pipeline_files,
                repos=repos,
            )

    for path in sorted(config.REPOS_DIR.glob("us-congress-*")):
        if not (path / ".git").is_dir() or path.name.endswith(".pre-fix"):
            continue
        listing = set(_git(path, "ls-tree", "-r", "--name-only", "main").split())
        # Only root-level Markdown is documentation. Everything nested is
        # content -- us-congress-code's main carries 60,493 section files, all
        # of them .md and none of them containing a link -- so reading each one
        # would mean sixty thousand subprocesses to learn nothing.
        for name in sorted(n for n in listing if n.endswith(".md") and "/" not in n):
            broken += check_document(
                _git(path, "show", f"main:{name}"),
                repo=path.name,
                document=name,
                files=listing,
                repos=repos,
            )

    return broken


def report() -> int:
    """Check everything and print a report.

    Returns:
        The number of broken links found.
    """
    broken = check_all()
    for link in broken:
        print(f"  {link}", flush=True)
    if broken:
        print(f"\n{len(broken)} broken links", flush=True)
    else:
        print("all links resolve", flush=True)
    return len(broken)
