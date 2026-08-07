"""Write the artifacts that let each generated repository stand on its own.

A generated repository is data, not a project: nobody edits it by hand and it can
be rebuilt from source at any time. That makes it easy to publish something that
arrives with no explanation of what it is, where it came from, or what its
contents may and may not be used for. Someone who finds
``us-congress-bills-113`` and sees 10,624 branches called ``hr-588`` needs to be
told, in the repository itself, what a branch means and why the diffs are what
they are.

So every generated repository carries:

* ``README.md`` -- what it holds, how to use it without reading anything else,
  what the caveats are, and a link back to the pipeline that built it.
* ``LICENSE`` -- see below.
* ``GAPS.md`` -- what is missing and why, written by the build that found it.

**Licensing is deliberately split.** The pipeline is proprietary. The federal
text it publishes is not, and cannot be: 17 U.S.C. § 105 puts work of the United
States Government in the public domain, so asserting copyright over the statutes
themselves would be a claim with nothing behind it. The data repositories
therefore disclaim rights over the text and reserve them only over the layer
this project actually authored -- the rendering, the structure, and the prose in
files like this one.
"""

from __future__ import annotations

from pathlib import Path

from .. import config
from ..gitbuild import GitRepo
from ..registry import OWNER, PIPELINE_REPO, REPOSITORIES, Repository, phase_of
from .links import check_document

PIPELINE_URL = f"https://github.com/{OWNER}/{PIPELINE_REPO}"

LICENSE_PIPELINE = """\
Copyright (c) 2026 Jade Naaman. All rights reserved.

This software is proprietary. No licence is granted to use, copy, modify,
distribute or create derivative works from it, in whole or in part, by any
means, without the prior written permission of the copyright holder.

The repositories this software generates are licensed separately; see the
LICENSE file in each of them.
"""

LICENSE_DATA = """\
This repository contains two different things, under two different terms.

1. THE FEDERAL TEXT -- PUBLIC DOMAIN

   The text of United States federal law reproduced here is a work of the
   United States Government. Under 17 U.S.C. § 105 it is not subject to
   copyright protection in the United States, and no rights are asserted over
   it by anyone. Copy it, redistribute it, build on it freely.

2. EVERYTHING ELSE -- Copyright (c) 2026 Jade Naaman

   The material this project authored around that text is not federal work and
   is not in the public domain:

     - the conversion of the official XML into Markdown, and the structure,
       naming and layout that conversion produces
     - the arrangement of the history: what constitutes a commit, a branch or
       a tag, and the ordering between them
     - the commit messages, and the explanatory prose in files such as
       README.md and GAPS.md

   All rights are reserved over that layer. No licence is granted to use, copy,
   modify or distribute it without prior written permission.

Nothing here is legal advice, and this repository is not an official source of
United States law. It is a derived copy. For authoritative text, consult the
Office of the Law Revision Counsel (uscode.house.gov) or GPO (govinfo.gov).
"""


def _status_of(repo: Repository, built: set[str]) -> str:
    """Describe a repository's state for the cross-reference table.

    What is on disk decides whether a repository is built; the roadmap in
    :data:`uscongress.registry.PHASES` decides whether the phase that creates it
    has shipped. Reading the second from the first would report a phase as
    planned on any machine that had not built it yet, and every generated
    repository carries this table, so a wrong answer here is published into all
    fourteen of them at once.

    Args:
        repo: The repository being described.
        built: Names of repositories that exist on disk.

    Returns:
        A short phrase.
    """
    if repo.is_pipeline:
        return "the pipeline"
    phase = phase_of(repo.phase)
    if "{" in repo.name:
        family = repo.name.replace("{congress}", "")
        count = sum(1 for name in built if name.startswith(family))
        if count:
            return f"{count} of these built"
        return "built" if phase and phase.is_done else "planned"
    if repo.name in built:
        return "built"
    return "built" if phase and phase.is_done else "planned"


