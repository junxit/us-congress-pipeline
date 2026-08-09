# Repository index

Every repository in this project, what it holds, and whether it exists yet,
and the plan the whole thing is being built to.

**Generated** 2026-08-09 19:07 UTC by `uv run uscongress index`. Do not edit by hand —
the source of truth is `src/uscongress/registry.py`.

## Roadmap

9 of 10 phases shipped. Phases 3, 4, 7, 8 and 9 produce no repository
of their own — they add to repositories built earlier — which is why the
repository table below skips those numbers.

| Phase | Work | State | Produces |
|---|---|---|---|
| 0 | **Scaffold, and snapshot the Statute Compilations**<br>The ETL itself, and the one genuinely time-sensitive job in the project: govinfo replaces Statute Compilations in place and keeps no version archive, so every day without a snapshot is history that cannot be recovered. | **shipped** | `us-congress-pipeline` |
| 1 | **The codified US Code**<br>383 distinct OLRC release points, each a commit and a tag, with per-law attribution from Table III. | **shipped** | `us-congress-code` |
| 2 | **Bills of the current Congress**<br>Every measure of the 119th as a branch, one commit per text version. | **shipped** | `us-congress-bills-{congress}` |
| 3 | **The daily loop, and a heartbeat that goes stale on its own**<br>Rebuild whatever govinfo reports as changed, and publish the date it last ran. Ordered before the corpus expanded, not after: bot rot is what killed every predecessor, and a stopped job raises no error — it simply stops, which is why the signal has to be a date going stale rather than an alert having to fire. | **shipped** | — |
| 4 | **Backfill the 118th through the 108th**<br>The remaining eleven Congresses, ~160,000 further branches. Produces no new repository: it fills out the family phase 2 created. | **shipped** | — |
| 5 | **Statutes at Large**<br>Session laws as enacted, 135 volumes and 101,975 laws, one commit per volume. Volumes 7 and 8 get none: they hold only Indian and foreign treaties, which are ratification rather than passage and presentment. Independent of everything above and of phase 6. | **shipped** | `us-congress-statutes` |
| 6 | **The Congressional Record**<br>Floor proceedings, 17 shards from the 103rd to the 119th: 9,382 issue days and 1,330,322 documents, one commit per issue day, with the daily and bound editions on separate branches. **The machine-readable Record begins in 1994, not 1873**: of 2,420 bound-edition packages, the 2,083 covering 1873–1998 are scanned page images whose granules offer a PDF and no `txtLink` at all, so that century is unbuildable rather than merely unbuilt — measured against `GPO-CRECB-1947-pt1` and `GPO-CRECB-1970-pt2`. The bound edition also stops at 2018, which is why the 116th onward carry a `daily` branch and no `bound`. | **shipped** | `us-congress-record-{congress}` |
| 7 | **Experimental amendment execution**<br>What a bill would do to existing law, under `derived/` and never authoritative. Measured across seven real bills only ~49% of amendatory instructions carry a machine-readable US Code reference, and a large bill would need ~99.99% per-instruction accuracy to come out wholly correct, so the output is marked derived and unapplied instructions are stated rather than guessed at. | planned | — |
| 8 | **Roll-call votes**<br>How each member voted, on the commit for the version that was voted on. **Not from the Congress.gov API**, which the plan assumed for years and which cannot serve this corpus: its roll-call endpoint covers the 118th and 119th Congresses and the House alone, against twelve Congresses and both chambers. The votes come from the chambers — `clerk.house.gov` and `senate.gov` — which BILLSTATUS already links and neither of which is keyed, so this shipped without adding a credential. The House publishes bioguide IDs, the same ones sponsors carry; the Senate publishes only its own LIS IDs, and that asymmetry is stated rather than crosswalked. Produces no new repository: it adds to the measures already built. Commit messages are part of what a commit hashes, so filling them in rewrote every affected branch — but only 7,510 of 172,082 measures carry a recorded vote, so a full rebuild of all twelve shards moved 5,969 refs and left 160,248 branches byte-identical. 19,471 roll calls were fetched and **none was missing**; what is recorded as a gap instead is 1,949 votes taken after the last text version their measure ever published, which therefore sit on no commit — 128 of them in the 108th, where every voted measure with a branch has only its introduced text. | **shipped** | — |
| 9 | **Hand the daily loop its own credentials**<br>The schedule is live. The token GitHub injects into a workflow run is scoped to the repository running it — enough to commit the heartbeat, not enough to push the 30 data repositories — so the loop carries a `DATA_REPO_TOKEN` of its own: a fine-grained token with Contents: read/write on the `us-congress-*` repositories and nothing else, minted by hand because no API can create one. Proved by a real run rather than a green tick: 544 measures checked, 82 branches rebuilt and published, the watermark advanced and the heartbeat written. Tracked as a phase rather than a note because an unattended loop nobody turned on is the same silent failure as one that stopped. | **shipped** | — |

