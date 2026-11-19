# Status

**Last successful update — 2026-11-19 14:06 UTC**

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
| Last run attempted | 2026-11-19 14:06 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-11-18 12:38 UTC |
| Measures govinfo reported modified | 415 |
| Branches rewritten | 74 |
| Rebuilt to the commit already published | 269 |
| Modified but still carrying no text | 72 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 74 | `hjres-222`, `hr-10136`, `hr-10226`, `hr-10382`, `hr-10985`, `hr-10992`, `hr-11000`, `hr-11007`, `hr-11038`, `hr-11039`, `hr-11040`, `hr-11041`, and 62 more |

## Congressional Record

**Last successful run — 2026-11-18 16:02 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-11-18 16:02 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 388 |
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
