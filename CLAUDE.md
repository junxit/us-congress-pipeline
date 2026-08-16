# CLAUDE.md

Orientation for anyone — human or agent — picking this repository up cold.

## What this is

An ETL that mirrors the workings of the US Congress as git repositories. This
repository is **only the pipeline**; it *generates* 32 data repositories and
contains none of them.

It owns **33 public repositories** under `github.com/junxit`:

| Repository | Count | Holds |
|---|---|---|
| `us-congress-pipeline` | 1 | this ETL |
| `us-congress-code` | 1 | the US Code, 383 release points, tagged |
| `us-congress-bills-{108..119}` | 12 | 160,190 branches, one per measure |
| `us-congress-statutes` | 1 | 135 volumes, 101,975 session laws |
| `us-congress-comps` | 1 | Statute Compilations, one commit per daily snapshot |
| `us-congress-record-{103..119}` | 17 | 9,382 issue days, 1,330,322 documents |

## Starting from a fresh clone

`data/` is gitignored and about **93 GB** here — 82 GB of cached upstream XML and
10 GB of built repositories. A fresh clone has none of it.

**Do not rebuild the data repositories from upstream to get started.** They
already exist on GitHub and cloning them is enormously cheaper than the days of
crawling a rebuild would cost:

```bash
uv sync
cp .env.example .env          # add your govinfo key
uv run uscongress bootstrap   # clone the 32 data repos into data/repos/
```

The cached XML under `data/raw/` does not need restoring at all. Every job
refetches what it needs and caches it again; the cache is an optimization, not
state. Rebuilding a *repository* from scratch is only necessary when a rendering
defect has to be corrected in commits that already exist — see `seed-bills
--rebuild`.

## Rewriting the corpus, and publishing it

Anything that changes what a commit *contains* — a message trailer, a file in
the tree — changes every SHA on every branch it touches, and the corpus is
already published. This is the procedure, in this order. It has been run three
times; the ordering is not arbitrary and getting it wrong is silent.

```bash
uv run uscongress update --since <date> --no-code \
    --state-path /tmp/x.json --status-path /tmp/x.md   # 1. refresh the cache
uv run uscongress seed-bills --congress N --rebuild     # 2. per shard, ~7-20 min
uv run uscongress artifacts                             # 3. before publishing
uv run uscongress republish --dry-run                   # 4. size it
uv run uscongress republish                             # 5. push what moved
uv run uscongress index && uv run uscongress describe   # 6. then the phase state
```

- **Push the pipeline code before the corpus, never after.** New code against an
  old corpus is safe: the daily loop rewrites the measures it touches in the new
  format, a progressive rollout. Old code against a new corpus is a regression —
  the loop rebuilds in the *old* format and force-pushes it back over the
  rewrite, and nothing reports an error because pushing rebuilt measures is
  exactly that job's purpose.
- **Do not disable the schedule for the rewrite.** A disabled schedule is the
  silent failure this project exists to prevent, and GitHub auto-disables a
  scheduled workflow after 60 days of repository inactivity, so pausing is the
  worse risk. The invariant above makes it unnecessary.
- **Step 1 is not optional.** The rebuild renders from `data/raw/`, so any
  measure CI has rebuilt since this machine last fetched it would be
  force-pushed backwards.
- **Step 3 before step 5.** `artifacts` writes `main`; running it afterwards
  means a second `republish` for twelve refs.
- **`--status-path` is load-bearing.** Without it a local `update` overwrites the
  tracked `STATUS.md`, and the next scheduled run commits it as though it were
  the loop's own heartbeat.
- Measured: a full 12-shard rebuild is ~2.5 hours; `republish` moved 534 refs in
  minutes, 5,969 in ~15, and 90,277 in ~50. Every ref landed first attempt.

`republish` computes what to push by comparing local refs against the remote, so
it is safe to re-run and pushes nothing when nothing moved.

## Two loops, one page

There are three scheduled jobs, and they are separate on purpose.

