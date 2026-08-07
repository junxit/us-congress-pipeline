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
        if name.startswith("us-congress-record-"):
            lines = []
            for branch, edition in (("daily", "daily edition"), ("bound", "bound edition")):
                days = int(repo._run("rev-list", "--count", branch).strip())  # noqa: SLF001
                if not days:
                    continue
                names = repo._run("ls-tree", "-r", "--name-only", branch).splitlines()  # noqa: SLF001
                # One README.md per issue day is navigation, not proceedings.
                documents = sum(1 for n in names if n.endswith(".md")) - days
                lines.append(
                    f"- **{days:,} issue days** on `{branch}`, the {edition}, "
                    f"holding **{documents:,} documents**"
                )
            if lines:
                lines.append(
                    "- a `main` branch holding this README, the licence, and `GAPS.md`"
                )
            return lines
        if name == "us-congress-code":
            tags = len([t for t in repo._run("tag").splitlines() if t.strip()])  # noqa: SLF001
            return [
                f"- **{tags:,} release points**, each a commit and a tag",
                "- the full US Code as Markdown, one file per section",
                "- a `GAPS.md` recording release points that could not be built",
            ]
        if name == "us-congress-statutes":
            tags = len([t for t in repo._run("tag").splitlines() if t.strip()])  # noqa: SLF001
            # Only files under volume-NNN/ are counted. README.md, LICENSE and
            # GAPS.md are on the same branch, and counting them would make this
            # number change every time this file is written -- which changes the
            # file, which changes the number.
            laws = len([p for p in repo.list_files("main") if p.startswith("volume-")])
            return [
                f"- **{tags:,} volumes**, each a commit and a tag",
                f"- **{laws:,} session laws** as enacted, one Markdown file each",
                "- a `GAPS.md` recording what is deliberately not here",
            ]
    except Exception:  # noqa: BLE001 - a repository mid-build simply reports less
        return []
    return []


def _record_examples(path: Path) -> tuple[str, str, str]:
    """Pick real paths out of a Record shard for its README's examples.

    Written rather than hard-coded because the hard-coded ones were wrong: the
    first draft told the reader to run
    ``git show daily:2017/01-03/senate/001-senate-chamber-action.md``, and that
    file does not exist -- the real first item of that day is
    ``001-congressional-record.md`` -- while the diff example named a day the
    bound edition does not carry. A README whose commands fail is worse than one
    with none, and shard contents differ, so the examples are read from the
    repository they will be published into.

    The earliest day is chosen deliberately: it does not move as the crawl
    progresses, so regenerating this file later does not churn the examples.

    Args:
        path: Repository directory.

    Returns:
        A ``(day, item, shared_day)`` triple. ``shared_day`` is empty when the
        bound edition holds nothing to compare against yet.
    """
    repo = GitRepo(path)

    def days(branch: str) -> list[str]:
        # A day is the first two segments, `YYYY/MM-DD`. Trimming from the right
        # instead gets this wrong, because a day's own README.md sits one level
        # shallower than the documents and collapses to just the year.
        return sorted(
            {
                "/".join(name.split("/")[:2])
                for name in repo.list_files(branch)
                if name.count("/") >= 2
            }
        )

    daily_days = days("daily")
    day = daily_days[0] if daily_days else "2017/01-03"
    items = sorted(
        name
        for name in repo.list_files("daily")
        if name.startswith(f"{day}/") and name.count("/") >= 3
    )
    item = items[0] if items else f"{day}/senate/001-congressional-record.md"
    shared = next((d for d in days("bound") if d in set(daily_days)), "")
    return day, item, shared


