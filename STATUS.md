# Status

**Last successful update — 2026-10-10 13:45 UTC**

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
| Last run attempted | 2026-10-10 13:45 UTC |
| Outcome | ok |
| Window asked of govinfo | since 2026-10-09 10:38 UTC |
| Measures govinfo reported modified | 284 |
| Branches rewritten | 48 |
| Rebuilt to the commit already published | 210 |
| Modified but still carrying no text | 26 |

## Measures updated on the last successful run

| Congress | Branches | Measures |
|---|---|---|
| 119 | 48 | `hconres-124`, `hconres-22`, `hr-10329`, `hr-10352`, `hr-10353`, `hr-10355`, `hr-10371`, `hr-10375`, `hr-10751`, `hr-10752`, `hr-10756`, `hr-10758`, and 36 more |

## Congressional Record

**Last successful run — 2026-10-10 13:43 UTC**

| | |
|---|---|
| **Heartbeat** | current |
| Last run attempted | 2026-10-10 13:43 UTC |
| Outcome | ok |
| Congress | 119 |
| Issue days added | 1 |
| Issue days held | 373 |
| Refs published | 2 |

## Needs a person

**1 thing a schedule cannot do — checked 2026-10-10 13:45 UTC**

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