def _related_table(current: str, built: set[str]) -> list[str]:
    """Render the cross-reference table shared by every repository.

    Args:
        current: Name of the repository the table is being written into.
        built: Names of repositories that exist on disk.

    Returns:
        Markdown lines.
    """
    lines = [
        "| Repository | Phase | Holds | State |",
        "|---|---|---|---|",
    ]
    for repo in sorted(REPOSITORIES, key=lambda r: r.phase):
        family = repo.name.replace("{congress}", "") if "{" in repo.name else ""
        in_family = bool(family) and current.startswith(family)
        here = " ← you are here" if repo.name == current or in_family else ""
        # Only link a repository that exists. Linking one that is still planned
        # publishes a 404 into every repository in the set at once.
        name = (
            f"[`{repo.name}`]({repo.url})"
            if "{" not in repo.name and repo.name in built
            else f"`{repo.name}`"
        )
        lines.append(
            f"| {name}{here} | {repo.phase} | {repo.summary} | {_status_of(repo, built)} |"
        )

    # A shard family is useless as a cross-reference unless its members are
    # named: someone reading us-congress-bills-113 should be able to reach the
    # 114th without guessing the URL.
    for repo in REPOSITORIES:
        if "{" not in repo.name:
            continue
        family = repo.name.replace("{congress}", "")
        members = sorted(name for name in built if name.startswith(family))
        if not members:
            continue
        links = ", ".join(
            f"**`{name.rsplit('-', 1)[-1]}`**"
            if name == current
            else f"[`{name.rsplit('-', 1)[-1]}`](https://github.com/{OWNER}/{name})"
            for name in members
        )
        lines += ["", f"`{repo.name}` — {links}"]
    return lines


def _facts(path: Path, name: str) -> list[str]:
    """Describe what a built repository actually contains.

    Args:
        path: Repository directory.
        name: Repository name.

    Returns:
        Markdown lines, or an empty list if nothing can be read.
    """
    repo = GitRepo(path)

    # Every count here must exclude the commit that writing this README creates,
    # or regenerating churns forever: the number goes up, which changes the file,
    # which makes another commit, which changes the number again.
    try:
        if name.startswith("us-congress-bills-"):
            measures = len(repo.branches() - {"main"})
            versions = int(
                repo._run("rev-list", "--count", "--all", "--not", "main").strip()  # noqa: SLF001
            )
            return [
                f"- **{measures:,} branches**, one per measure",
                f"- **{versions:,} commits**, one per text version",
                "- a `main` branch holding this README, the licence, and `GAPS.md`",
            ]
        if name == "us-congress-code":
            tags = len([t for t in repo._run("tag").splitlines() if t.strip()])  # noqa: SLF001
            return [
                f"- **{tags:,} release points**, each a commit and a tag",
                "- the full US Code as Markdown, one file per section",
                "- a `GAPS.md` recording release points that could not be built",
            ]
    except Exception:  # noqa: BLE001 - a repository mid-build simply reports less
        return []
    return []


def _usage(name: str) -> list[str]:
    """Render the how-to-use section, which differs by repository shape.

    Args:
        name: Repository name.

    Returns:
        Markdown lines.
    """
    url = f"git@github.com:{OWNER}/{name}.git"
    if name.startswith("us-congress-bills-"):
        congress = name.rsplit("-", 1)[-1]
        return [
            "```bash",
            f"git clone {url}",
            f"cd {name}",
            "",
            "# every measure is a branch named from its citation",
            "git branch --list 'hr-*' | head",
            "",
            "# read one bill, oldest version first",
            "git log --reverse --format='%ad  %s' --date=short hr-588",
            "",
            "# what changed between two versions of it",
            "git diff hr-588~2..hr-588 -- bill.md",
            "",
            "# who sponsored it, and where it had got to at that version",
            "git show hr-588:metadata.md",
            "",
            "# measures with no published text",
            "git show main:GAPS.md",
            "```",
            "",
            "Each branch holds exactly two files, rewritten at every version:",
            "",
            "| File | Contents |",
            "|---|---|",
            "| `bill.md` | the bill text as of that version |",
            "| `metadata.md` | sponsor, cosponsors, committees and actions **as of that version** |",
            "",
            f"Branches are named from the citation — `hr-588`, `s-1339`, "
            f"`sconres-13` — so a measure of the {congress}th Congress can be found "
            "without a lookup.",
        ]
    if name == "us-congress-code":
        return [
            "```bash",
            f"git clone {url}",
            f"cd {name}",
            "",
            "# the Code as of a given release point",
            "git checkout pl-118-22",
            "",
            "# one file per section",
            "cat title-18/chapter-93/sec-1924.md",
            "",
            "# what a release point changed",
            "git show --stat pl-118-22",
            "",
            "# release points omitted, and why",
            "git show main:GAPS.md",
            "```",
            "",
            "Sections live at `title-NN/chapter-NN/sec-NNN.md`. Appendix titles keep "
            "their letter: `title-05a`, `title-50a`.",
        ]
    return ["```bash", f"git clone {url}", "```"]


