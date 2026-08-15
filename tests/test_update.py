"""Tests for the daily update loop and its heartbeat.

Every predecessor of this project died silently, so the tests here are weighted
towards the two things that make silence impossible: the watermark must never
advance past work that failed, and the heartbeat must go stale on its own when
nothing runs at all.

The idempotency test is the one the original plan listed and could never run
until the loop existed. Running the job twice against a pinned watermark must
produce no second commit -- not "no visible change", but the same commit SHAs,
because that is what makes re-processing an overlapping window free and the
overlap itself safe.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uscongress.gitbuild import GitRepo
from uscongress.jobs import bills, update
from uscongress.jobs import votes as votes_job
from uscongress.jobs.update import (
    OVERLAP,
    STALE_AFTER,
    Package,
    Result,
    State,
    changed_packages,
    check,
    load_state,
    parse_package_id,
    render_status,
    save_state,
)


def test_package_id_maps_to_a_measure() -> None:
    """The daily job knows a measure only by its govinfo identifier.

    Deriving Congress, type and number from it is what lets the job skip the
    listing endpoint entirely -- 10,038 entries for House bills of the 119th
    alone, fetched to learn URLs that were already predictable.
    """
    assert parse_package_id("BILLSTATUS-119hr7283") == ("119", "hr", "7283")
    assert parse_package_id("BILLSTATUS-113sconres13") == ("113", "sconres", "13")


def test_unplaceable_package_ids_are_rejected_not_guessed() -> None:
    """A package this pipeline cannot place must be reported, not silently
    dropped: a skipped package is indistinguishable from one that never
    existed, which is the class of gap that goes unnoticed for months.
    """
    assert parse_package_id("BILLS-119hr7283ih") is None
    assert parse_package_id("BILLSTATUS-119xyz1") is None  # not a measure type
    assert parse_package_id("nonsense") is None


def test_document_url_is_derived_from_the_identifier() -> None:
    """The bulk-data path is fully predictable, so no listing call is needed."""
    package = Package("BILLSTATUS-119hr7283", "119", "hr", "7283", "")

    assert package.url.endswith(
        "/bulkdata/BILLSTATUS/119/hr/BILLSTATUS-119hr7283.xml"
    )
    assert package.branch == "hr-7283"
    assert package.filename == "BILLSTATUS-119hr7283.xml"


class _Listing:
    """A govinfo collections endpoint that pages, as the real one does."""

    def __init__(self, pages: list[list[str]]) -> None:
        self._pages = pages
        self.marks: list[str] = []

    async def api_json(self, path: str, **params: object) -> dict:
        mark = str(params["offsetMark"])
        self.marks.append(mark)
        index = 0 if mark == "*" else int(mark)
        page = self._pages[index] if index < len(self._pages) else []
        # govinfo keeps serving a nextPage on the last page, pointing back at
        # the mark just consumed.
        nxt = min(index + 1, len(self._pages) - 1)
        return {
            "count": sum(len(p) for p in self._pages),
            "nextPage": f"https://api.govinfo.gov/collections/x?offsetMark={nxt}",
            "packages": [
                {"packageId": pid, "lastModified": "2026-08-06T00:00:00Z"}
                for pid in page
            ],
        }


def test_paging_stops_at_the_last_page() -> None:
    """govinfo answers the final page with a nextPage pointing back at itself.

    Following it without noticing loops forever against a live API.
    """
    listing = _Listing([["BILLSTATUS-119hr1"], ["BILLSTATUS-119hr2"]])
    packages, unplaceable = asyncio.run(
        changed_packages(listing, datetime(2026, 8, 1, tzinfo=UTC))
    )

    assert [p.branch for p in packages] == ["hr-1", "hr-2"]
    assert unplaceable == []
    assert listing.marks == ["*", "1"]


def test_a_measure_listed_twice_is_built_once() -> None:
    """A measure touched twice inside one window is listed twice."""
    listing = _Listing([["BILLSTATUS-119hr1", "BILLSTATUS-119hr1"]])
    packages, _ = asyncio.run(
        changed_packages(listing, datetime(2026, 8, 1, tzinfo=UTC))
    )

    assert [p.branch for p in packages] == ["hr-1"]


def test_unplaceable_packages_are_returned_for_reporting() -> None:
    """They are handed back rather than dropped, so the run can say so."""
    listing = _Listing([["BILLSTATUS-119hr1", "BILLSTATUS-119quux9"]])
    packages, unplaceable = asyncio.run(
        changed_packages(listing, datetime(2026, 8, 1, tzinfo=UTC))
    )

    assert [p.branch for p in packages] == ["hr-1"]
    assert unplaceable == ["BILLSTATUS-119quux9"]


def test_the_window_is_widened_for_clock_skew() -> None:
    """govinfo stamps lastModified on its own clock.

    An idempotent build has nothing to lose by asking twice, and everything to
    lose by asking from a moment that has already passed there.
    """
    success = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)
    assert State(last_success=success).since == success - OVERLAP


def test_a_first_run_reaches_back_a_week() -> None:
    """With no watermark there is no history to protect."""
    window = State().since
    assert timedelta(days=6) < datetime.now(UTC) - window < timedelta(days=8)


def _result(ok: bool = True) -> Result:
    """A finished run, successful or not."""
    started = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)
    result = Result(started=started, since=started - timedelta(days=1))
    result.rebuilt = {"119": ["hr-7283"]}
    if not ok:
        result.errors = ["BILLSTATUS-119hr1: HTTPStatusError: 500"]
    return result


def test_the_watermark_advances_only_on_success(tmp_path: Path) -> None:
    """A crash must re-fetch rather than skip.

    Advancing past a window in which something failed is how a gap becomes
    permanent: nothing ever asks for that window again.
    """
    state_path = tmp_path / "update.json"
    status_path = tmp_path / "STATUS.md"

    update._finish(State(), _result(ok=True), state_path, status_path)  # noqa: SLF001
    after_success = load_state(state_path)
    assert after_success.last_success == datetime(2026, 8, 7, 4, 0, tzinfo=UTC)
    assert after_success.last_outcome == "ok"

    later = _result(ok=False)
    later.started = datetime(2026, 8, 8, 4, 0, tzinfo=UTC)
    update._finish(after_success, later, state_path, status_path)  # noqa: SLF001
    after_failure = load_state(state_path)

    assert after_failure.last_run == datetime(2026, 8, 8, 4, 0, tzinfo=UTC)
    assert after_failure.last_success == datetime(2026, 8, 7, 4, 0, tzinfo=UTC)
    assert "500" in after_failure.last_outcome


def test_a_corrupt_watermark_reads_as_never_run(tmp_path: Path) -> None:
    """The safe default is more work, not less.

    Reading a damaged watermark as "up to date" would silently skip everything
    published while it was broken, and nothing would ever ask again.
    """
    broken = tmp_path / "update.json"
    broken.write_text("{not json", encoding="utf-8")

    assert load_state(broken).last_success is None


def test_state_round_trips(tmp_path: Path) -> None:
    """What is written must read back identically, or the overlap is wrong."""
    state = State(
        last_success=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_run=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_outcome="ok",
        measures={"119": ["hr-7283"]},
        release_points=["pl-119-102"],
        checked_since=datetime(2026, 8, 6, 3, 0, tzinfo=UTC),
    )
    path = save_state(state, tmp_path / "update.json")

    assert load_state(path) == state
    assert json.loads(path.read_text(encoding="utf-8"))["last_outcome"] == "ok"


def test_an_unplaceable_package_survives_on_the_heartbeat() -> None:
    """It must not fail the run, and must not vanish either.

    Failing would hold the watermark for a package that can never be placed,
    freezing every other measure behind it for ever. Advancing silently would
    lose it: the watermark moves past, and nothing ever asks for that window
    again. So the run continues and the gap is stated where it stays visible.
    """
    state = State(
        last_success=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_run=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_outcome="ok",
        unplaceable=["BILLSTATUS-119zz1"],
    )
    text = render_status(state)

    assert "## Packages that could not be placed" in text
    assert "`BILLSTATUS-119zz1`" in text
    assert "| Outcome | ok |" in text  # the run itself did not fail


def test_unplaceable_packages_persist_across_runs(tmp_path: Path) -> None:
    """They are written to the watermark, so they do not vanish on the next run."""
    result = _result(ok=True)
    result.unplaceable = ["BILLSTATUS-119zz1"]
    path = tmp_path / "update.json"
    update._finish(State(), result, path, tmp_path / "STATUS.md")  # noqa: SLF001

    assert load_state(path).unplaceable == ["BILLSTATUS-119zz1"]


def test_a_stopped_loop_is_reported_stale(tmp_path: Path, capsys) -> None:
    """The whole point: a job that stops running says so without firing.

    A disabled schedule or an expired token raises nothing and notifies nobody,
    so the check has to be something anyone can run from outside the job.
    """
    path = tmp_path / "update.json"
    save_state(
        State(
            last_success=datetime.now(UTC) - STALE_AFTER - timedelta(days=5),
            last_run=datetime.now(UTC) - STALE_AFTER - timedelta(days=5),
            last_outcome="ok",
        ),
        path,
    )

    assert check(path) == 1
    assert "STALE" in capsys.readouterr().out


def test_a_running_loop_passes_the_check(tmp_path: Path) -> None:
    """A job that ran this morning must not fail its own health check."""
    path = tmp_path / "update.json"
    save_state(
        State(
            last_success=datetime.now(UTC) - timedelta(hours=6),
            last_run=datetime.now(UTC) - timedelta(hours=6),
            last_outcome="ok",
        ),
        path,
    )

    assert check(path) == 0


def test_a_job_that_never_ran_is_not_exempt(tmp_path: Path) -> None:
    """Never having succeeded is the most stale a heartbeat can be.

    Treating an absent watermark as healthy would pass the check on a machine
    where the job has never once worked.
    """
    assert check(tmp_path / "absent.json") == 1


def test_the_heartbeat_leads_with_the_date(tmp_path: Path) -> None:
    """It is read by someone deciding whether the data can be trusted.

    "Last updated 3 weeks ago" on a public front page is legible to a stranger
    who knows nothing about this project, which is the only signal that
    survives the job's own death.

    The timestamp is relative on purpose. This test first pinned an absolute
    2026-08-07 and asserted the heartbeat read *current*, which is only true
    while that date sits inside the two-day staleness window -- so it passed
    when written and failed two days later, for no reason connected to the code.
    A test of "does a recent success read as current" has to date itself
    recently.
    """
    recent = datetime.now(UTC) - timedelta(hours=6)
    state = State(
        last_success=recent,
        last_run=recent,
        last_outcome="ok",
        measures={"119": ["hr-7283", "s-1"]},
    )
    text = render_status(state, _result())

    assert text.startswith("# Status\n")
    assert f"**Last successful update — {recent.strftime('%Y-%m-%d %H:%M UTC')}**" in text
    assert "| **Heartbeat** | current |" in text
    assert "`hr-7283`" in text


def test_the_heartbeat_marks_itself_stale() -> None:
    """The document says so too, not only the exit code."""
    state = State(
        last_success=datetime.now(UTC) - STALE_AFTER - timedelta(days=10),
        last_run=datetime.now(UTC) - STALE_AFTER - timedelta(days=10),
        last_outcome="ok",
    )
    assert "| **Heartbeat** | **stale** |" in render_status(state)


def test_a_quiet_day_is_distinguished_from_a_failure() -> None:
    """Congress does not sit every day.

    Zero measures changed is an ordinary result and must not read as the job
    having broken, or the signal that matters gets lost in noise.
    """
    state = State(
        last_success=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_run=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_outcome="ok",
    )
    text = render_status(state)

    assert "No measure changed on the last successful run" in text
    assert "not the same as the" in text


def test_work_done_that_changed_nothing_reads_differently() -> None:
    """Two different quiet days, and they should not read the same.

    Congress not sitting is one thing. govinfo restamping 381 measures whose
    content is unchanged is another: the job fetched all of them, rebuilt all of
    them, and found the commits already published. Reporting that as "nothing
    happened" hides the fact that the loop demonstrably worked.
    """
    state = State(
        last_success=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_run=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_outcome="ok",
    )
    busy = Result(
        started=state.last_success, since=state.last_success - timedelta(days=1)
    )
    busy.listed = 442
    busy.unchanged = 381
    busy.textless = 61
    text = render_status(state, busy)

    assert "381 measures were rebuilt" in text
    assert "No measure changed on the last successful run" not in text
    assert "| Measures govinfo reported modified | 442 |" in text
    assert "| Modified but still carrying no text | 61 |" in text


_TEXT_URL = (
    "https://www.govinfo.gov/content/pkg/BILLS-119hr7283ih/xml/BILLS-119hr7283ih.xml"
)

_STATUS = (
    "<?xml version='1.0'?><billStatus><bill>"
    "<number>7283</number><type>HR</type><congress>119</congress>"
    "<introducedDate>2026-01-30</introducedDate>"
    "<title>Ensuring Federal Purchasing Efficiency Act</title>"
    "<sponsors><item><fullName>Rep. Fallon, Pat [R-TX-4]</fullName>"
    "<bioguideId>F000246</bioguideId></item></sponsors>"
    "<committees><item><name>Oversight and Government Reform Committee</name>"
    "<chamber>House</chamber><activities><item><name>Referred To</name>"
    "<date>2026-01-30T15:32:10Z</date></item></activities></item></committees>"
    "<textVersions><item><type>Introduced in House</type>"
    "<date>2026-01-30T05:00:00Z</date>"
    f"<formats><item><url>{_TEXT_URL}</url></item></formats>"
    "</item></textVersions>"
    "</bill></billStatus>"
).encode()

_TEXT = (
    "<?xml version='1.0'?><bill><form><legis-num>H. R. 7283</legis-num></form>"
    "<legis-body><section><enum>1.</enum><header>Short title</header>"
    "<text>This Act may be cited as the Ensuring Federal Purchasing Efficiency Act.</text>"
    "</section></legis-body></bill>"
).encode()


class _Upstream:
    """govinfo, serving one changed measure and its single text version."""

    def __init__(self) -> None:
        self.fetched: list[str] = []

    async def api_json(self, path: str, **params: object) -> dict:
        return {
            "count": 1,
            "nextPage": "https://api.govinfo.gov/collections/x?offsetMark=*",
            "packages": [
                {
                    "packageId": "BILLSTATUS-119hr7283",
                    "lastModified": "2026-08-06T00:00:00Z",
                }
            ],
        }

    async def get_bytes(self, url: str) -> bytes:
        self.fetched.append(url)
        return _TEXT if "/BILLS-" in url else _STATUS


def _pinned(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point the job's filesystem at a temporary directory."""
    monkeypatch.setattr(update.config, "REPOS_DIR", tmp_path / "repos")
    monkeypatch.setattr(bills.config, "RAW_DIR", tmp_path / "raw")
    (tmp_path / "repos").mkdir()
    return tmp_path / "update.json", tmp_path / "STATUS.md"


