# Status

**Last successful update — 2026-11-18 13:38 UTC**

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
| Last run attempted | 2026-11-18 13:38 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-11-17 13:04 UTC |
| Measures govinfo reported modified | 470 |
| Branches rewritten | 22 |
| Rebuilt to the commit already published | 442 |
| Modified but still carrying no text | 6 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 22 | `hconres-86`, `hconres-93`, `hr-10784`, `hr-4464`, `hr-4930`, `hr-5525`, `hr-665`, `hr-8552`, `hr-8801`, `hr-9607`, `hr-9608`, `s-3296`, and 10 more |

## Congressional Record

**Last successful run — 2026-11-17 16:33 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-11-17 16:33 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 387 |
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