def _caveats(name: str) -> list[str]:
    """Render the caveats a consumer has to know before trusting the data.

    Args:
        name: Repository name.

    Returns:
        Markdown lines.
    """
    common = [
        "This is a **derived copy**, not an official source of United States law. "
        "It is generated automatically and published without review. For "
        "authoritative text, use "
        "[uscode.house.gov](https://uscode.house.gov) or "
        "[govinfo.gov](https://www.govinfo.gov).",
    ]
    if name.startswith("us-congress-bills-"):
        return common + [
            "",
            "**A diff between two versions of a bill shows how the bill changed, "
            "not how the US Code would change.** Bills are written as amendatory "
            "instructions — *strike subsection (b) and insert…* — not as diffs, and "
            "executing them automatically is unsolved: measured across seven real "
            "bills, only ~49% of instructions carry a machine-readable US Code "
            "reference. Nothing here attempts it.",
            "",
            "**`metadata.md` is as of its version, not final.** The upstream record "
            "is a single present-day snapshot of the whole measure. Written "
            "unfiltered it would have a bill's introduced text already reporting "
            "that it became law, so cosponsors and actions are filtered to each "
            "version's date.",
            "",
            "**Ordering comes from the upstream metadata, not the bill files.** "
            "Engrossed, enrolled and received versions carry no date of their own, "
            "and a reported version repeats the introduction date. Where no date is "
            "published at all — usually the enrolled bill — the preceding version's "
            "date is carried forward, and the commit message says so.",
            "",
            "**Coverage of older Congresses is thin.** Some measures have no "
            "published text at all and therefore no branch; `GAPS.md` lists every "
            "one. This is heavily concentrated before the 111th Congress.",
        ]
    if name == "us-congress-code":
        return common + [
            "",
            "**Commit dates are publication dates, not enactment dates.** A release "
            "point closes over several public laws at once, so a commit is not the "
            "effect of a single law.",
            "",
            "**Attribution trailers record present-day classification.** They answer "
            "*where does this law live now*, not *what did this commit change*. "
            "PL 113-40 (2013) is listed under Title 54, which did not exist until "
            "December 2014. For what changed, read the diff.",
        ]
    return common


