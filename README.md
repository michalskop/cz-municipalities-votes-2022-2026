# CZ municipalities votes 2022-2026

Monitor of roll-call votes in Czech municipal assemblies, term 2022–2026. Serves
`mesta.datatimes.cz/<city>` (a [legislature-dashboard](https://github.com/michalskop/legislature-dashboard)
app). No database — this repo *is* the data store: everything is git-committed CSV/JSON,
schema-validated, updated nightly by CI, no AI in the daily data path.

Reuses the same [Legislature Data Standard](https://github.com/michalskop/legislature-data-standard)
(popolo/dt schemas) and the same shared
[legislature-data-analyses](https://github.com/michalskop/legislature-data-analyses) scripts as
`snemovna.datatimes.cz` (Czech parliament) and `nrsr.datatimes.sk` (Slovak parliament) — this repo
only adds city-specific *sources* and *definition files*, never forked analysis logic.

## Municipalities

- **Praha** — [`praha/`](./praha/) · source: open-data CSV · status: pipeline built, G1/G2/G5 pass,
  definitions owner-approved (C7/C8 done)
- **Brno** — [`brno/`](./brno/) · source: `zastupko.cz` JSON feed, origin server (the original
  `kod.brno.cz` endpoint stayed down; see [`brno/README.md`](./brno/README.md)) · status:
  pipeline + golden sample + real party/klub data + analysis definitions done, G1/G2/G5 pass,
  coalition (`government_groups`) owner-approved (C2/C3/C4/D7 done); not yet wired into nightly
  automation or the dashboard
- **Ostrava** — [`ostrava/`](./ostrava/) · source: HTML scrape · status: scaffolded, pipeline not
  built yet (task C9)

Coverage may extend to more cities later (top-20 by population); a city is only added if it has
recorded roll-call votes.

## Repository structure

```
config/schemas.yml    — shared: published dt-standard schema URLs, used by every city
scripts/               — shared: schema validators (validate_tables.py, validate_records.py),
                          IO helpers. City-agnostic; never city-specific logic.
<city>/
  config/sources.yml   — city-specific: source endpoint(s), vote-option vocabulary notes
  scripts/              — city-specific: downloader + standardizer
  work/                 — ephemeral, gitignored
  data/                 — committed dt-standard tables (persons, organizations, memberships,
                          votes, vote-events, motions)
  analyses/<slug>/      — attendance, rebelity, govity, wpca: definition.json (owner sign-off
                          required, see <city>/analyses/README.md) + outputs/<slug>.json
legacy/<city>/          — frozen pre-standard scripts+CSVs (tag v1-flourish), regression
                          baseline only, never updated
```

## Quality gates

Every city's pipeline must pass, before data is committed:

- **Schema validation** — every table/record against the published dt-standard schemas
  (`scripts/validate_tables.py`, `scripts/validate_records.py`).
- **Source cross-check** — per-vote-event tallies reconciled against the source's own published
  aggregate counts; documented, non-silent handling of known exceptions (e.g. Praha's mid-term
  substitute pattern, see `praha/config/sources.yml`).
- **Golden sample** — a handful of vote events manually verified once, then pinned as a
  regression test.
- **Identity** — person IDs unique; ambiguous name collisions fail the pipeline rather than
  silently merging or splitting.

## Adding a city

1. Research the source (format, coverage, licence, update cadence, vote-option vocabulary,
   per-person granularity) — write it up with citations, decide go/no-go.
2. Scaffold `<city>/{config,scripts,work,data,analyses}` following an existing city's layout.
3. Build the downloader + standardizer, producing the dt-standard tables in `<city>/data/`.
   Cross-check totals against the source's own published aggregates.
4. Get `<city>/analyses/<slug>/<slug>_definition.json` (especially `government_groups` — the
   coalition) drafted with citations and **signed off by the project owner** before first publish.
5. Wire the nightly workflow's city matrix to include the new city.
6. Enable the city in the dashboard's `CityConfig`.

## Legacy

The original pre-standard scrapers and CSV outputs (tag `v1-flourish`) have moved to
[`legacy/`](./legacy/) — frozen, kept only as a regression baseline for the new pipelines.

## Articles
- https://www.seznamzpravy.cz/clanek/fakta-v-brne-k-sobe-maji-stale-nejblize-ods-s-top-09-a-ano-ukazuje-analyza-239593
- https://www.seznamzpravy.cz/clanek/domaci-hlasovani-zastupitelu-v-praze-a-brne-ano-a-spolu-k-sobe-maji-velmi-blizko-225824

## Data standards
http://www.popoloproject.com/

## Based on
- https://github.com/michalskop/cz-psp-votes-2021-202x 
- https://www.seznamzpravy.cz/clanek/fakta-poslanecka-snemovna-hlasovani-dochazka-poslancu-219329

## Previous article:
Praha + Brno + Ostrava: 
https://www.seznamzpravy.cz/clanek/fakta-tri-nejvetsi-mesta-koalice-drzely-pri-sobe-opozice-mely-horsi-dochazku-214977

