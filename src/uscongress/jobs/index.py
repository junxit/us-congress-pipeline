"""Regenerate ``REPOSITORIES.md``, the index of every repository in the project.

The index is generated rather than hand-maintained so it cannot drift: status is
read live from GitHub, and a repository that has not been created yet is shown
as *not created* instead of being quietly implied to exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from .. import config
from ..registry import OWNER, PHASES, PREFIX, REPOSITORIES, Repository, RepoStatus
from ..registry import fetch_status as _fetch_status

INDEX_PATH = config.REPO_ROOT / "REPOSITORIES.md"


@lru_cache(maxsize=None)
def fetch_status(name: str) -> RepoStatus:
    """Look a repository up on GitHub, once per run.

    Rendering asks about each repository several times -- to decide whether to
    link it, to describe its state, and again for every shard of a family. Each
    call shells out to ``gh``, so without memoising, generating this file makes
    dozens of round trips to answer the same handful of questions.

    Args:
        name: Repository name without the owner.

    Returns:
        The live status.
    """
    return _fetch_status(name)


def _status_cell(name: str) -> str:
    """Render one repository's live status as a table cell.

    Args:
        name: Repository name, possibly a ``{congress}`` shard template.

    Returns:
        Markdown for the status column.
    """
    if "{" in name:
        # A shard family has no single repository to query, so each member is
        # asked about separately. Reporting the family as merely "planned" once
        # the shards exist understates what is there.
        built = sorted(
            p
            for p in config.REPOS_DIR.glob(name.replace("{congress}", "*"))
            if (p / ".git").is_dir() and not p.name.endswith(".pre-fix")
        )
        if not built:
            return "planned (sharded)"
        live = [p for p in built if fetch_status(p.name).exists]
        if not live:
            return f"{len(built)} shards built locally, not pushed"

        # Three cases, not two: all private, all public, or a mix mid-flip.
        # Collapsing the last two into one reported a fully published family as
        # "mixed", which is the state that most needs to be visible when it is
        # real and most misleading when it is not.
        private = sum(1 for p in live if fetch_status(p.name).private)
        if private == len(live):
            visibility = "private"
        elif private == 0:
            visibility = "**public**"
        else:
            visibility = f"**mixed** ({private} still private)"

        if len(live) == len(built):
            return f"{len(live)} shards live, {visibility}"
        return f"{len(live)} of {len(built)} shards live, {visibility}"
    status = fetch_status(name)
    if status.error:
        return f"unknown — {status.error}"
    if not status.exists:
        return "not created yet"
    visibility = "private" if status.private else "**public**"
    when = status.pushed_at[:10] if status.pushed_at else "?"
    return f"live, {visibility}, pushed {when}"


def _shards_of(repo: Repository) -> list[str]:
    """Return the built shard names of a repository family, in order.

    Args:
        repo: A sharded repository entry.

    Returns:
        Directory names, sorted by their trailing number rather than as text so
        the 109th does not sort after the 110th.
    """
    if "{" not in repo.name:
        return []
    return config.built_shards(repo.name)


def _roadmap() -> list[str]:
    """Render the plan's phases.

    The repository table below cannot carry the roadmap on its own: three of the
    eight phases produce no repository, so a table keyed on repositories showed
    the numbering jumping from 2 to 5 with nothing to explain the gap -- and
    omitted the phase whose absence mattered most, the daily loop the plan
    deliberately ordered before the corpus expanded.

    Returns:
        Markdown lines.
    """
    done = sum(1 for phase in PHASES if phase.is_done)
    quiet = [str(p.number) for p in sorted(PHASES, key=lambda p: p.number) if not p.produces]
    gaps = ", ".join(quiet[:-1]) + f" and {quiet[-1]}" if len(quiet) > 1 else quiet[0]
    lines = [
        "## Roadmap",
        "",
        f"{done} of {len(PHASES)} phases shipped. Phases {gaps} produce no repository",
        "of their own — they add to repositories built earlier — which is why the",
        "repository table below skips those numbers.",
        "",
        "| Phase | Work | State | Produces |",
        "|---|---|---|---|",
    ]
    for phase in sorted(PHASES, key=lambda p: p.number):
        produces = f"`{phase.produces}`" if phase.produces else "—"
        state = "**shipped**" if phase.is_done else "planned"
        lines.append(
            f"| {phase.number} | **{phase.title}**<br>{phase.detail} | {state} | "
            f"{produces} |"
        )
    lines += [
        "",
        "Phases 5 and 6 are independent of everything above and of each other, so",
        "they can be reordered or run in parallel now that the daily loop is",
        "standing. See [`STATUS.md`](STATUS.md) for whether it last ran.",
        "",
    ]
    return lines


def render() -> str:
    """Build the Markdown index.

    Returns:
        The full document.
    """
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Repository index",
        "",
        "Every repository in this project, what it holds, and whether it exists yet,",
        "and the plan the whole thing is being built to.",
        "",
        f"**Generated** {stamp} by `uv run uscongress index`. Do not edit by hand —",
        "the source of truth is `src/uscongress/registry.py`.",
        "",
        *_roadmap(),
        "## The repositories",
        "",
        f"All names are prefixed `{PREFIX}`. Every repository here is public; the",
        "federal text they carry is public domain under 17 U.S.C. § 105, and each",
        "one states its own terms in a `LICENSE` file.",
        "",
        "| Repository | Phase | Contents | Status |",
        "|---|---|---|---|",
    ]

    for repo in sorted(REPOSITORIES, key=lambda r: (r.phase, r.name)):
        # Only link a repository that exists. A link to a repository that has
        # not been created yet is a 404 -- harmless while this is private, and
        # the first thing a reader hits once it is not.
        if "{" in repo.name:
            label = f"`{repo.name}`"
        elif fetch_status(repo.name).exists:
            label = f"[`{repo.name}`]({repo.url})"
        else:
            label = f"`{repo.name}`"
        if repo.is_pipeline:
            label += " ← you are here"
        lines.append(
            f"| {label} | {repo.phase} | {repo.summary} | {_status_cell(repo.name)} |"
        )

    families = [(r, _shards_of(r)) for r in REPOSITORIES if "{" in r.name]
    families = [(r, s) for r, s in families if s]
    if families:
        lines += [
            "",
            "### The sharded repositories",
            "",
            "A family row above names a template, not a repository. These are the",
            "repositories it actually stands for.",
            "",
        ]
        for repo, shards in families:
            links = ", ".join(
                f"[`{name.rsplit('-', 1)[-1]}`](https://github.com/{OWNER}/{name})"
                if fetch_status(name).exists
                else f"`{name.rsplit('-', 1)[-1]}`"
                for name in shards
            )
            lines += [f"**`{repo.name}`** — {links}", ""]

    lines += ["", "## Sources", "", "| Repository | Built from |", "|---|---|"]
    for repo in sorted(REPOSITORIES, key=lambda r: (r.phase, r.name)):
        if not repo.is_pipeline:
            lines.append(f"| `{repo.name}` | {repo.source} |")

    sharded = [r for r in REPOSITORIES if r.shards]
    if sharded:
        lines += ["", "## Sharding", ""]
        lines += [f"- `{r.name}` — {r.shards}" for r in sharded]
        lines += [
            "",
            "Sharding by Congress is a deliberate choice, not a capacity limit. The",
            "twelve Congresses from the 108th hold 171,881 measures in 6.60 GB of XML,",
            "which packs to roughly 1-2 GB; one repository could carry all of it, and",
            "GitHub publishes no branch-count ceiling.",
            "",
            "It is sharded because a finished Congress never changes again, so frozen",
            "shards never rebuild and their clones stay valid; because a defect in",
            "recent data should not force a rebuild of 2003; because reading the 118th",
            "should not mean downloading 6.6 GB; and because twelve repositories build",
            "in parallel where one serializes. Branch counts run 10,637 to 19,315 per",
            "Congress.",
        ]

    lines += [
        "",
        "## What the diffs mean",
        "",
        "Diffing across a bill branch's own commits -- `hr-1234~2..hr-1234`, or",
        "against the commit that introduced it -- shows how the **bill** changed,",
        "not how the **US Code** would change. Bills are written as amendatory",
        "instructions, not diffs, and executing them automatically is unsolved.",
        "Synthesised effects on existing law are always marked derived.",
        "",
    ]
    return "\n".join(lines)


def write(path: Path | None = None) -> Path:
    """Render the index and write it to disk.

    Args:
        path: Destination; defaults to ``REPOSITORIES.md`` at the repo root.

    Returns:
        The path written.
    """
    target = path or INDEX_PATH
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target.name}")
    return target
