# Status

**Last successful update — 2026-08-20 05:26 UTC**

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
| Last run attempted | 2026-08-20 05:26 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-08-19 04:25 UTC |
| Measures govinfo reported modified | 139 |
| Branches rewritten | 29 |
| Rebuilt to the commit already published | 110 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 29 | `hr-10061`, `hr-10109`, `hr-10110`, `hr-10111`, `hr-10112`, `hr-10113`, `hr-10114`, `hr-10115`, `hr-10116`, `hr-10117`, `hr-7906`, `hr-7951`, and 17 more |

## Congressional Record

**Last successful run — 2026-08-19 07:29 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-08-19 07:29 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 347 |
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