| Workflow | When | Runs | Writes |
|---|---|---|---|
| `update.yml` | 05:00 UTC daily | `attention --announce`, then `update --publish` — bills, and reports US Code release points | `STATUS.md`, `state/update.json`, `state/attention.json` |
| `record.yml` | 07:00 UTC daily | `update-record --publish` — the current Congress's Record shard | `state/record.json` |
| `rebuild.yml` | 03:00 UTC monthly | `seed-bills --rebuild` then `republish` — the only thing that can refresh `GAPS.md` honestly | `state/ci-rebuild.txt` |

`attention` runs **before** `update`, not after, because `update` is what renders
`STATUS.md` and it renders that list from the file `attention` writes. Run it
afterwards and every reader sees yesterday's answer.

Each job also writes `state/ci-*.txt` in shell from a step that always runs.
That is not decoration: a failure early enough to skip the Python job — a broken
lockfile, a failing test — leaves nothing to commit, and GitHub disables a
scheduled workflow after 60 days without repository activity. Without those
files the mechanism that keeps the schedules alive is the first thing a
persistent failure takes down.

Only `update.yml` renders `STATUS.md`, and it renders **both** heartbeats from
the two state files. That is what makes a stopped Record loop visible: the
bills loop redraws that row every morning on a page it is still writing, so the
Record date simply stops moving. Neither loop can report its own death, which
is why each is rendered by something other than itself. The Record row
therefore lags by up to a day, which the two-day staleness threshold absorbs.

**The Record loop is append-only and must stay that way.** It asks which issue
days a branch already holds and builds the rest; it never passes `--rebuild`.
The Record is one cumulative branch, so rewriting a day in the middle rewrites
every commit after it. See the trap below about restamping.

**`rebuild.yml` never runs `artifacts`.** That job builds its cross-reference
set from what is on disk, and a runner holds only the shard it just rebuilt —
running it there would strip every sibling link out of the README it writes.
The rewrite procedure above calls for `artifacts` because it assumes a machine
holding the whole corpus.

## Maintenance that needs a person

**Ask, do not remember: `uv run uscongress attention`.** It computes what a
schedule cannot do from live state — a missing shard for the sitting Congress, a
disabled workflow, a stale COMPS snapshot, a crosswalk older than the Congress
it describes, a release-point backlog, a date bound about to start rejecting
real data. It exits non-zero when anything is due, renders a **Needs a person**
section on `STATUS.md`, and the daily loop keeps one GitHub issue in step with
the list. On an ordinary day it prints `nothing needs a person` and the section
is absent.

A check that cannot answer a question reports that, rather than counting it as
answered no. An empty list means every question was asked.

Two things it deliberately cannot judge for you:

- **`data/scripts/build_members.py`** when a Congress seats new members. The
  check tells you the table predates the sitting Congress; it cannot tell you
  the diff is right. Read it, because a changed bioguide ID moves votes from one
  senator to another. Nothing fetches that table at build time and nothing
  should — see the module docstring.
- **A vote that cannot be fetched has never happened.** All 19,471 roll calls
  were retrievable, so the marker path and the matching `GAPS.md` section have
  only ever run in tests. Their first real execution will be unattended.

## Where the truth lives

Never duplicate these anywhere — memory, notes, or prose. They are the source.

| Question | Answer |
|---|---|
| What phase is the project in? | `src/uscongress/registry.py` → `PHASES` |
| Which repositories exist, and are they live? | `uv run uscongress index` → `REPOSITORIES.md` |
| Is the daily loop still alive? | `STATUS.md`, or `uscongress update --check` |
| Is the Record loop still alive? | the Congressional Record table on `STATUS.md` |
| What needs a person right now? | `uv run uscongress attention`, or the **Needs a person** section on `STATUS.md` |
| Can the loop still publish where it must? | the same check — it asks `git-receive-pack`, per repository |
| What is missing from a corpus, and why? | `GAPS.md` on that repository's `main` |

