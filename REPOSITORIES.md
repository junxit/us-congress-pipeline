# Repository index

Every repository in this project, what it holds, and whether it exists yet,
and the plan the whole thing is being built to.

**Generated** 2026-08-16 16:11 UTC by `uv run uscongress index`. Do not edit by hand —
the source of truth is `src/uscongress/registry.py`.

## Roadmap

12 of 12 phases shipped. Phases 3, 4, 7, 8, 9 and 10 produce no repository
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
| 7 | **Experimental amendment execution**<br>What a bill would do to existing law, in `derived/amendments.md` on every branch, never authoritative. Read 1,155,101 amendatory instructions across the corpus and **carried out 214,139 of them (18.5%)** — the ones where the bill states both the text removed and the text inserted, so the result follows from the bill alone and can be checked against it. The other 940,962 are listed with the reason, most often that the bill names the law by structure — *strike subsection (k)* — and the words being changed are in the US Code rather than in the bill. **The rate is mostly a fact about the year:** an instruction can only be placed if GPO tagged the citation it names, and they did so in 64% of the 108th's documents against 5% of the 112th's, so the share carried out runs from 1.3% to 23.9% by Congress with no change in the reading of them. Nothing reads `us-congress-code`: that would divide the build, because the daily loop runs where no copy of it exists and would publish a weaker answer over the better one every day. Supersedes the ~49% figure this roadmap carried from seven bills, which is not reproducible and does not say what it counted; measured here, 78.6% of instructions carry a machine-readable reference, and carrying one was never the hard part. | **shipped** | — |
| 8 | **Roll-call votes**<br>How each member voted, on the commit for the version that was voted on. **Not from the Congress.gov API**, which the plan assumed for years and which cannot serve this corpus: its roll-call endpoint covers the 118th and 119th Congresses and the House alone, against twelve Congresses and both chambers. The votes come from the chambers — `clerk.house.gov` and `senate.gov` — which BILLSTATUS already links and neither of which is keyed, so this shipped without adding a credential. The House publishes bioguide IDs, the same ones sponsors carry; the Senate publishes only its own LIS IDs, and that asymmetry is stated rather than crosswalked. Produces no new repository: it adds to the measures already built. Commit messages are part of what a commit hashes, so filling them in rewrote every affected branch — but only 7,510 of 172,082 measures carry a recorded vote, so a full rebuild of all twelve shards moved 5,969 refs and left 160,248 branches byte-identical. 19,471 roll calls were fetched and **none was missing**; what is recorded as a gap instead is 1,949 votes taken after the last text version their measure ever published, which therefore sit on no commit — 128 of them in the 108th, where every voted measure with a branch has only its introduced text. | **shipped** | — |
| 9 | **Hand the daily loop its own credentials**<br>The schedule is live. The token GitHub injects into a workflow run is scoped to the repository running it — enough to commit the heartbeat, not enough to push the 31 data repositories — so the loop carries a `DATA_REPO_TOKEN` of its own: a fine-grained token with Contents: read/write on the `us-congress-*` repositories and nothing else, minted by hand because no API can create one. Proved by a real run rather than a green tick: 544 measures checked, 82 branches rebuilt and published, the watermark advanced and the heartbeat written. Tracked as a phase rather than a note because an unattended loop nobody turned on is the same silent failure as one that stopped. | **shipped** | — |
| 10 | **A members crosswalk, so Senate votes are joinable**<br>Phase 8 left the two chambers keyed differently, because the sources are: the House Clerk publishes bioguide IDs — the same ones sponsors and cosponsors carry — while the Senate publishes only its own LIS member IDs. Nothing was inferred at the time, which was right, but it leaves the first question anyone doing analysis asks — how one member voted across both chambers — answerable only by a join the reader has to build themselves. **Measured before being planned**: 246 distinct LIS IDs appear across all 4,932 Senate roll calls in the corpus and all 246 resolve to a bioguide ID in `unitedstates/congress-legislators` (CC0), with surname, state and party agreeing independently for 244 — the two exceptions being a diacritic and a name change, the same people either way. The table is **vendored and pinned rather than fetched**: it is edited continuously upstream, and a live read would re-render every affected vote file the day someone corrects a spelling, breaking the unchanged-input-unchanged-bytes rule the daily loop rests on. 246 rows is also small enough to read in review, which no feed is. The added identifier is marked as a crosswalk rather than passed off as something the Senate published, and a row whose name, state and party do not agree across both sources is not used and is said so: a vote attributed to the wrong senator is worse than a vote with no identifier at all. That gate earned itself before it shipped, refusing a test fixture that paired S330 with Barrasso of Wyoming when S330 is Bennet of Colorado. Produces no new repository. Rewrote 534 refs — far fewer than phase 8's 5,969, because Senate roll calls concentrate on few measures: a vote-a-rama puts dozens of roll calls on one bill. House vote files were left byte-identical and no House-only branch moved, checked against the copies already on GitHub rather than against a fixture. | **shipped** | — |
| 11 | **Publish the Statute Compilations, so they stop living on one disk**<br>Phase 0 has snapshotted COMPS since the first day of the project, for a reason stated there and nowhere acted on: **govinfo replaces these packages in place and keeps no version archive**, so a superseded compilation is gone from the internet and a day without a snapshot is history that cannot be recovered. The snapshots then sat under `data/`, which is gitignored — 633 MB across 2,681 packages, with no copy anywhere else and nothing that would report their loss. The one irreplaceable thing here was the one thing not published. It was also the only job with no schedule, because there was nowhere for a scheduled run to put its output: a runner is destroyed minutes after it finishes. Publishing it fixes all three at once — an off-machine copy, something CI can check freshness against, and a schedule that finally has somewhere to write. **Named by compilation, not by hash.** The local store is content-addressed because it has to deduplicate 633 MB across snapshots; git already does that, so hash-named files would buy nothing and cost the only question these snapshots exist to answer — what changed in this compilation, and when. One commit per snapshot day, so a diff reads. Measured on the first three snapshots: 633 MB of XML packs to 84 MB, and the 2026-08-15 commit diffs as three new compilations and seven amended ones. A day on which nothing changed still commits, because *checked and identical* has to be distinguishable from *never checked* in a repository whose whole purpose is to be the surviving record. | **shipped** | `us-congress-comps` |