def test_running_twice_produces_no_second_commit(monkeypatch, tmp_path: Path) -> None:
    """The idempotency check the original plan listed and could never run.

    The window overlaps by an hour on every run, so the same measure is
    re-processed constantly. That is only safe if a rebuild of unchanged data
    lands on the identical commit: the render is a pure function of the
    document and the stamps come from the version dates, so the SHA has to
    match. Anything that leaked the wall clock into a commit would show up
    here as a repository that grows a commit a day, for ever.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    upstream = _Upstream()
    since = datetime(2026, 8, 1, tzinfo=UTC)

    first = asyncio.run(
        update.run(
            upstream,
            since=since,
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )
    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")
    after_first = repo.ref_map()

    second = asyncio.run(
        update.run(
            upstream,
            since=since,
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )
    after_second = repo.ref_map()

    assert first.measures_changed == 1
    assert first.rebuilt == {"119": ["hr-7283"]}
    assert second.measures_changed == 0
    assert second.unchanged == 1
    assert after_first == after_second
    assert repo._run("rev-list", "--count", "hr-7283").strip() == "1"  # noqa: SLF001


def test_the_status_document_is_refetched_but_the_text_is_not(
    monkeypatch, tmp_path: Path
) -> None:
    """BILLSTATUS is rewritten upstream whenever a measure moves.

    Re-reading yesterday's cached copy is exactly what the daily job exists to
    avoid. A published text version, by contrast, never changes, so refetching
    it would be pure waste at 134,013 versions.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    upstream = _Upstream()
    since = datetime(2026, 8, 1, tzinfo=UTC)

    for _ in range(2):
        asyncio.run(
            update.run(
                upstream,
                since=since,
                state_path=state_path,
                status_path=status_path,
                code=False,
            )
        )

    status_fetches = [u for u in upstream.fetched if "BILLSTATUS" in u]
    text_fetches = [u for u in upstream.fetched if "/BILLS-" in u]
    assert len(status_fetches) == 2
    assert len(text_fetches) == 1


