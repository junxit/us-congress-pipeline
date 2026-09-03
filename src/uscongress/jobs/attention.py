"""Compute what currently needs a person, rather than remembering it.

``CLAUDE.md`` kept a hand-written list titled "Maintenance that needs a person".
A hand-written list of things to remember is the thing this project is least
willing to rely on: it cannot go stale visibly, and nobody rereads it. This is
the generated version, which is the project's own rule -- prefer generated over
hand-maintained.

Every condition here is evaluated against live state and answers one question:
*is there something a schedule cannot do that is due now?* Almost everything
else is automated; what is left is genuinely irreducible, and most of it lands
on one date, the day the next Congress convenes.

**An evaluation that fails reports itself.** A check that cannot reach GitHub
must not read as "nothing is due" -- that is the silent-success failure the rest
of the project is shaped against -- so it becomes a condition of its own saying
it could not be answered. The list is empty only when every question was asked
and every answer was no.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .. import config
from ..govinfo import GovInfoClient
from ..registry import OWNER, PIPELINE_REPO, REPOSITORIES
from . import publish, record
from . import update as update_job

#: Where the rendered list is persisted for ``STATUS.md``. Beside the two
#: heartbeats and tracked for the same reason: a scheduled runner is a fresh
#: machine every time.
STATE_PATH = config.STATE_DIR / "attention.json"

#: How old a COMPS snapshot may be before it is worth saying so. Two days, like
#: the heartbeat threshold: one missed day is a hiccup, two is a pattern -- and
#: unlike every other job here, a missed day is not recoverable.
COMPS_STALE_AFTER = timedelta(days=2)

#: ``Extracted YYYY-MM-DD`` in the vendored crosswalk's module docstring.
_EXTRACTED = re.compile(r"^Extracted (\d{4}-\d{2}-\d{2})", re.MULTILINE)


@dataclass(frozen=True)
class Condition:
    """One thing that needs a person.

    Attributes:
        key: Stable identifier, so the same condition is recognisable across
            runs -- the GitHub issue is edited rather than duplicated on it.
        summary: What is true, in one line.
        action: What a person has to do about it.
    """

    key: str
    summary: str
    action: str


def _sitting_congress(today: date | None = None) -> int:
    """Return the Congress sitting today.

    Args:
        today: Override the date, for tests.

    Returns:
        The Congress number.
    """
    return record.congress_of(today or datetime.now(UTC).date())


def shards_exist(congress: int) -> list[Condition]:
    """Check that the sitting Congress's repositories exist on GitHub.

    The 120th convening is the ordinary way to reach this: govinfo starts
    reporting a shard nothing has created yet, and both loops then fail loudly
    every day until someone creates it. Saying so weeks ahead turns an outage
    into an errand.

    Args:
        congress: Congress to check.

    Returns:
        One condition per missing repository.
    """
    due = []
    for name in (f"us-congress-bills-{congress}", f"us-congress-record-{congress}"):
        if not publish.remote_exists(publish.repo_url(name)):
            due.append(
                Condition(
                    key=f"repo-missing:{name}",
                    summary=f"`{name}` does not exist on GitHub, "
                    f"and the {congress}th Congress is sitting",
                    action=f"Create {name}, add it to DATA_REPO_TOKEN's "
                    "repository list by hand, then run `uscongress artifacts` "
                    "and `uscongress describe`",
                )
            )
    return due


def registry_repos_exist() -> list[Condition]:
    """Check every repository this project claims to produce is on GitHub.

    Distinct from :func:`shards_exist`, which watches the sitting Congress. This
    catches the other way a repository comes to be owed: the pipeline learns to
    build something new, it is built and verified locally, and then it waits on
    the two steps no API can take -- creating the repository and adding it to a
    fine-grained token's list.

    Only repositories that exist *here* are asked about. One that has never been
    built is not owed to anybody; it is simply not written yet.

    Returns:
        One condition per repository built locally and absent from GitHub.
    """
    due = []
    for entry in REPOSITORIES:
        if entry.is_pipeline or "{" in entry.name:
            continue
        if not (config.REPOS_DIR / entry.name / ".git").is_dir():
            continue
        if publish.remote_exists(publish.repo_url(entry.name)):
            continue
        due.append(
            Condition(
                key=f"repo-unpublished:{entry.name}",
                summary=f"`{entry.name}` is built here and does not exist on "
                "GitHub, so nothing it produces is being published",
                action=f"`gh repo create junxit/{entry.name} --public`, add it "
                "to DATA_REPO_TOKEN's repository list by hand, then run "
                "`uscongress artifacts` and `uscongress describe`",
            )
        )
    return due


def token_can_publish(token: str) -> list[Condition]:
    """Check the publishing credential can actually write where it must.

    :func:`registry_repos_exist` asks whether a repository is there, which is a
    different question and was not enough. ``us-congress-comps`` existed, was
    public, and read perfectly through ``ls-remote`` -- while the credential the
    daily job pushes with could not write to it, because a fine-grained token
    reaches only the repositories on its list and no API can add one. Nothing
    could say so; the signal was a scheduled run going red the next morning.

    ``DATA_REPO_TOKEN`` is scoped deliberately, so this recurs by design every
    time a repository is created: the 120th Congress convening will add two.

    Asked of every repository the pipeline publishes, because the answer is
    per-repository -- that is the whole nature of the scoping.

    Args:
        token: The credential to test. Empty means this machine does not
            publish, and nothing is checked: the case that matters, a scheduled
            run holding an empty secret, is already a loud failure in
            :func:`uscongress.jobs.update.run` and is on the heartbeat.

    Returns:
        One condition per repository the credential cannot push to, or one
        saying the question could not be asked.
    """
    if not token:
        print("  push access: not checked, no credential here", flush=True)
        return []

    # Asked of GitHub, not of the filesystem. Deriving the shards from
    # `built_shards` reads `data/repos`, which is empty on a scheduled runner --
    # so the first CI run of this check reported "3 of 3 repositories writable"
    # and had silently skipped all 29 shards, the ones the January failure will
    # actually land on. `bootstrap.remote_repositories` documents this exact
    # trap; the authority for what exists is the place the repositories are.
    from . import bootstrap

    names = [n for n in bootstrap.remote_repositories() if n != PIPELINE_REPO]
    if not names:
        return [
            Condition(
                key="push-access-unknown",
                summary="the repository list could not be read, so whether the "
                "publishing credential can still write is unknown",
                action="Check `gh auth status`, then re-run",
            )
        ]

    due = []
    for name in sorted(names):
        status = publish.can_push(publish.repo_url(name, token))
        if status == 200:
            continue
        # 401 and 0 are facts about the credential or the network, not about
        # this repository, so they are reported once and the sweep stops.
        # Emitting them per-repository would turn one expired token into
        # thirty-two identical lines and bury whatever else was due.
        if status == 401:
            return [
                Condition(
                    key="push-credential-rejected",
                    summary="GitHub does not recognise `DATA_REPO_TOKEN`; "
                    "nothing can be published at all",
                    action="Mint a replacement at "
                    "https://github.com/settings/personal-access-tokens with "
                    "Contents: read/write on the `us-congress-*` repositories, "
                    "then update the DATA_REPO_TOKEN secret",
                )
            ]
        if status == 0:
            return [
                Condition(
                    key="push-access-unknown",
                    summary="whether the publishing credential can still write "
                    "could not be determined",
                    action="Check network access to github.com, then re-run",
                )
            ]
        reason = {
            403: "the credential is valid but has no write access to it",
            404: "GitHub will not show it to this credential, which for a "
            "fine-grained token means it is not on the token's list",
        }.get(status, f"GitHub answered {status}")
        due.append(
            Condition(
                key=f"push-denied:{name}",
                summary=f"`DATA_REPO_TOKEN` cannot push to `{name}` — {reason}",
                action="Add it under Repository access at "
                "https://github.com/settings/personal-access-tokens . Editing "
                "the list does not change the token, so the secret stays as is",
            )
        )
    # Said out loud because "nothing needs a person" cannot distinguish a
    # question answered no from a question never asked -- which is the exact
    # failure this whole check exists to prevent, and it would be absurd for the
    # check to commit it. A run that skipped this because it found no credential
    # says so above instead.
    print(
        f"  push access: {len(names) - len(due)} of {len(names)} repositories "
        "writable",
        flush=True,
    )
    return due


def schedules_enabled() -> list[Condition]:
    """Check that every scheduled workflow is still enabled.

    GitHub disables a scheduled workflow after 60 days without repository
    activity, and a disabled workflow never runs to report its own death. The
    two heartbeats cross-report each other for the same reason; this is the
    third case, where neither can, so it is asked of GitHub directly.

    Returns:
        One condition per workflow that is not active, or one saying the
        question could not be asked.
    """
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{OWNER}/{PIPELINE_REPO}/actions/workflows",
            "--jq",
            ".workflows[] | .name + \" \" + .state",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return [
            Condition(
                key="schedule-unknown",
                summary="whether the scheduled workflows are still enabled "
                "could not be determined",
                action="Check `gh auth status`, then look at the Actions tab",
            )
        ]
    due = []
    for line in result.stdout.split("\n"):
        name, _, state = line.strip().rpartition(" ")
        if name and state != "active":
            due.append(
                Condition(
                    key=f"schedule-disabled:{name}",
                    summary=f"the `{name}` workflow is `{state}`, not active",
                    action=f"Re-enable {name} in the Actions tab. GitHub "
                    "disables a schedule after 60 days without repository "
                    "activity",
                )
            )
    return due


def backlog(state: update_job.State) -> list[Condition]:
    """Surface the backlogs the daily loop already computes but never escalates.

    Both of these are deliberately not errors -- holding the watermark for an
    unplaceable package would freeze every measure behind it -- so they sit on
    ``STATUS.md`` indefinitely with nothing pressing them.

    Args:
        state: The bills loop's recorded state.

    Returns:
        A condition for each non-empty backlog.
    """
    due = []
    if state.pending_release_points:
        tags = ", ".join(f"`{t}`" for t in state.pending_release_points[:5])
        due.append(
            Condition(
                key="release-points-pending",
                summary=f"{len(state.pending_release_points)} US Code release "
                f"point(s) published upstream and not built: {tags}",
                action="Run `uscongress seed-code` where `us-congress-code` "
                "already is; a release point is built against its predecessor",
            )
        )
    if state.unplaceable:
        due.append(
            Condition(
                key="unplaceable-packages",
                summary=f"{len(state.unplaceable)} govinfo package(s) could not "
                "be mapped to a measure",
                action="Fix `bills.TYPES` or `update._PACKAGE_ID`, or explain "
                "the packages in GAPS.md",
            )
        )
    return due


def members_current(congress: int) -> list[Condition]:
    """Check the vendored members crosswalk covers the sitting Congress.

    Nothing fetches this table at build time and nothing should, so a senator
    seated after it was extracted simply gets no bioguide ID -- which renders
    exactly like the pre-crosswalk behavior and is invisible.

    Args:
        congress: Congress to check against.

    Returns:
        One condition if the table predates the Congress convening.
    """
    from .. import members

    try:
        found = _EXTRACTED.search(
            Path(members.__file__).read_text(encoding="utf-8")
        )
    except OSError:
        return []
    if not found:
        return []
    extracted = date.fromisoformat(found.group(1))
    convened, _ = record.congress_span(congress)
    if extracted >= convened:
        return []
    return [
        Condition(
            key="members-stale",
            summary=f"the members crosswalk was extracted {extracted}, before "
            f"the {congress}th Congress convened on {convened}",
            action="Run `data/scripts/build_members.py` and read the diff -- a "
            "changed bioguide ID moves votes from one senator to another",
        )
    ]


def comps_current(now: datetime | None = None) -> list[Condition]:
    """Check a COMPS snapshot has been taken recently.

    govinfo replaces Statute Compilations in place and keeps no archive, so a
    day without a snapshot is history that cannot be recovered. This is the one
    staleness here that is permanent rather than something the next run catches
    up on, which makes it the one most worth asking about.

    Asked of the published repository, not of ``data/comps``. The local store is
    gitignored and a scheduled runner has none of it, so reading it meant this
    check silently did nothing on the only machine that runs it daily -- the
    monitor for the irrecoverable job, absent from the loop that reports. Giving
    the snapshots a repository is what made them checkable from anywhere; this
    is the check finally using it.

    Args:
        now: Override the clock, for tests.

    Returns:
        One condition if the newest published snapshot is older than
        :data:`COMPS_STALE_AFTER`, or if the question could not be answered.
    """
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{OWNER}/us-congress-comps/commits?sha=snapshots&per_page=1",
            "--jq",
            ".[0].commit.committer.date",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return [
            Condition(
                key="comps-unknown",
                summary="when the Statute Compilations were last snapshotted "
                "could not be determined",
                action="Check `gh auth status`, then look at "
                f"https://github.com/{OWNER}/us-congress-comps",
            )
        ]
    try:
        newest = date.fromisoformat(result.stdout.strip()[:10])
    except ValueError:
        return [
            Condition(
                key="comps-unknown",
                summary="the newest Statute Compilations snapshot carries an "
                "unreadable date",
                action=f"Look at https://github.com/{OWNER}/us-congress-comps",
            )
        ]
    age = (now or datetime.now(UTC)).date() - newest
    if age <= COMPS_STALE_AFTER:
        return []
    return [
        Condition(
            key="comps-stale",
            summary=f"the newest COMPS snapshot is {newest}, {age.days} days old",
            action="Check the `comps` workflow. govinfo overwrites these in "
            "place and keeps no archive, so a missed day cannot be recovered",
        )
    ]


async def upstream_editions(client: GovInfoClient) -> list[Condition]:
    """Check upstream for editions this corpus does not carry.

    Two annual-ish events that nothing else in the project watches for: a new
    Statutes at Large volume, and GPO catching up on bound Congressional Record
    volumes past :data:`uscongress.jobs.record.LAST_BOUND_YEAR`.

    Args:
        client: HTTP client.

    Returns:
        One condition per edition that exists upstream and not here.
    """
    due: list[Condition] = []

    after = f"{record.LAST_BOUND_YEAR + 1}-01-01"
    today = datetime.now(UTC).date().isoformat()
    bound = await client.api_json(
        f"published/{after}/{today}", collection="CRECB", pageSize=1, offsetMark="*"
    )
    if bound.get("count"):
        due.append(
            Condition(
                key="bound-edition",
                summary=f"GPO has published {bound['count']} bound Record "
                f"package(s) after {record.LAST_BOUND_YEAR}",
                action="Bump `record.LAST_BOUND_YEAR`, then `seed-record "
                "--congress N` by hand for the shards that gain a bound branch",
            )
        )

    volumes = await client.api_json(
        "collections/STATUTE/1990-01-01T00:00:00Z", pageSize=1, offset=0
    )
    tags = publish.remote_tags(publish.repo_url("us-congress-statutes"))
    # Two volumes print no session law at all, so the tag count is legitimately
    # below the package count and only a *growing* gap means anything.
    if volumes.get("count", 0) > len(tags) + 2:
        due.append(
            Condition(
                key="statutes-volume",
                summary=f"govinfo carries {volumes['count']} Statutes volumes "
                f"against {len(tags)} tagged here",
                action="Run `uscongress seed-statutes`, then `uscongress "
                "republish --repo us-congress-statutes`",
            )
        )
    return due


async def check(
    client: GovInfoClient | None = None,
    state_path: Path | None = None,
    token: str = "",
) -> list[Condition]:
    """Collect everything that needs a person right now.

    Args:
        client: HTTP client for the upstream checks. Without one they are
            skipped and said to be skipped, rather than counted as passing.
        state_path: Override the bills loop's watermark location.
        token: The publishing credential, for the push-access check. Empty
            means this machine does not publish and that check is skipped.

    Returns:
        Every condition that is due, in a stable order.
    """
    congress = _sitting_congress()
    state = update_job.load_state(state_path)

    due: list[Condition] = []
    due += shards_exist(congress)
    due += registry_repos_exist()
    due += token_can_publish(token)
    due += schedules_enabled()
    due += backlog(state)
    due += members_current(congress)
    due += comps_current()

    if client is None:
        due.append(
            Condition(
                key="upstream-unchecked",
                summary="upstream editions were not checked (no govinfo client)",
                action="Re-run with a GOVINFO_API_KEY configured",
            )
        )
    else:
        due += await upstream_editions(client)
    return due


def save(due: list[Condition], path: Path | None = None) -> Path:
    """Persist the list for ``STATUS.md`` to render.

    Args:
        due: What is currently due.
        path: Override the location.

    Returns:
        The path written.
    """
    target = path or STATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "checked": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "due": [
                    {"key": c.key, "summary": c.summary, "action": c.action}
                    for c in due
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def load(path: Path | None = None) -> tuple[datetime | None, list[Condition]]:
    """Read back what was last due.

    A missing or damaged file reads as "never checked", which renders as an
    absent section rather than as a clean bill of health.

    Args:
        path: Override the location.

    Returns:
        When the check last ran, and what it found.
    """
    target = path or STATE_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, []
    if not isinstance(payload, dict):
        return None, []
    due = [
        Condition(
            key=str(item.get("key") or ""),
            summary=str(item.get("summary") or ""),
            action=str(item.get("action") or ""),
        )
        for item in payload.get("due") or []
        if isinstance(item, dict)
    ]
    return update_job._parse_stamp(payload.get("checked")), due  # noqa: SLF001


#: Title of the one issue this opens. Fixed, because it is how the issue is
#: found again: the job edits that issue rather than opening a second one, so a
#: condition that stays due for a month costs one notification, not thirty.
ISSUE_TITLE = "Needs a person"


def _issue_body(due: list[Condition]) -> str:
    """Render the issue body.

    Args:
        due: What is currently due.

    Returns:
        Markdown.
    """
    lines = [
        "Opened and maintained by the daily job. It edits this issue while "
        "anything is due and closes it when nothing is.",
        "",
        "| What | What to do |",
        "|---|---|",
    ]
    lines += [f"| {item.summary} | {item.action} |" for item in due]
    lines += [
        "",
        "Run `uv run uscongress attention` to reproduce this locally. The same "
        "list is on [`STATUS.md`](../blob/main/STATUS.md).",
    ]
    return "\n".join(lines)


def announce(due: list[Condition]) -> str:
    """Open, update or close the single issue that carries this list.

    The page is the signal that cannot stop firing; this is the one that
    reaches a person. Both exist because they fail differently -- an issue
    depends on a job running, and the page depends on somebody looking.

    Idempotent on :data:`ISSUE_TITLE`, so a condition that stays due for weeks
    produces one notification rather than one a day.

    Args:
        due: What is currently due.

    Returns:
        What it did, for logging.
    """

    def gh(*args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=False, timeout=60
        )
        return result.returncode, result.stdout.strip()

    code, out = gh(
        "issue",
        "list",
        "--state",
        "open",
        "--search",
        f'"{ISSUE_TITLE}" in:title',
        "--json",
        "number,title",
    )
    if code != 0:
        return "could not reach GitHub; issue not touched"
    try:
        existing = [
            row["number"]
            for row in json.loads(out or "[]")
            if row.get("title") == ISSUE_TITLE
        ]
    except json.JSONDecodeError:
        return "could not read the issue list; issue not touched"

    if due and not existing:
        code, out = gh(
            "issue", "create", "--title", ISSUE_TITLE, "--body", _issue_body(due)
        )
        return f"opened {out}" if code == 0 else "failed to open the issue"
    if due:
        code, _ = gh(
            "issue", "edit", str(existing[0]), "--body", _issue_body(due)
        )
        return f"updated issue #{existing[0]}" if code == 0 else "failed to update"
    if existing:
        gh(
            "issue",
            "comment",
            str(existing[0]),
            "--body",
            "Nothing needs a person any more; closing. This will reopen as a "
            "new issue if something comes due again.",
        )
        code, _ = gh("issue", "close", str(existing[0]))
        return f"closed issue #{existing[0]}" if code == 0 else "failed to close"
    return "nothing due, no issue open"


def report(due: list[Condition]) -> int:
    """Print what needs a person, in the shape the other checks use.

    Args:
        due: What is currently due.

    Returns:
        How many conditions are due, which the CLI turns into an exit code.
    """
    for condition in due:
        print(f"  {condition.summary}", flush=True)
        print(f"    → {condition.action}", flush=True)
    if due:
        things = "thing needs" if len(due) == 1 else "things need"
        print(f"\n{len(due)} {things} a person", flush=True)
    else:
        print("nothing needs a person", flush=True)
    return len(due)
