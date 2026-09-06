# Status

**Last successful update — 2026-09-06 09:08 UTC**

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
| Last run attempted | 2026-09-06 09:08 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-04 08:18 UTC |
| Measures govinfo reported modified | 307 |
| Branches rewritten | 44 |
| Rebuilt to the commit already published | 253 |
| Modified but still carrying no text | 10 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 44 | `hr-10249`, `hr-10251`, `hr-10253`, `hr-10255`, `hr-10256`, `hr-10257`, `hr-10258`, `hr-10259`, `hr-10260`, `hr-10261`, `hr-10262`, `hr-10263`, and 32 more |

## Congressional Record

**Last successful run — 2026-09-04 11:52 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-05 11:02 UTC |
| Outcome | HTTPStatusError: 500 for https://api.govinfo.gov/published/2024-12-04/2027-02-01?offsetMark=*&pageSize=1000&collection=CREC&api_key=BuRb8HtH8Sn3AoHVu2bnPUzz7ByW8ucYNf5JyyUh |
| Congress | 119 |
| Issue days added | 0 |
| Issue days held | 353 |
| Refs published | 0 |

No issue day was added on the last successful run. Congress does not
sit every day, and the Record is published only for the days it does,
so an unchanged shard is the ordinary result of a recess rather than a
sign the job failed — the date above would show that.

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
