# Ostrava

Status: **C9 done (2026-08-27)** — downloader + standardizer built and run against the full
2022–2026 term; G1 (schema), G2 (self-consistency) and G3 (golden sample) all pass. No legacy
scraper existed for Ostrava in this repo; this was a fresh build, not a port.

- Source: `config/sources.yml` — per-vote roll-call HTML at
  `ostrava.cz/.../vysledky_hlasovani/vo2226/z<code>/<NNNN>.html`, no JSON/CSV API. Full-term
  backfill: 31 meetings discovered from the term landing page, 2,116 individual vote pages
  crawled (with a politeness delay + retry-on-timeout — a full backfill against this old
  municipal server WILL hit transient timeouts, see `scripts/downloader.py`'s docstring).
- G2 caveat: unlike Praha/Brno, there is no separately-published aggregate to cross-check
  against — the G2 signal here is pure self-consistency (recomputed per-option tally vs. the same
  page's own published totals line). 2,116/2,116 events match exactly.
- Person identity finding: the same real person's displayed academic title (both prefix and
  comma-suffix credential) varies across different vote pages — see `scripts/standardize.py`'s
  module docstring. Identity is built from normalized (given_name, family_name) only; 59 distinct
  persons resulting, consistent with Ostrava's real ~55-58-member assembly (spot-checked against
  the live composition page, no sign of a false name-based split).
- **Not yet done**: C4 (real party/klub organizations + memberships, government_groups + owner
  sign-off per D7) — Ostrava's per-vote party grouping is only sometimes populated (confirmed
  empty on an entire sampled meeting), so this needs the live composition page
  (`ostrava.cz/.../slozeni-zastupitelstva-1`) as its primary source instead, plus real political
  research: the governing coalition changed mid-term (original 2022 ANO+SPOLU+Ostravak+Starostové
  pro Ostravu+Piráti coalition broke apart in March 2023 when part of ANO's club left to form an
  independent club; a new ANO+SPOLU coalition formed 2023-04-25) — not resolved here. Also not
  yet done: nightly workflow wiring, dashboard `CityConfig` entry.

## Structure

```
config/sources.yml   — source endpoint(s)
scripts/              — downloader + standardizer
work/                 — ephemeral, gitignored (raw downloads, intermediate files)
data/                 — committed dt-standard tables: persons.csv, organizations.csv,
                        memberships.csv, votes.csv, vote_events.json, motions.json
tests/                — golden-sample regression test (G3)
analyses/<slug>/      — attendance, rebelity, govity, wpca: definition.json (needs owner
                        sign-off, see analyses/README.md) + outputs/<slug>.json (committed)
```

See the repo root [`README.md`](../README.md) for the full per-city contract and quality gates.
