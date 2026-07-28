# us-congress

Mirroring the workings of the US Congress as git repositories: the text of federal law with
its history as commits, every bill as a branch, tags marking the state of the law at a point
in time, and commit messages carrying sponsors, cosponsors, committee actions and votes.

> **Status: scaffolding.** This repository currently holds only a project stub. The
> implementation plan lives at `~/.claude/plans/so-i-want-a-frolicking-whale.md`.

## Why

As of July 2026 there is **no actively maintained git repository of US federal law**. Several
appeared during a wave in March–April 2026 and all went silent within weeks; the older,
better-known projects (`divegeek/uscode`, `unitedstates/uscode`) are archived. Meanwhile the
upstream government feeds — govinfo bulk data, OLRC release points, the Congress.gov API — are
healthy and publishing daily.

## Planned shape

| Repository | Visibility | Contents |
|---|---|---|
| `junxit/us-code` | public | the law itself; one commit per OLRC release point, tagged |
| `junxit/us-congress-{NNN}` | public | one repo per Congress; one branch per bill |
| `junxit/uscongress-etl` | private | the ingestion pipeline |

Sharding by Congress is deliberate: there are roughly 180,000 measures since 2003, against a
recommended ceiling of 5,000 branches per repository.

## Primary sources

- **US Code** — OLRC release points, USLM 1.0 XML (`uscode.house.gov/download/`)
- **Statutes at Large** — govinfo, USLM 2.0 XML, volumes 1–137 (1789–2023)
- **Bills** — govinfo `BILLS` (text, 113th–119th) and `BILLSTATUS` (metadata, 108th–119th)
- **Attribution** — OLRC Table III, mapping public-law sections to US Code sections
- **Members** — `unitedstates/congress-legislators` (CC0)

## What this is not

Diffing a bill branch against its base tag shows how the **bill** changed, not how the **US
Code** would change. Bills are written as amendatory instructions ("strike subsection (b) and
insert…"), not as diffs; executing them automatically is an unsolved problem. Any synthesised
"effect on existing law" will be clearly marked as derived and unofficial.

## Licensing

Federal government works are in the public domain under 17 U.S.C. § 105, and any compiled
legal text published here carries a public-domain notice.

The tooling in this repository is **proprietary — © 2026 Jade Naaman, all rights reserved.**
No licence is granted. Licensing is a one-way door; terms may be opened up later, but nothing
here is offered under an open-source licence today.