Phases 5 and 6 are independent of everything above and of each other, so
they can be reordered or run in parallel now that the daily loop is
standing. See [`STATUS.md`](STATUS.md) for whether it last ran.

## The repositories

All names are prefixed `us-congress-`. Every repository here is public; the
federal text they carry is public domain under 17 U.S.C. § 105, and each
one states its own terms in a `LICENSE` file.

| Repository | Phase | Contents | Status |
|---|---|---|---|
| [`us-congress-pipeline`](https://github.com/junxit/us-congress-pipeline) ← you are here | 0 | The ETL itself. Generates every repository below. | live, **public**, pushed 2026-08-16 |
| [`us-congress-code`](https://github.com/junxit/us-congress-code) | 1 | The codified US Code. One commit per OLRC release point, tagged, with per-law attribution from Table III. | live, **public**, pushed 2026-08-16 |
| `us-congress-bills-{congress}` | 2 | One branch per measure; one commit per bill text version. | 12 shards live, **public** |
| [`us-congress-statutes`](https://github.com/junxit/us-congress-statutes) | 5 | Statutes at Large — session laws as enacted, volumes 1–137. | live, **public**, pushed 2026-08-16 |
| `us-congress-record-{congress}` | 6 | Congressional Record floor proceedings as text, 1994 to present, sharded by Congress and linked to bills by metadata. | 17 shards live, **public** |
| [`us-congress-comps`](https://github.com/junxit/us-congress-comps) | 11 | Statute Compilations — non-codified law as amended, snapshotted daily because govinfo overwrites it in place and keeps no archive. | live, **public**, pushed 2026-08-16 |

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
| `us-congress-comps` | govinfo COMPS |

## Sharding

- `us-congress-bills-{congress}` — one repo per Congress, from the 108th
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
