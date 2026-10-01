# Status

**Last successful update — 2026-10-01 11:19 UTC**

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
| Last run attempted | 2026-10-01 11:19 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-09-30 09:53 UTC |
| Measures govinfo reported modified | 298 |
| Branches rewritten | 0 |
| Rebuilt to the commit already published | 245 |
| Modified but still carrying no text | 53 |

No branch changed. 245 measures were rebuilt from freshly
fetched upstream records and came out as the commits already
published, so there was nothing to write. That is the ordinary
result of a day on which nothing moved, and is not the same as the
job having failed — the date above would show that.

## Congressional Record

**Last successful run — 2026-09-30 13:09 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-09-30 13:09 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 2 |
| Issue days held | 368 |
| Refs published | 2 |

## Needs a person

**1 thing a schedule cannot do — checked 2026-10-01 11:19 UTC**

| What | What to do |
|---|---|
| 5 US Code release point(s) published upstream and not built: `pl-119-103`, `pl-119-103`, `pl-119-108`, `pl-119-110`, `pl-119-111` | Run `uscongress seed-code` where `us-congress-code` already is; a release point is built against its predecessor |

This list is computed, not remembered. It is empty on an ordinary day,
and this section is absent when it is empty.

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
