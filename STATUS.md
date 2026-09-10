# Status

**Last successful update — 2026-09-10 09:22 UTC**

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
| Last run attempted | 2026-09-10 09:22 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-09 08:22 UTC |
| Measures govinfo reported modified | 218 |
| Branches rewritten | 29 |
| Rebuilt to the commit already published | 175 |
| Modified but still carrying no text | 14 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 29 | `hr-10304`, `hr-10305`, `hr-10306`, `hr-10311`, `hr-10317`, `hr-10320`, `hr-10321`, `hr-10322`, `hr-10323`, `hr-10325`, `hr-4795`, `hr-5267`, and 17 more |

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

**Last successful run — 2026-09-09 12:02 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-09 12:02 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 356 |
| Refs published | 2 |

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
