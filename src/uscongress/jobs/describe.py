"""Set each repository's description and topics on GitHub.

A repository's README explains it to someone already looking at it. Its
description and topics are what decide whether anyone gets that far: they are
what a search result shows, what the owner's repository list shows, and the only
thing a reader sees before choosing to click. Thirteen repositories called
``us-congress-bills-114`` with no description between them are indistinguishable
from each other and from an abandoned scratch directory.

Topics are GitHub's tag mechanism, and they are constrained: lowercase letters,
digits and hyphens only, at most 50 characters each, at most 20 per repository.
The set here is deliberately small and honest -- terms someone looking for this
data would actually search, rather than everything that could conceivably apply.

Descriptions and topics are derived from :mod:`uscongress.registry`, so a
repository added there gets described without anyone remembering to do it by
hand, and running this again after a new shard is created costs nothing for the
repositories that already match.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from ..registry import OWNER, PIPELINE_REPO, REPOSITORIES

#: GitHub's rule for a topic: lowercase alphanumerics and hyphens, 50 max.
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")

#: The most topics GitHub accepts on one repository.
MAX_TOPICS = 20

#: Topics every repository in the project shares. These are the terms someone
#: hunting for machine-readable US legislative data would actually try.
COMMON_TOPICS = (
    "us-congress",
    "congress",
    "legislation",
    "legal-data",
    "open-data",
    "open-government",
    "govinfo",
    "united-states",
)

PIPELINE_URL = f"https://github.com/{OWNER}/{PIPELINE_REPO}"


@dataclass(frozen=True)
class Metadata:
    """What a repository should advertise about itself.

    Attributes:
        description: One-line summary, shown in search and listings.
        topics: GitHub topics, already validated.
        homepage: URL shown beside the description.
    """

    description: str
    topics: tuple[str, ...]
    homepage: str


def _congress_of(name: str) -> str:
    """Return the Congress number a bills repository holds.

    Args:
        name: Repository name.

    Returns:
        The number, or an empty string.
    """
    tail = name.rsplit("-", 1)[-1]
    return tail if name.startswith("us-congress-bills-") and tail.isdigit() else ""


def metadata_for(name: str) -> Metadata:
    """Build the description and topics for one repository.

    Args:
        name: Repository name.

    Returns:
        What it should advertise.

    Raises:
        ValueError: If any derived topic is not a legal GitHub topic, or there
            are more than GitHub accepts. Both would be rejected silently at the
            API, leaving the repository looking configured when it is not.
    """
    congress = _congress_of(name)

    if name == PIPELINE_REPO:
        # Votes are named as planned, not as present. They were advertised here
        # as a feature before they existed, and a description is the one thing a
        # reader sees before deciding to click, so it is the worst place in the
        # project to overstate what is there. Phase 8 tracks the actual work.
        description = (
            "ETL that mirrors the workings of the US Congress as git repositories: "
            "federal law with its history as commits, every bill as a branch, and "
            "sponsors, cosponsors, committees and actions recorded as of each "
            "version. Updated daily. Roll-call votes planned."
        )
        topics = (*COMMON_TOPICS, "etl", "python", "data-pipeline", "uslm", "us-code")
        homepage = ""
    elif name == "us-congress-code":
        description = (
            "The codified US Code as a git repository: one commit per OLRC release "
            "point, tagged, with per-law attribution from Table III. Public domain "
            "federal text."
        )
        topics = (
            *COMMON_TOPICS,
            "us-code",
            "united-states-code",
            "statutes",
            "olrc",
            "uslm",
            "public-domain",
        )
        homepage = PIPELINE_URL
    elif congress:
        description = (
            f"Bills of the {congress}th Congress as a git repository: one branch per "
            "measure, one commit per text version, with sponsors and actions as of "
            "each version. Public domain federal text."
        )
        topics = (
            *COMMON_TOPICS,
            "bills",
            "legislative-data",
            f"congress-{congress}",
            "public-domain",
        )
        homepage = PIPELINE_URL
    else:
        entry = next((r for r in REPOSITORIES if r.name == name), None)
        description = entry.summary if entry else name
        topics = COMMON_TOPICS
        homepage = PIPELINE_URL

    bad = [t for t in topics if not _TOPIC.match(t)]
    if bad:
        raise ValueError(f"{name}: not valid GitHub topics: {', '.join(bad)}")
    if len(topics) > MAX_TOPICS:
        raise ValueError(f"{name}: {len(topics)} topics, GitHub accepts {MAX_TOPICS}")

    return Metadata(description=description, topics=tuple(topics), homepage=homepage)


def _gh(*args: str, payload: str | None = None) -> tuple[int, str]:
    """Run a ``gh`` command.

    Args:
        *args: Arguments after ``gh``.
        payload: Optional JSON body to send on stdin.

    Returns:
        An ``(exit code, stdout)`` pair.
    """
    result = subprocess.run(
        ["gh", *args],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout


def current(name: str) -> Metadata | None:
    """Read what a repository currently advertises.

    Args:
        name: Repository name.

    Returns:
        Its present metadata, or None if it does not exist.
    """
    code, out = _gh(
        "api",
        f"repos/{OWNER}/{name}",
        "--jq",
        "{description:(.description//\"\"),topics:.topics,homepage:(.homepage//\"\")}",
    )
    if code != 0:
        return None
    payload = json.loads(out)
    return Metadata(
        description=payload["description"],
        topics=tuple(sorted(payload["topics"])),
        homepage=payload["homepage"],
    )


def apply_to(name: str) -> bool:
    """Set one repository's description, homepage and topics.

    Args:
        name: Repository name.

    Returns:
        True if anything changed.

    Raises:
        RuntimeError: If GitHub rejects the update.
    """
    wanted = metadata_for(name)
    have = current(name)
    if have is None:
        return False
    if (
        have.description == wanted.description
        and have.homepage == wanted.homepage
        and have.topics == tuple(sorted(wanted.topics))
    ):
        return False

    code, _ = _gh(
        "api",
        "-X",
        "PATCH",
        f"repos/{OWNER}/{name}",
        "-f",
        f"description={wanted.description}",
        "-f",
        f"homepage={wanted.homepage}",
    )
    if code != 0:
        raise RuntimeError(f"{name}: could not set description")

    # Topics have their own endpoint and replace wholesale, so the full set is
    # sent every time rather than diffed.
    code, _ = _gh(
        "api",
        "-X",
        "PUT",
        f"repos/{OWNER}/{name}/topics",
        "--input",
        "-",
        payload=json.dumps({"names": list(wanted.topics)}),
    )
    if code != 0:
        raise RuntimeError(f"{name}: could not set topics")
    return True


def repositories() -> list[str]:
    """Return every repository name this project owns, shards expanded.

    Returns:
        Names, in registry order with shards in numeric order.
    """
    from .. import config

    names: list[str] = []
    for repo in sorted(REPOSITORIES, key=lambda r: r.phase):
        if "{" not in repo.name:
            names.append(repo.name)
            continue
        family = repo.name.replace("{congress}", "")
        shards = [
            p.name
            for p in config.REPOS_DIR.glob(repo.name.replace("{congress}", "*"))
            if (p / ".git").is_dir() and not p.name.endswith(".pre-fix")
        ]
        names += sorted(
            shards,
            key=lambda n: int(n[len(family) :]) if n[len(family) :].isdigit() else 0,
        )
    return names


def stale() -> list[str]:
    """Return repositories whose GitHub metadata does not match the registry.

    Exists so drift is detectable rather than depending on someone remembering
    to run the setter. A repository created after this was last run shows up
    here instead of sitting undescribed indefinitely.

    Returns:
        Names needing an update; repositories not yet on GitHub are not counted.
    """
    behind = []
    for name in repositories():
        have = current(name)
        if have is None:
            continue
        want = metadata_for(name)
        if (
            have.description != want.description
            or have.homepage != want.homepage
            or have.topics != tuple(sorted(want.topics))
        ):
            behind.append(name)
    return behind


def check() -> int:
    """Report repositories whose description or topics are out of date.

    Returns:
        How many are out of date.
    """
    behind = stale()
    for name in behind:
        print(f"  {name}: description or topics out of date", flush=True)
    if behind:
        print(f"\n{len(behind)} repositories need `uscongress describe`", flush=True)
    else:
        print("every repository is described", flush=True)
    return len(behind)


def apply_all() -> list[str]:
    """Describe every repository that exists on GitHub.

    Returns:
        Names of repositories that changed.
    """
    changed = []
    for name in repositories():
        if current(name) is None:
            print(f"  {name}: not on GitHub yet, skipped", flush=True)
            continue
        if apply_to(name):
            changed.append(name)
            print(f"  {name}: described, {len(metadata_for(name).topics)} topics", flush=True)
        else:
            print(f"  {name}: unchanged", flush=True)
    return changed
