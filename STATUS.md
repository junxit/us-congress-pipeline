# Status

**Last successful update — 2026-09-03 18:38 UTC**

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
| Last run attempted | 2026-09-03 18:38 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-03 08:25 UTC |
| Measures govinfo reported modified | 262 |
| Branches rewritten | 32 |
| Rebuilt to the commit already published | 229 |
| Modified but still carrying no text | 1 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 32 | `hconres-115`, `hr-10220`, `hr-10221`, `hr-10222`, `hr-10223`, `hr-10224`, `hr-10225`, `hr-10227`, `hr-10228`, `hr-10229`, `hr-10230`, `hr-10231`, and 20 more |

## Congressional Record

**Last successful run — 2026-09-03 11:49 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-03 11:49 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 2 |
| Issue days held | 353 |
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
