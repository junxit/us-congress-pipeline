"""Regenerate ``REPOSITORIES.md``, the index of every repository in the project.

The index is generated rather than hand-maintained so it cannot drift: status is
read live from GitHub, and a repository that has not been created yet is shown
as *not created* instead of being quietly implied to exist.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..registry import PREFIX, REPOSITORIES, fetch_status

INDEX_PATH = config.REPO_ROOT / "REPOSITORIES.md"


def _status_cell(name: str) -> str:
    """Render one repository's live status as a table cell.

    Args:
        name: Repository name, possibly a ``{congress}`` shard template.

    Returns:
        Markdown for the status column.
    """
    if "{" in name:
        return "planned (sharded)"
    status = fetch_status(name)
    if status.error:
        return f"unknown — {status.error}"
    if not status.exists:
        return "not created yet"
    visibility = "private" if status.private else "**public**"
    when = status.pushed_at[:10] if status.pushed_at else "?"
    return f"live, {visibility}, pushed {when}"


def render() -> str:
    """Build the Markdown index.

    Returns:
        The full document.
    """
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Repository index",
        "",
        "Every repository in this project, what it holds, and whether it exists yet.",
        "",
        f"**Generated** {stamp} by `uv run uscongress index`. Do not edit by hand —",
        "the source of truth is `src/uscongress/registry.py`.",
        "",
        f"All names are prefixed `{PREFIX}`. All repositories are private for now;",
        "publishing is a one-way door and the decision is deliberately deferred.",
        "",
        "| Repository | Phase | Contents | Status |",
        "|---|---|---|---|",
    ]

    for repo in sorted(REPOSITORIES, key=lambda r: (r.phase, r.name)):
        label = f"[`{repo.name}`]({repo.url})" if "{" not in repo.name else f"`{repo.name}`"
        if repo.is_pipeline:
            label += " ← you are here"
        lines.append(
            f"| {label} | {repo.phase} | {repo.summary} | {_status_cell(repo.name)} |"
        )

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
            "Sharding by Congress is forced by arithmetic: roughly 180,000 measures",
            "since 2003, against GitHub's recommended ceiling of 5,000 branches per",
            "repository.",
        ]

    lines += [
        "",
        "## What the diffs mean",
        "",
        "Diffing a bill branch against its base tag shows how the **bill** changed,",
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