def test_a_run_writes_the_heartbeat_even_with_nothing_to_do(
    monkeypatch, tmp_path: Path
) -> None:
    """A quiet day still has to move the date, or silence reads as death."""
    state_path, status_path = _pinned(monkeypatch, tmp_path)

    class _Empty(_Upstream):
        async def api_json(self, path: str, **params: object) -> dict:
            return {"count": 0, "nextPage": "", "packages": []}

    result = asyncio.run(
        update.run(
            _Empty(),
            since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )

    assert result.ok
    assert result.listed == 0
    assert load_state(state_path).last_success is not None
    assert "# Status" in status_path.read_text(encoding="utf-8")


def test_a_failed_listing_does_not_advance_the_watermark(
    monkeypatch, tmp_path: Path
) -> None:
    """govinfo being down must not be recorded as a day with no changes."""
    state_path, status_path = _pinned(monkeypatch, tmp_path)

    class _Broken(_Upstream):
        async def api_json(self, path: str, **params: object) -> dict:
            raise RuntimeError("503 Service Unavailable")

    result = asyncio.run(
        update.run(
            _Broken(),
            since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )

    assert not result.ok
    assert load_state(state_path).last_success is None
    assert load_state(state_path).last_run is not None
    assert "503" in load_state(state_path).last_outcome


def test_a_branch_nobody_asked_for_fails_the_run(monkeypatch, tmp_path: Path) -> None:
    """Nothing outside the listed measures may move.

    ``main`` carries the README, the license and GAPS.md, and the measure
    rebuild regenerates none of them -- fast-import's ``deleteall`` sets a
    commit's whole tree, so a stray write there deletes all three silently. The
    check is against git rather than against the builder's own count, because a
    build that reported 10,617 branches for 10,637 measures once looked
    completely successful.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")
    repo.init()

    real_commit = update.bills.commit_message

    def also_writes_main(measure, version):
        with repo.fast_import() as stream:
            stream.commit("main", {"README.md": "clobbered\n"}, "stray")
        monkeypatch.setattr(update.bills, "commit_message", real_commit)
        return real_commit(measure, version)

    monkeypatch.setattr(update.bills, "commit_message", also_writes_main)
    result = asyncio.run(
        update.run(
            _Upstream(),
            since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )

    assert not result.ok
    assert any("did not ask for" in e for e in result.errors)
    assert load_state(state_path).last_success is None


def test_the_committee_fix_reaches_the_built_branch(
    monkeypatch, tmp_path: Path
) -> None:
    """The daily job must write the corrected metadata, not the old shape.

    This is why the selector was fixed before the loop was stood up: otherwise
    the job would have written the same wrong ``metadata.md`` every day.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    asyncio.run(
        update.run(
            _Upstream(),
            since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path,
            status_path=status_path,
            code=False,
        )
    )

    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")
    metadata = repo.read_tree("hr-7283")["metadata.md"]
    assert "## Committees (1)" in metadata
    assert "House — Oversight and Government Reform Committee" in metadata


def test_publishing_without_a_credential_is_recorded_not_exited(
    monkeypatch, tmp_path: Path
) -> None:
    """A missing token must leave the heartbeat telling the truth.

    This was an argparse error, which exits before anything is written. The
    first real CI run had an empty DATA_REPO_TOKEN secret: the run failed, the
    workflow failed, GitHub notified -- and STATUS.md went on publishing
    "Outcome | ok" from the previous day, because nothing had reached `_finish`.
    A public heartbeat that reads healthy through a failed run is the one lie
    this module exists to prevent.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)

    result = asyncio.run(
        update.run(
            _Upstream(),
            since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path,
            status_path=status_path,
            code=False,
            token="",
            publish=True,
        )
    )

    assert not result.ok
    assert "GITHUB_TOKEN is empty" in result.errors[0]
    # The watermark must not advance, so tomorrow covers this window again.
    assert load_state(state_path).last_success is None
    assert load_state(state_path).last_run is not None
    # And the heartbeat must say so out loud.
    assert "GITHUB_TOKEN is empty" in status_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Roll-call votes in the daily loop
# --------------------------------------------------------------------------

_ROLL_URL = "https://clerk.house.gov/evs/2026/roll042.xml"

#: The engrossed text, published the day after the vote that produced it.
_ENGROSSED_URL = (
    "https://www.govinfo.gov/content/pkg/BILLS-119hr7283eh/xml/BILLS-119hr7283eh.xml"
)

#: The same measure, carrying the recorded vote BILLSTATUS nests inside an
#: action, and the engrossed version that vote produced. The vote is stamped
#: ``2026-02-12T01:30:00Z`` -- 8:30pm Eastern on the 11th -- and the Clerk dates
#: it the 11th, so the two sources disagree about which day it belongs to.
_STATUS_VOTED = _STATUS.replace(
    b"<textVersions>",
    (
        "<actions><item><actionDate>2026-02-11</actionDate>"
        "<text>Passed the House.</text><recordedVotes><recordedVote>"
        "<rollNumber>42</rollNumber>"
        f"<url>{_ROLL_URL}</url>"
        "<chamber>House</chamber><congress>119</congress>"
        "<date>2026-02-12T01:30:00Z</date><sessionNumber>2</sessionNumber>"
        "</recordedVote></recordedVotes></item></actions><textVersions>"
        "<item><type>Engrossed in House</type><date>2026-02-11T05:00:00Z</date>"
        f"<formats><item><url>{_ENGROSSED_URL}</url></item></formats></item>"
    ).encode(),
)

#: One House roll call, trimmed. The DOCTYPE is kept because it is the trap.
_ROLL = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<!DOCTYPE rollcall-vote PUBLIC "-//US Congress//DTDs/vote v1.0 20031119 //EN"'
    ' "../vote.dtd">\r\n'
    "<rollcall-vote><vote-metadata>"
    "<congress>119</congress><session>2nd</session>"
    "<chamber>U.S. House of Representatives</chamber>"
    "<rollcall-num>42</rollcall-num><legis-num>H R 7283</legis-num>"
    "<vote-question>On Passage</vote-question><vote-type>YEA-AND-NAY</vote-type>"
    "<vote-result>Passed</vote-result><action-date>11-Feb-2026</action-date>"
    '<action-time time-etz="20:30">8:30 PM</action-time>'
    "<vote-totals><totals-by-vote><total-stub>Totals</total-stub>"
    "<yea-total>1</yea-total><nay-total>1</nay-total>"
    "<present-total>0</present-total><not-voting-total>0</not-voting-total>"
    "</totals-by-vote></vote-totals></vote-metadata><vote-data>"
    '<recorded-vote><legislator name-id="A000055" unaccented-name="Aderholt"'
    ' party="R" state="AL">Aderholt</legislator><vote>Yea</vote></recorded-vote>'
    '<recorded-vote><legislator name-id="B000213" unaccented-name="Bishop"'
    ' party="D" state="GA">Bishop</legislator><vote>Nay</vote></recorded-vote>'
    "</vote-data></rollcall-vote>"
).encode()


class _UpstreamWithVote(_Upstream):
    """govinfo and the House Clerk, serving one measure that was voted on."""

    async def get_bytes(self, url: str) -> bytes:
        self.fetched.append(url)
        if url == _ROLL_URL:
            return _ROLL
        return _TEXT if "/BILLS-" in url else _STATUS_VOTED


def test_a_measure_with_a_vote_still_reaches_a_fixed_point(
    monkeypatch, tmp_path: Path
) -> None:
    """Votes must not be what makes the daily loop churn.

    The window overlaps by an hour, so every measure is reprocessed constantly
    and a rebuild of unchanged data has to land on the identical commit. Votes
    add a second upstream and a whole directory to the tree, and any ordering
    or timestamp that leaked in from the fetch would show up here as a
    repository growing a commit a day for ever.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path / "raw")
    upstream = _UpstreamWithVote()
    since = datetime(2026, 8, 1, tzinfo=UTC)

    first = asyncio.run(
        update.run(
            upstream, since=since, state_path=state_path,
            status_path=status_path, code=False,
        )
    )
    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")
    after_first = repo.ref_map()

    second = asyncio.run(
        update.run(
            upstream, since=since, state_path=state_path,
            status_path=status_path, code=False,
        )
    )

    assert first.rebuilt == {"119": ["hr-7283"]}
    assert second.measures_changed == 0
    assert second.unchanged == 1
    assert after_first == repo.ref_map()
    # Introduced and engrossed, and no third commit from the second run.
    assert repo._run("rev-list", "--count", "hr-7283").strip() == "2"  # noqa: SLF001


def test_the_vote_lands_in_the_tree_and_the_commit_message(
    monkeypatch, tmp_path: Path
) -> None:
    """The daily loop is where most measures will first gain their votes."""
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path / "raw")

    asyncio.run(
        update.run(
            _UpstreamWithVote(), since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path, status_path=status_path, code=False,
        )
    )
    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")

    assert "votes/house-119-2-0042.md" in repo.list_files("hr-7283")
    message = repo._run("log", "-1", "--format=%B", "hr-7283")  # noqa: SLF001
    assert "Roll-Call: House 119-2-42 2026-02-11 Passed 1-1" in message


