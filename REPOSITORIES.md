# Repository index

Every repository in this project, what it holds, and whether it exists yet.

**Generated** 2026-08-04 15:59 UTC by `uv run uscongress index`. Do not edit by hand —
the source of truth is `src/uscongress/registry.py`.

All names are prefixed `us-congress-`. All repositories are private for now;
publishing is a one-way door and the decision is deliberately deferred.

| Repository | Phase | Contents | Status |
|---|---|---|---|
| [`us-congress-pipeline`](https://github.com/junxit/us-congress-pipeline) ← you are here | 0 | The ETL itself. Generates every repository below. | live, private, pushed 2026-08-03 |
| [`us-congress-code`](https://github.com/junxit/us-congress-code) | 1 | The codified US Code. One commit per OLRC release point, tagged, with per-law attribution from Table III. | live, private, pushed 2026-08-04 |
| `us-congress-bills-{congress}` | 2 | One branch per measure; one commit per bill text version. | planned (sharded) |
| [`us-congress-statutes`](https://github.com/junxit/us-congress-statutes) | 5 | Statutes at Large — session laws as enacted, volumes 1–137. | not created yet |
| `us-congress-record-{congress}` | 6 | Congressional Record floor proceedings, 1873 to present, linked to bills by metadata. | planned (sharded) |

## Sources

| Repository | Built from |
|---|---|
| `us-congress-code` | uscode.house.gov release points (USLM 1.0 XML) |
| `us-congress-bills-{congress}` | govinfo BILLS + BILLSTATUS |
| `us-congress-statutes` | govinfo STATUTE (USLM 2.0 XML) |
| `us-congress-record-{congress}` | govinfo CREC + CRECB |

## Sharding

- `us-congress-bills-{congress}` — one repo per Congress, 108 through 119
- `us-congress-record-{congress}` — one repo per Congress

Sharding by Congress is forced by arithmetic: roughly 180,000 measures
since 2003, against GitHub's recommended ceiling of 5,000 branches per
repository.

## What the diffs mean

Diffing a bill branch against its base tag shows how the **bill** changed,
not how the **US Code** would change. Bills are written as amendatory
instructions, not diffs, and executing them automatically is unsolved.
Synthesised effects on existing law are always marked derived.