Phase state is deliberately in-repo rather than in anyone's head. A phase is
marked `DONE` only when its repositories actually exist and are pushed — a
roadmap claiming *shipped* while the repository table says *not created yet* is
the contradiction the roadmap exists to prevent.

## How this project works

- **Every job is resumable and idempotent.** `seed-code` skips release points
  whose tag exists, `seed-bills` skips measures whose branch exists,
  `seed-record` skips issue days already committed. Re-running a finished job
  fetches nothing. Stopping a long job costs almost nothing.
- **Reconcile, and warn when buckets do not sum.** A build once reported 10,617
  branches for 10,637 measures and looked entirely successful; three separate
  bugs were behind the gap. Every job accounts for every input in exactly one
  bucket.
- **State gaps rather than hiding them.** `GAPS.md` exists because an
  unexplained absence of 8,755 measures reads as a build that quietly failed.
- **Prefer generated over hand-maintained.** `REPOSITORIES.md`, every generated
  repository's README, and the GitHub descriptions are all derived from
  `registry.py` so they cannot drift.
- **Verify against an independent count, not the job's own output.** Every real
  defect found here was caught by reconciling arithmetic, never by reading a
  job's summary.

## Traps that keep recurring

Each of these cost real time to find. They are documented at the code that
handles them; this is the index.

- **Soft 404s.** govinfo answers a missing document with its ordinary web page
  and **HTTP 200** — about 44 KB of HTML. Validate the payload before caching,
  or you cache a web page under an `.xml` name.
- **The `<?xml` guard is not enough.** 51 of 137 Statutes volumes carry a UTF-8
  BOM, which `bytes.lstrip()` does not strip; MODS documents carry no XML
  declaration at all. See `statutes._is_xml` and `record` for the two shapes.
- **`ls-remote` says nothing about whether you can push.** It speaks to
  `git-upload-pack`, the read service, which a public repository serves to
  anyone — so a credential with no write access reads a repository perfectly and
  then fails on the push. `us-congress-comps` was created, public and readable
  for an afternoon while `DATA_REPO_TOKEN` could not write to it, and nothing
  could say so until a scheduled run went red. The write service advertises refs
  at the same path under `?service=git-receive-pack`, and GitHub requires push
  permission to answer: 200 may push, 403 valid credential without write, 401
  unrecognised, 404 not on a fine-grained token's list. See `publish.can_push`.
- **govinfo restamps `lastModified` in bulk, with no content change.** On
  2026-08-12 it restamped nine already-published CREC days, two of them from
  2025: 1,469 documents before, 1,469 after, granule titles unchanged. Anything
  that decides what to rebuild from `lastModified` will see those and rebuild
  them. For bills that is merely wasted work the daily loop already reports as
  "rebuilt to the commit already published". For the **Record** it is far worse:
  one cumulative branch, so a day in the middle rewrites everything after it and
  force-pushes the whole history to produce byte-identical trees. Drive the
  Record by issue date, never by modification stamp — see `recordloop`.
- **Two hosts, two rate regimes.** `api.govinfo.gov` is limited to 36,000
  requests/hour per key; `www.govinfo.gov` — bulkdata, `/content/pkg/`,
  `/metadata/pkg/` — is unkeyed and unlimited. Over 99% of a Record crawl goes
  to the second. See `config.GOVINFO_RATE_PER_SEC`.
- **A vote's date comes from the chamber, never from BILLSTATUS.** BILLSTATUS
  stamps a vote as a UTC instant; the House Clerk and the Senate each date it
  locally. A vote taken after 7pm Eastern therefore belongs to a different day
  depending on which document is believed, and it decides which commit the vote
  lands on. 60 of the 814 distinct vote stamps in the 113th Congress fall in
  that window. See `votes.RecordedVote.when`.
