# Status

**Last successful update — 2026-10-03 10:11 UTC**

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
| Last run attempted | 2026-10-03 10:11 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-10-02 09:51 UTC |
| Measures govinfo reported modified | 447 |
| Branches rewritten | 56 |
| Rebuilt to the commit already published | 295 |
| Modified but still carrying no text | 96 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 56 | `hr-10641`, `hr-10643`, `hr-10660`, `hr-10662`, `hr-2400`, `hr-5349`, `hr-7730`, `hres-1292`, `s-1055`, `s-1514`, `s-1564`, `s-2273`, and 44 more |

## Congressional Record

**Last successful run — 2026-10-02 13:20 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-10-02 13:20 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 2 |
| Issue days held | 370 |
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
