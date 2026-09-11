# Status

**Last successful update — 2026-09-11 09:20 UTC**

This file is written by `uv run uscongress update`, which runs daily. It is
here because the way a project like this dies is not with an error: a
disabled schedule or an expired token raises nothing and notifies nobody.
So the signal is inverted. Nothing has to fire for you to notice a problem —
this date simply stops moving, and a stale date is visible to anyone who
reads this page.

If that date is more than 2 days old, the loop has stopped.

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-11 09:20 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-10 08:22 UTC |
| Measures govinfo reported modified | 324 |
| Branches rewritten | 29 |
| Rebuilt to the commit already published | 258 |
| Modified but still carrying no text | 37 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 29 | `hr-10009`, `hr-10099`, `hr-10307`, `hr-10308`, `hr-10309`, `hr-10310`, `hr-10312`, `hr-10313`, `hr-10314`, `hr-10315`, `hr-10316`, `hr-10318`, and 17 more |

## US Code release points not built yet

OLRC has published 1 release point(s) that `us-congress-code` does not carry:

- `pl-119-103`

A release point is a full snapshot of ~60,000 files built against the
one before it — the guard that stops a truncated archive recording
hundreds of repeals and reversing them two commits later compares the
two trees — so it is built where that history already is, with
`uv run uscongress seed-code`, rather than by the daily job. This
backlog is stated rather than left to be noticed.

## Congressional Record

**Last successful run — 2026-09-10 11:57 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-10 11:57 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 0 |
| Issue days held | 356 |
| Refs published | 0 |

No issue day was added on the last successful run. Congress does not
sit every day, and the Record is published only for the days it does,
so an unchanged shard is the ordinary result of a recess rather than a
sign the job failed — the date above would show that.

## Needs a person

**1 thing a schedule cannot do — checked 2026-09-11 09:20 UTC**

| What | What to do |
|---|---|
| 1 US Code release point(s) published upstream and not built: `pl-119-103` | Run `uscongress seed-code` where `us-congress-code` already is; a release point is built against its predecessor |

This list is computed, not remembered. It is empty on an ordinary day,
and this section is absent when it is empty.

## What this does not cover

Only measures govinfo reports as modified are rebuilt, and only the
repositories this project has already built. A Congress that has finished
legislating never changes again, so the shards below the current one are
expected to sit still; see [`REPOSITORIES.md`](REPOSITORIES.md) for what
exists.

The Congressional Record is built by a second loop, on its own
schedule; its heartbeat is the table above. Neither loop can report
the other's death, which is why both are rendered on this page.

Generated — do not edit by hand.