Phases 5 and 6 are independent of everything above and of each other, so
they can be reordered or run in parallel now that the daily loop is
standing. See [`STATUS.md`](STATUS.md) for whether it last ran.

## The repositories

All names are prefixed `us-congress-`. Every repository here is public; the
federal text they carry is public domain under 17 U.S.C. § 105, and each
one states its own terms in a `LICENSE` file.

| Repository | Phase | Contents | Status |
|---|---|---|---|
| [`us-congress-pipeline`](https://github.com/junxit/us-congress-pipeline) ← you are here | 0 | The ETL itself. Generates every repository below. | live, **public**, pushed 2026-08-09 |
| [`us-congress-code`](https://github.com/junxit/us-congress-code) | 1 | The codified US Code. One commit per OLRC release point, tagged, with per-law attribution from Table III. | live, **public**, pushed 2026-08-09 |
| `us-congress-bills-{congress}` | 2 | One branch per measure; one commit per bill text version. | 12 shards live, **public** |
| [`us-congress-statutes`](https://github.com/junxit/us-congress-statutes) | 5 | Statutes at Large — session laws as enacted, volumes 1–137. | live, **public**, pushed 2026-08-09 |
| `us-congress-record-{congress}` | 6 | Congressional Record floor proceedings as text, 1994 to present, sharded by Congress and linked to bills by metadata. | 17 shards live, **public** |

### The sharded repositories

A family row above names a template, not a repository. These are the
repositories it actually stands for.

**`us-congress-bills-{congress}`** — [`108`](https://github.com/junxit/us-congress-bills-108), [`109`](https://github.com/junxit/us-congress-bills-109), [`110`](https://github.com/junxit/us-congress-bills-110), [`111`](https://github.com/junxit/us-congress-bills-111), [`112`](https://github.com/junxit/us-congress-bills-112), [`113`](https://github.com/junxit/us-congress-bills-113), [`114`](https://github.com/junxit/us-congress-bills-114), [`115`](https://github.com/junxit/us-congress-bills-115), [`116`](https://github.com/junxit/us-congress-bills-116), [`117`](https://github.com/junxit/us-congress-bills-117), [`118`](https://github.com/junxit/us-congress-bills-118), [`119`](https://github.com/junxit/us-congress-bills-119)

**`us-congress-record-{congress}`** — [`103`](https://github.com/junxit/us-congress-record-103), [`104`](https://github.com/junxit/us-congress-record-104), [`105`](https://github.com/junxit/us-congress-record-105), [`106`](https://github.com/junxit/us-congress-record-106), [`107`](https://github.com/junxit/us-congress-record-107), [`108`](https://github.com/junxit/us-congress-record-108), [`109`](https://github.com/junxit/us-congress-record-109), [`110`](https://github.com/junxit/us-congress-record-110), [`111`](https://github.com/junxit/us-congress-record-111), [`112`](https://github.com/junxit/us-congress-record-112), [`113`](https://github.com/junxit/us-congress-record-113), [`114`](https://github.com/junxit/us-congress-record-114), [`115`](https://github.com/junxit/us-congress-record-115), [`116`](https://github.com/junxit/us-congress-record-116), [`117`](https://github.com/junxit/us-congress-record-117), [`118`](https://github.com/junxit/us-congress-record-118), [`119`](https://github.com/junxit/us-congress-record-119)


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
in parallel where one serializes. Branch counts run 10,637 to 19,315 per
Congress.

## What the diffs mean

Diffing across a bill branch's own commits -- `hr-1234~2..hr-1234`, or
against the commit that introduced it -- shows how the **bill** changed,
not how the **US Code** would change. Bills are written as amendatory
instructions, not diffs, and executing them automatically is unsolved.
Synthesised effects on existing law are always marked derived.
