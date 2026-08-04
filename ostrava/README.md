# Ostrava

Status: **scaffolded (C1)** — downloader/standardizer not yet built (task C9). No legacy scraper
existed for Ostrava in this repo; this is a fresh build, not a port.

- Source: `config/sources.yml` — per-councilor roll-call HTML at
  `ostrava.cz/.../vysledky_hlasovani/vo2226/z<YYYYMM>/<NNNN>.html`, confirmed available for the
  full 2022–2026 term. See [`../research/R2-ostrava.md`](../research/R2-ostrava.md) (in the
  planning repo) for the full research writeup.
- No JSON/CSV API — C9 must scrape HTML (enumerate meetings, then votes per meeting, then
  per-vote-event pages).

## Structure

```
config/sources.yml   — source endpoint(s)
scripts/              — downloader + standardizer (task C9, not built yet)
work/                 — ephemeral, gitignored (raw downloads, intermediate files)
data/                 — committed dt-standard tables: persons.csv, organizations.csv,
                        memberships.csv, votes.csv, vote_events.json, motions.json
analyses/<slug>/      — attendance, rebelity, govity, wpca: definition.json (needs owner
                        sign-off, see analyses/README.md) + outputs/<slug>.json (committed)
```

See the repo root [`README.md`](../README.md) for the full per-city contract and quality gates.
