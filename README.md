# us-congress-pipeline

ETL that mirrors the workings of the US Congress as git repositories — the text of federal law
with its history as commits, every bill as a branch, tags marking the law at a point in time,
and commit messages carrying sponsors, cosponsors, committee actions and votes.

**This repository contains only the pipeline.** It *generates* the data repositories; it does
not contain them.

## Why this exists

There is no actively maintained git repository of US federal law. A wave of them appeared in
March–April 2026 and every one went silent within weeks; the older, better-known projects
(`divegeek/uscode`, `unitedstates/uscode`) are archived. Meanwhile the upstream government
feeds — govinfo bulk data, OLRC release points, the Congress.gov API — are healthy and
publishing daily.

## Repositories it produces

| Repository | Contents |
|---|---|
| `us-congress-code` | the codified US Code; one commit per OLRC release point, tagged |
| `us-congress-statutes` | Statutes at Large, volumes 1–137 (1789–2023) |
| `us-congress-bills-{NNN}` | one repo per Congress; one branch per bill |
| `us-congress-record-{NNN}` | Congressional Record, sharded by Congress |

Sharding by Congress is forced by arithmetic: roughly 180,000 measures since 2003, against a
recommended ceiling of 5,000 branches per repository.

All repositories are private for now. Publishing is a one-way door and the decision is
deliberately deferred.

## What this is — and what it is not

Diffing a bill branch against its base tag shows how the **bill** changed, not how the **US
Code** would change. Bills are written as amendatory instructions ("strike subsection (b) and
insert…"), not as diffs, and executing them automatically is an unsolved problem: measured
across seven real bills, only ~49% of amendatory instructions carry a machine-readable US Code
reference, and a large bill would need ~99.99% per-instruction accuracy to come out wholly
correct.

Any synthesised "effect on existing law" is therefore marked derived and unofficial, and the
pipeline emits explicit *unapplied* markers rather than guessing.

## Usage

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env              # then add your govinfo key
uv run uscongress comps           # snapshot Statute Compilations
uv run uscongress comps --fresh   # ignore today's manifest, refetch everything
uv run pytest                     # tests
```

### Why `comps` runs first

govinfo replaces Statute Compilations **in place and keeps no version archive**. Once a
compilation is superseded, the previous text is gone. Non-codified law — the Social Security
Act, for example, which bills amend by act section rather than by US Code citation — exists
nowhere else in versioned form. Every day without a snapshot is history that cannot be
recovered, which makes this the only genuinely time-sensitive job in the project.

Snapshots are content-addressed: blobs live under `data/comps/objects/` keyed by SHA-256, with
one manifest per run under `data/comps/snapshots/`. A second run against an unchanged
collection costs one manifest, not another 270 MB.

## Layout

```
src/uscongress/
├── config.py        filesystem layout, credentials, rate limits
├── govinfo.py       rate-limited, retrying async client
└── jobs/
    └── comps.py     Statute Compilations snapshot
data/                gitignored — corpora and generated repos (~50 GB)
```

`data/` is gitignored in full. It holds the fetched corpora and the generated repositories,
which carry their own git histories; nesting them inside this repository's history would be
painful to undo.

## Data sources

All federal, all public domain under 17 U.S.C. § 105.

- **US Code** — OLRC release points, USLM 1.0 XML (`uscode.house.gov/download/`)
- **Statutes at Large / PLAW / COMPS** — govinfo bulk data, USLM 2.0 XML
- **Bills** — govinfo `BILLS` (text) and `BILLSTATUS` (metadata, back to the 108th Congress)
- **Attribution** — OLRC Table III, mapping public-law sections to US Code sections
- **Members** — `unitedstates/congress-legislators` (CC0), the bioguide↔LIS crosswalk

Two mutually incompatible USLM schemas are in production: OLRC emits v1.0.15, GPO emits
v2.0.17. The pipeline needs both.

Bulk listing endpoints return **HTTP 406** without an `Accept: application/json` header — they
work in a browser and fail from a script. The client handles this.

## Licence

Compiled federal text is public domain under 17 U.S.C. § 105, and each generated data
repository carries that notice.

This pipeline is **proprietary — © 2026 Jade Naaman, all rights reserved.** No licence is
granted. Terms may be opened up later; nothing here is offered under an open-source licence
today.
