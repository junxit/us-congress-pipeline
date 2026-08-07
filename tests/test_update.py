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
    """
    state = State(
        last_success=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_run=datetime(2026, 8, 7, 4, 0, tzinfo=UTC),
        last_outcome="ok",
        measures={"119": ["hr-7283", "s-1"]},
    )
    text = render_status(state, _result())

    assert text.startswith("# Status\n")
    assert "**Last successful update — 2026-08-07 04:00 UTC**" in text
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

    ``main`` carries the README, the licence and GAPS.md, and the measure
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