def test_a_vote_is_dated_by_the_chamber_not_by_the_billstatus_stamp(
    monkeypatch, tmp_path: Path
) -> None:
    """BILLSTATUS says 2026-02-12T01:30Z; the Clerk says 11 February.

    Both describe the same moment -- 8:30pm Eastern on the 11th. Taking the day
    off the UTC stamp would date the vote to the 12th, put it after a version
    published on the 11th instead of before, and move it onto a different
    commit. This is the live end-to-end form of the trap; 60 of 814 distinct
    vote stamps in the 113th Congress fall in that window.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    monkeypatch.setattr(votes_job.config, "RAW_DIR", tmp_path / "raw")

    asyncio.run(
        update.run(
            _UpstreamWithVote(), since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path, status_path=status_path, code=False,
        )
    )
    repo = GitRepo(tmp_path / "repos" / "us-congress-bills-119")
    message = repo._run("log", "-1", "--format=%B", "hr-7283")  # noqa: SLF001

    assert "2026-02-11" in message
    assert "2026-02-12" not in message


def test_the_heartbeat_can_be_written_somewhere_other_than_the_tracked_copy(
    monkeypatch, tmp_path: Path
) -> None:
    """A local run must not leave the published heartbeat in the working tree.

    ``STATUS.md`` is tracked, and the scheduled workflow commits whatever it
    finds there. Before this was reachable from the command line, running the
    job locally to refresh a cache rewrote the heartbeat with the local run's
    outcome, and the next CI run published it as though it were the loop's.
    """
    state_path, status_path = _pinned(monkeypatch, tmp_path)
    tracked = tmp_path / "tracked-STATUS.md"
    tracked.write_text("the published heartbeat\n", encoding="utf-8")
    monkeypatch.setattr(update, "STATUS_PATH", tracked)

    asyncio.run(
        update.run(
            _Upstream(), since=datetime(2026, 8, 1, tzinfo=UTC),
            state_path=state_path, status_path=status_path, code=False,
        )
    )

    assert status_path.is_file()
    assert tracked.read_text(encoding="utf-8") == "the published heartbeat\n"


def test_a_never_run_record_loop_adds_nothing_to_the_heartbeat() -> None:
    """The page must render exactly as before until the Record loop first runs.

    A heading promising a heartbeat that does not exist yet is worse than no
    heading, and the prose that points at the table has to disappear with it or
    it refers to something that is not on the page.
    """
    page = render_status(State(last_success=datetime(2026, 8, 15, tzinfo=UTC)),
                         record=update.RecordState())

    assert "## Congressional Record" not in page
    assert "second loop" not in page


def test_the_record_row_is_rendered_by_the_bills_loop() -> None:
    """Neither loop can report its own death, so each renders the other's.

    This is the whole reason the Record heartbeat lives on the page the bills
    loop rewrites daily rather than in a file of its own.
    """
    fresh = update.RecordState(
        last_success=datetime.now(UTC), last_run=datetime.now(UTC),
        last_outcome="ok", congress=119, days_built=2, days_present=346,
    )

    page = render_status(State(last_success=datetime.now(UTC)), record=fresh)

    assert "## Congressional Record" in page
    assert "| Congress | 119 |" in page
    assert "| Issue days held | 346 |" in page
    assert "**Heartbeat** | current" in page


def test_a_stopped_record_loop_goes_stale_on_its_own() -> None:
    """The inverted signal: nothing fires, the date simply stops moving."""
    stopped = datetime.now(UTC) - STALE_AFTER - timedelta(days=1)
    state = update.RecordState(
        last_success=stopped, last_run=stopped, last_outcome="ok", congress=119
    )

    assert state.stale_for is not None
    assert "**stale**" in render_status(State(), record=state)


def test_a_record_loop_that_never_succeeded_is_stale_not_exempt() -> None:
    """A loop that has never worked must not read as healthy."""
    assert update.RecordState().stale_for is not None


def test_the_record_watermark_round_trips(tmp_path: Path) -> None:
    """What is written must read back identically, or the row lies."""
    state = update.RecordState(
        last_success=datetime(2026, 8, 15, 22, 10, tzinfo=UTC),
        last_run=datetime(2026, 8, 15, 22, 10, tzinfo=UTC),
        last_outcome="ok", congress=119, days_built=2, days_present=346,
        refs_published=2,
    )
    path = update.save_record_state(state, tmp_path / "record.json")

    assert update.load_record_state(path) == state


def test_a_corrupt_record_watermark_reads_as_never_run(tmp_path: Path) -> None:
    """Damaged reads as stale, which is the direction that stays visible."""
    broken = tmp_path / "record.json"
    broken.write_text("{not json", encoding="utf-8")

    assert update.load_record_state(broken).last_success is None
    assert update.load_record_state(broken).stale_for is not None


def test_a_recess_reads_as_success_not_failure() -> None:
    """Congress does not sit every day, and that must not look like a fault.

    The staleness threshold watches whether the *job* ran, not whether new text
    appeared, so a run that adds nothing still moves the date.
    """
    page = render_status(
        State(last_success=datetime.now(UTC)),
        record=update.RecordState(
            last_success=datetime.now(UTC), last_run=datetime.now(UTC),
            last_outcome="ok", congress=119, days_built=0, days_present=346,
        ),
    )

    assert "ordinary result of a recess" in page
    assert "**stale**" not in page
