# Status

**Last successful update — 2026-10-22 09:49 UTC**

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
| Last run attempted | 2026-10-23 17:47 UTC |
| Outcome | checking release points: HTTPStatusError: 500 for https://uscode.house.gov/download/priorreleasepoints.htm |
| Window asked of govinfo | since 2026-10-22 08:49 UTC |
| Measures govinfo reported modified | 336 |
| Branches rewritten | 23 |
| Rebuilt to the commit already published | 279 |
| Modified but still carrying no text | 34 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 11 | `hr-1721`, `hr-227`, `hr-2388`, `hr-2481`, `hr-3620`, `hr-4307`, `hr-4467`, `hr-6380`, `hr-7831`, `hr-837`, `s-5682` |

## Congressional Record

**Last successful run — 2026-10-23 14:14 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-10-23 14:14 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 377 |
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
