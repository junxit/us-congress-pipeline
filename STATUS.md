# Status

**Last successful update — 2026-11-21 13:13 UTC**

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
| Last run attempted | 2026-11-21 13:13 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-11-19 13:06 UTC |
| Measures govinfo reported modified | 797 |
| Branches rewritten | 57 |
| Rebuilt to the commit already published | 611 |
| Modified but still carrying no text | 129 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 57 | `hr-10159`, `hr-10167`, `hr-11037`, `hr-11058`, `hr-11067`, `hr-11130`, `hr-1520`, `hr-2347`, `hr-4464`, `hr-4624`, `hr-6213`, `hres-1671`, and 45 more |

## Congressional Record

**Last successful run — 2026-11-20 15:49 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-11-20 15:49 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 390 |
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