- **`<recordedVotes>` is repeated on every action item that mentions it.** Roll
  129 appears twice on H.R. 588. Deduplicate on
  `(chamber, session, number)` — number alone collides across chambers and
  sessions. Same trap as `bills._committees`, reached another way.
- **Nothing infers a member identifier.** The House publishes bioguide IDs and
  the Senate publishes LIS IDs. The vendored table in `members.py` joins them,
  and refuses any row whose surname and state disagree with the vote document —
  a vote attributed to the wrong senator is wrong in a way that reads as
  authoritative. Party is deliberately not checked; senators change party.
- **`<amendment-instruction>` is not where amendatory instructions live.** It
  appears in 1 document in 400 and holds an engrossed *amendment's* instruction.
  Ordinary amendatory text is prose in `<text>`. See `amendments`.
- **A bare "is amended" is a heading, not an operation.** Congress names the
  target once and lists the operations beneath it, so counting the opening line
  as an instruction adds a bogus unapplied row per amendment.
- **A citation is inherited, and the walk up must stop at `<section>`.** A bill
  amends many sections of law; without the boundary an instruction inherits a
  different amendment's citation and reports it confidently.
- **How much of a bill can be executed is mostly a fact about the year.** GPO
  tagged machine-readable citations in 64% of the 108th Congress's documents and
  5% of the 112th's, so the share of instructions carried out runs from 1.3% to
  23.9% with no change in the reading. A low number is not a broken build; each
  `GAPS.md` says so beside its own.
- **Three volumes need an `Accept` header or they do not exist.** STATUTE 107,
  108 and 109 return HTTP 200 and 67 KB of error HTML without
  `Accept: application/xml`.
- **`fast-import`'s `deleteall` sets a commit's whole tree.** Anything writing
  `main` must `read_tree` first and merge, or it deletes `README.md`, `LICENSE`
  and `GAPS.md`. This has bitten twice, from both directions.
- **`read_tree` costs one `git show` per file.** Use `GitRepo.list_files` when
  you only need existence — `us-congress-code`'s `main` holds 60,493 files.
- **git refuses to fetch into a checked-out branch.** Park HEAD first; see
  `publish.PARKED_HEAD`.
- **GitHub rejects large ref pushes.** ~10,000 refs fails atomically *after*
  transferring everything; force-updates fail far lower. Batch at
  `publish.BATCH`, and read back what landed with `ls-remote` — a push that
  exits zero is not evidence.
- **Generated documents must not embed counts their own commit changes**, or
  regeneration churns forever.

## Conventions

- **`uv` always**, never pip. `uv run pytest`, `uv run uscongress …`.
- **Google-style docstrings** on every module, class and function.
- Comments explain **why**, with the concrete measurement that motivated them.
  Match the surrounding density, which is high.
- **US English** throughout: prose, comments, docstrings, identifiers, commit
  messages. Never alter the spelling of quoted federal text.
- Commits are `<type>: <summary>` with a body explaining why. **No agentic
  co-authors.** There is no `changelog.txt`.
- Before finishing: `uv run pytest`, `uscongress check-links`,
  `uscongress describe --check` — all must be clean.
- Long jobs go to the background with `nohup … &`; the shell tool caps at about
  two minutes.

## What the data is not

Diffing a bill branch shows how the **bill** changed, not how the **US Code**
would change. Bills are amendatory instructions, not diffs, and executing them
automatically is unsolved. `derived/amendments.md` carries out the solvable
part: measured over the whole corpus, 214,139 of 1,155,101 instructions (18.5%)
state both the text removed and the text inserted and are executed; the rest
are listed with the reason. The rate is mostly a fact about the year — GPO
tagged citations in 64% of the 108th's documents and 5% of the 112th's — so it
runs from 1.3% to 23.9% by Congress with no change in the reading. Anything
synthesised is marked derived and unofficial.

The federal text is a work of the United States Government and public domain
under 17 U.S.C. § 105. **Never rewrite it** — not for spelling, not for
formatting. Only the layer this project authored around it is ours to change.