def readme(name: str, path: Path, built: set[str]) -> str:
    """Build one repository's README.

    Args:
        name: Repository name.
        path: Repository directory.
        built: Names of repositories that exist on disk.

    Returns:
        The full document.
    """
    entry = next(
        (r for r in REPOSITORIES if r.name == name),
        None,
    )
    if entry is None:
        # A shard: find its family template, e.g. us-congress-bills-{congress}.
        entry = next(
            r
            for r in REPOSITORIES
            if "{" in r.name and name.startswith(r.name.replace("{congress}", ""))
        )

    congress = name.rsplit("-", 1)[-1] if name.startswith("us-congress-bills-") else ""
    title = f"{name}"
    subtitle = (
        f"Measures of the {congress}th Congress, each as a branch."
        if congress
        else entry.summary
    )

    lines = [
        f"# {title}",
        "",
        subtitle,
        "",
        f"Generated by [`{PIPELINE_REPO}`]({PIPELINE_URL}). This repository holds "
        "data only: nothing in it is edited by hand, and all of it can be rebuilt "
        "from the upstream sources at any time.",
        "",
        # Someone who finds this repository on its own cannot tell whether it is
        # maintained or abandoned, and the archived predecessors of this project
        # look identical to a live one from the inside. Point at the heartbeat.
        f"**Is this still maintained?** [`STATUS.md`]({PIPELINE_URL}/blob/main/STATUS.md) "
        "in the pipeline carries the date the daily job last ran. If that date has "
        "stopped moving, so has this.",
        "",
        "## What is here",
        "",
        *_facts(path, name),
        "",
        f"Built from {entry.source}.",
        "",
        "## Using it",
        "",
        *_usage(name),
        "",
        "## What to watch out for",
        "",
        *_caveats(name),
        "",
        "## The other repositories",
        "",
        f"This is one of a set. The [pipeline]({PIPELINE_URL}) that generates them "
        "tracks what exists and what is still queued.",
        "",
        *_related_table(name, built),
        "",
        "## Licence",
        "",
        "The federal text here is a work of the United States Government and is not "
        "subject to copyright under 17 U.S.C. § 105 — it is public domain, and no "
        "rights are asserted over it.",
        "",
        "The layer this project authored around it — the Markdown conversion, the "
        "structure of the history, the commit messages and the prose in files like "
        "this one — is © 2026 Jade Naaman, all rights reserved. See "
        "[`LICENSE`](LICENSE).",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def write_repo(path: Path, name: str, built: set[str]) -> bool:
    """Write README and LICENSE into one generated repository.

    Bills repositories keep these on ``main`` beside ``GAPS.md``, written through
    fast-import so the working tree is untouched. The US Code repository has a
    working tree already, and its ``main`` carries the Code itself, so the files
    are committed there normally.

    Args:
        path: Repository directory.
        name: Repository name.
        built: Names of repositories that exist on disk.

    Returns:
        True if a commit was made.
    """
    repo = GitRepo(path)
    files = {"README.md": readme(name, path, built), "LICENSE": LICENSE_DATA}

    # Check before committing, not after. These are written in bulk into
    # repositories that are then published, so a bad link in the template lands
    # in all of them at once; catching it here means it is never committed.
    existing_files = repo.list_files("main")
    broken = check_document(
        files["README.md"],
        repo=name,
        document="README.md",
        files=existing_files | set(files),
        repos=built | {PIPELINE_REPO},
    )
    if broken:
        detail = "; ".join(str(link) for link in broken)
        raise ValueError(f"refusing to write a README with broken links: {detail}")
    message = (
        "Add README and licence\n"
        "\n"
        "So the repository explains itself to anyone who finds it without\n"
        "the pipeline, and states the terms for the two different kinds of\n"
        "material it holds.\n"
    )

    if name.startswith("us-congress-bills-"):
        existing = repo.read_tree("main")
        merged = {**existing, **files}
        if merged == existing:
            return False
        with repo.fast_import() as stream:
            stream.commit("main", merged, message)
        return True

    for filename, content in files.items():
        repo.write(filename, content)
    return repo.commit(message)


def write_all() -> list[str]:
    """Write artifacts into every generated repository present on disk.

    Returns:
        Names of repositories that changed.
    """
    present = sorted(
        p for p in config.REPOS_DIR.glob("us-congress-*") if (p / ".git").is_dir()
    )
    # A preserved pre-fix copy is not a repository anyone consumes.
    present = [p for p in present if not p.name.endswith(".pre-fix")]
    built = {p.name for p in present}

    changed = []
    for path in present:
        if write_repo(path, path.name, built):
            changed.append(path.name)
            print(f"  {path.name}: README.md, LICENSE", flush=True)
        else:
            print(f"  {path.name}: unchanged", flush=True)

    licence = config.REPO_ROOT / "LICENSE"
    if not licence.is_file() or licence.read_text(encoding="utf-8") != LICENSE_PIPELINE:
        licence.write_text(LICENSE_PIPELINE, encoding="utf-8")
        changed.append(PIPELINE_REPO)
        print(f"  {PIPELINE_REPO}: LICENSE (commit it yourself)", flush=True)

    return changed
