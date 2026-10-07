# Status

**Last successful update — 2026-10-07 11:29 UTC**

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
| Last run attempted | 2026-10-07 11:29 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-10-06 10:44 UTC |
| Measures govinfo reported modified | 317 |
| Branches rewritten | 53 |
| Rebuilt to the commit already published | 220 |
| Modified but still carrying no text | 44 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 53 | `hconres-123`, `hr-10642`, `hr-10700`, `hr-10701`, `hr-10702`, `hr-10703`, `hr-10705`, `hr-10706`, `hr-10707`, `hr-10708`, `hr-10709`, `hr-10710`, and 41 more |

## Congressional Record

**Last successful run — 2026-10-06 13:45 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-10-06 13:45 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 371 |
| Refs published | 2 |

## Needs a person

**1 thing a schedule cannot do — checked 2026-10-07 11:29 UTC**

| What | What to do |
|---|---|
| govinfo carries 138 Statutes volumes against 135 tagged here | Run `uscongress seed-statutes`, then `uscongress republish --repo us-congress-statutes` |

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
