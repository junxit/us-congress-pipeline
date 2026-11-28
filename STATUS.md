# Status

**Last successful update — 2026-11-28 13:51 UTC**

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
| Last run attempted | 2026-11-28 13:51 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-11-27 13:39 UTC |
| Measures govinfo reported modified | 28 |
| Branches rewritten | 8 |
| Rebuilt to the commit already published | 20 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 8 | `hr-11122`, `hr-11123`, `hr-11124`, `hr-11125`, `hr-11126`, `hr-11127`, `hr-11165`, `hres-1675` |

## Congressional Record

**Last successful run — 2026-11-27 17:03 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-11-27 17:03 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 0 |
| Issue days held | 392 |
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
