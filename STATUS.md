# Status

**Last successful update — 2026-10-17 09:00 UTC**

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
| Last run attempted | 2026-10-17 09:00 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-10-15 08:35 UTC |
| Measures govinfo reported modified | 483 |
| Branches rewritten | 23 |
| Rebuilt to the commit already published | 436 |
| Modified but still carrying no text | 24 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 23 | `hr-10810`, `hr-10812`, `hr-10813`, `hr-10815`, `hr-10816`, `hr-10817`, `hr-10818`, `hr-10820`, `hr-10821`, `hr-10823`, `hr-10824`, `hr-10827`, and 11 more |

## Congressional Record

**Last successful run — 2026-10-16 12:00 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-10-16 12:00 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 375 |
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