def _usage(name: str, path: Path) -> list[str]:
    """Render the how-to-use section, which differs by repository shape.

    Args:
        name: Repository name.
        path: Repository directory, read for the Record shards so their example
            commands name paths that actually exist.

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
    if name.startswith("us-congress-record-"):
        congress = name.rsplit("-", 1)[-1]
        day, item, shared = _record_examples(path)
        lines = [
            "```bash",
            f"git clone {url}",
            f"cd {name}",
            "",
            "# the daily edition: one commit per issue day, oldest first",
            "git log --reverse --format='%ad  %s' --date=short daily | head",
            "",
            "# everything printed on one day",
            f"git show --stat daily -- {day}/",
            "",
            "# read one item",
            f"git show daily:{item}",
        ]
        if shared:
            lines += [
                "",
                "# what the permanent edition changed about what was said",
                f"git diff bound daily -- {shared}/",
            ]
        lines += [
            "",
            "# what is missing, and why",
            "git show main:GAPS.md",
            "```",
            "",
        ]
        return lines + [
            "Two branches carry the same proceedings, published years apart:",
            "",
            "| Branch | Edition | Contents |",
            "|---|---|---|",
            "| `daily` | CREC | the issue printed the next morning |",
            "| `bound` | CRECB | the permanent edition, revised and repaginated |",
            "",
            "Files live at `YYYY/MM-DD/{senate,house,extensions,daily-digest}/`, "
            "numbered in the order they were printed. Each day also carries a "
            "`README.md` indexing that day.",
            "",
            "**The tree accumulates.** A commit adds the day's proceedings without "
            "removing the days before it, so a commit's diff *is* that day's "
            f"publication and `git log` is the {congress}th Congress's calendar. "
            "That is the opposite of the bills repositories, where each commit "
            "replaces the whole tree, and it is right here because the Record is "
            "a serial: an issue succeeds its predecessor rather than revising it.",
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
    if name == "us-congress-statutes":
        return [
            "```bash",
            f"git clone {url}",
            f"cd {name}",
            "",
            "# one commit per volume; the subject carries the years it covers",
            "git log --oneline",
            "",
            "# tags are named for the citation: stat-117 is 117 Stat.",
            "git show --stat stat-117",
            "",
            "# a law as it was enacted",
            "cat volume-117/public/public-law-108-1.md",
            "",
            "# the private relief acts of 1951, filed apart from general law",
            "ls volume-065/private/",
            "",
            "# everything the Statutes at Large prints that is not here",
            "cat GAPS.md",
            "```",
            "",
            "Laws live at `volume-NNN/{public,private,resolutions,organic}/`, named "
            "the way they are cited: `public-law-108-1.md`, `private-law-82-1.md`, "
            "`chapter-1-1-i.md` for the numbered chapters that preceded public law "
            "numbering in 1957.",
            "",
            "Each file opens with frontmatter carrying its `citation` (*117 Stat. 3*), "
            "its `approved` date and, where GPO records one, the `bills` reference — "
            "`108/s-23`, which is branch `s-23` of `us-congress-bills-108`.",
            "",
            "Tags are `stat-001` to `stat-137`, named for the citation rather than "
            "for the directory. `volume-117` would be both a tag and a path, and git "
            "refuses an argument that is both — `git log volume-117` fails with "
            "*ambiguous argument*.",
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
    if name.startswith("us-congress-record-"):
        return common + [
            "",
            "**The machine-readable Record begins in 1994, not 1873.** The "
            "Congressional Record has been published since 1873 and govinfo holds "
            "2,420 bound-edition parts covering all of it, but only the 337 from "
            "1999 onwards carry text: the rest are scanned page images whose "
            "granules offer a PDF and no text rendition at all. That century is "
            "unbuildable here, not merely unbuilt. `GAPS.md` names what is "
            "affected.",
            "",
            "**A diff between commits is not a revision.** The tree accumulates, "
            "so each commit's diff is the proceedings printed that day. To see "
            "what was actually *changed* about a day, compare the two editions: "
            "`git diff bound daily -- YYYY/MM-DD/`.",
            "",
            "**An issue day is a legislative day, not a calendar day.** The Senate "
            "may hold one sitting across two dates, and it prints under the first: "
            "the 6–7 February 2017 sitting appears under 6 February, so "
            "7 February has no Senate section at all. That is the chamber's "
            "convention, not a gap.",
            "",
            "**Not everything here was spoken aloud.** Material inserted into the "
            "Record rather than delivered on the floor is marked ● in the source "
            "and kept as ● here, because the distinction is the whole reason the "
            "mark exists.",
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
    if name == "us-congress-statutes":
        return common + [
            "",
            "**A diff here is not a change in the law.** A session law is printed "
            "as passed and is never amended — a later Congress supersedes it, it "
            "does not rewrite the page. The only thing that ever changes is GPO's "
            "transcription, so a diff between two commits for the same volume is a "
            "correction to the text of the *record*, not to the law.",
            "",
            "**Commit dates before 1970 are all 1970-01-01.** git stores no "
            "timestamp earlier than the Unix epoch, and 82 of the 137 volumes close "
            "before then. The real dates are on each commit's subject line, in its "
            "message, and in the `approved:` frontmatter of every law.",
            "",
            "**Treaties and proclamations are not here.** The Statutes at Large "
            "prints them alongside the session laws, but they are not acts of "
            "Congress passed by both chambers and presented for signature. Volumes "
            "7 and 8 contain nothing else and therefore have no commit at all; "
            "`GAPS.md` says so.",
            "",
            "**Marginal notes have been moved, not deleted.** In the printed volume "
            "they sit in the margin; in the source XML they sit *inside* the "
            "sentence, often mid-clause. Reproducing that position would splice a "
            "note into the middle of a provision, so they are collected under a "
            "heading of their own at the end of each law.",
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
    session = name.rsplit("-", 1)[-1] if name.startswith("us-congress-record-") else ""
    title = f"{name}"
    if congress:
        subtitle = f"Measures of the {congress}th Congress, each as a branch."
    elif session:
        subtitle = (
            f"Floor proceedings of the {session}th Congress, one commit per issue day."
        )
    else:
        subtitle = entry.summary

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
        *_usage(name, path),
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

    if not _has_working_tree(path):
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


def _has_working_tree(path: Path) -> bool:
    """Report whether ``main`` is genuinely checked out on disk.

    Which write path is correct is a property of how the repository was *built*,
    not of its name. This used to test the name prefix ``us-congress-bills-``,
    and the Congressional Record shards -- also written through fast-import,
    also with nothing checked out -- did not match it. They took the
    working-tree path, where ``git add -A`` sees a directory holding only the
    two files just written and stages the deletion of everything else on
    ``main``. It removed ``GAPS.md`` from ``us-congress-record-115``: the same
    trap ``bills._write_gaps`` documents, arriving from the opposite direction
    and just as quietly, because deleting a file is an ordinary commit.

    The question has to be *is every tracked file present*, not *is anything
    present*. The first attempt at this asked the weaker one and was defeated by
    its own wreckage: the buggy run had already written ``README.md`` and
    ``LICENSE`` into the directory, so the next run saw two files, concluded
    there was a working tree, and deleted ``GAPS.md`` a second time.

    ``all`` short-circuits, so a fast-import repository costs one stat rather
    than a walk of 101,975 files.

    Args:
        path: Repository directory.

    Returns:
        True if every file on ``main`` exists on disk.
    """
    tracked = GitRepo(path).list_files("main")
    if not tracked:
        # Nothing committed yet: the only signal left is whether anything is
        # staged for a first commit.
        return any(child.name != ".git" for child in path.iterdir())
    return all((path / name).exists() for name in tracked)


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
