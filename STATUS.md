# Status

**Last successful update — 2026-09-27 10:21 UTC**

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
| Last run attempted | 2026-09-27 10:21 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-26 08:41 UTC |
| Measures govinfo reported modified | 65 |
| Branches rewritten | 9 |
| Rebuilt to the commit already published | 49 |
| Modified but still carrying no text | 7 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 9 | `hr-2400`, `s-240`, `s-283`, `s-3257`, `s-3258`, `sres-902`, `sres-903`, `sres-904`, `sres-905` |

## US Code release points not built yet

OLRC has published 5 release point(s) that `us-congress-code` does not carry:

- `pl-119-103`
- `pl-119-103`
- `pl-119-108`
- `pl-119-110`
- `pl-119-111`

A release point is a full snapshot of ~60,000 files built against the
one before it — the guard that stops a truncated archive recording
hundreds of repeals and reversing them two commits later compares the
two trees — so it is built where that history already is, with
`uv run uscongress seed-code`, rather than by the daily job. This
backlog is stated rather than left to be noticed.

## Congressional Record

**Last successful run — 2026-09-26 11:56 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-26 11:56 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 366 |
| Refs published | 2 |

## Needs a person

**1 thing a schedule cannot do — checked 2026-09-27 10:21 UTC**

| What | What to do |
|---|---|
| 5 US Code release point(s) published upstream and not built: `pl-119-103`, `pl-119-103`, `pl-119-108`, `pl-119-110`, `pl-119-111` | Run `uscongress seed-code` where `us-congress-code` already is; a release point is built against its predecessor |

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
