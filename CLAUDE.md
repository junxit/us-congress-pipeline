# CLAUDE.md

Orientation for anyone — human or agent — picking this repository up cold.

## What this is

An ETL that mirrors the workings of the US Congress as git repositories. This
repository is **only the pipeline**; it *generates* 30 data repositories and
contains none of them.

It owns **31 public repositories** under `github.com/junxit`:

| Repository | Count | Holds |
|---|---|---|
| `us-congress-pipeline` | 1 | this ETL |
| `us-congress-code` | 1 | the US Code, 383 release points, tagged |
| `us-congress-bills-{108..119}` | 12 | 160,190 branches, one per measure |
| `us-congress-statutes` | 1 | 135 volumes, 101,975 session laws |
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
uv run uscongress bootstrap   # clone the 30 data repos into data/repos/
```

The cached XML under `data/raw/` does not need restoring at all. Every job
refetches what it needs and caches it again; the cache is an optimisation, not
state. Rebuilding a *repository* from scratch is only necessary when a rendering
defect has to be corrected in commits that already exist — see `seed-bills
--rebuild`.

## Where the truth lives

Never duplicate these anywhere — memory, notes, or prose. They are the source.

| Question | Answer |
|---|---|
| What phase is the project in? | `src/uscongress/registry.py` → `PHASES` |
| Which repositories exist, and are they live? | `uv run uscongress index` → `REPOSITORIES.md` |
| Is the daily loop still alive? | `STATUS.md`, or `uscongress update --check` |
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
- **Two hosts, two rate regimes.** `api.govinfo.gov` is limited to 36,000
  requests/hour per key; `www.govinfo.gov` — bulkdata, `/content/pkg/`,
  `/metadata/pkg/` — is unkeyed and unlimited. Over 99% of a Record crawl goes
  to the second. See `config.GOVINFO_RATE_PER_SEC`.
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
automatically is unsolved: only ~49% of instructions carry a machine-readable
US Code reference. Anything synthesised is marked derived and unofficial.

The federal text is a work of the United States Government and public domain
under 17 U.S.C. § 105. **Never rewrite it** — not for spelling, not for
formatting. Only the layer this project authored around it is ours to change.
