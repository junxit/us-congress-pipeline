# Repository index

Every repository in this project, what it holds, and whether it exists yet.

**Generated** 2026-08-07 01:17 UTC by `uv run uscongress index`. Do not edit by hand —
the source of truth is `src/uscongress/registry.py`.

All names are prefixed `us-congress-`. Every repository here is public; the
federal text they carry is public domain under 17 U.S.C. § 105, and each
one states its own terms in a `LICENSE` file.

| Repository | Phase | Contents | Status |
|---|---|---|---|
| [`us-congress-pipeline`](https://github.com/junxit/us-congress-pipeline) ← you are here | 0 | The ETL itself. Generates every repository below. | live, **public**, pushed 2026-08-07 |
| [`us-congress-code`](https://github.com/junxit/us-congress-code) | 1 | The codified US Code. One commit per OLRC release point, tagged, with per-law attribution from Table III. | live, **public**, pushed 2026-08-06 |
| `us-congress-bills-{congress}` | 2 | One branch per measure; one commit per bill text version. | 12 shards live, **public** |
| `us-congress-statutes` | 5 | Statutes at Large — session laws as enacted, volumes 1–137. | not created yet |
| `us-congress-record-{congress}` | 6 | Congressional Record floor proceedings, 1873 to present, linked to bills by metadata. | planned (sharded) |

### The sharded repositories

A family row above names a template, not a repository. These are the
repositories it actually stands for.

**`us-congress-bills-{congress}`** — [`108`](https://github.com/junxit/us-congress-bills-108), [`109`](https://github.com/junxit/us-congress-bills-109), [`110`](https://github.com/junxit/us-congress-bills-110), [`111`](https://github.com/junxit/us-congress-bills-111), [`112`](https://github.com/junxit/us-congress-bills-112), [`113`](https://github.com/junxit/us-congress-bills-113), [`114`](https://github.com/junxit/us-congress-bills-114), [`115`](https://github.com/junxit/us-congress-bills-115), [`116`](https://github.com/junxit/us-congress-bills-116), [`117`](https://github.com/junxit/us-congress-bills-117), [`118`](https://github.com/junxit/us-congress-bills-118), [`119`](https://github.com/junxit/us-congress-bills-119)


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

Sharding by Congress is a deliberate choice, not a capacity limit. The
twelve Congresses from the 108th hold 171,881 measures in 6.60 GB of XML,
which packs to roughly 1-2 GB; one repository could carry all of it, and
GitHub publishes no branch-count ceiling.

It is sharded because a finished Congress never changes again, so frozen
shards never rebuild and their clones stay valid; because a defect in
recent data should not force a rebuild of 2003; because reading the 118th
should not mean downloading 6.6 GB; and because twelve repositories build
in parallel where one serialises. Branch counts run 10,637 to 19,315 per
Congress.

## What the diffs mean

Diffing across a bill branch's own commits -- `hr-1234~2..hr-1234`, or
against the commit that introduced it -- shows how the **bill** changed,
not how the **US Code** would change. Bills are written as amendatory
instructions, not diffs, and executing them automatically is unsolved.
Synthesised effects on existing law are always marked derived.
