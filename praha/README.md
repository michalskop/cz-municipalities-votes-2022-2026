# Praha

Status: **scaffolded (C1)** — downloader/standardizer not yet built (task C7).

- Source: `config/sources.yml` — the open-data CSV (`storage.golemio.cz`), **not** the old
  `praha.eu` HTML scraper (confirmed broken — 302 redirects). See
  [`../research/R1-praha.md`](../research/R1-praha.md) (in the planning repo) for the full
  research writeup, including a data-quality pattern C7 must handle (mid-term substitute
  councilors undercounted in 124/2346 rows).
- Legacy (frozen, pre-standard) scripts and CSV outputs:
  [`../legacy/praha/`](../legacy/praha/) — its scraper is broken and was never a working reference
  for the new source; kept only for the historical Flourish charts and as documentation of the
  old (now-dead) endpoint.
- Charts from the legacy pipeline (pre-standard, kept for context):
  - govity: https://public.flourish.studio/visualisation/12766953/
  - attendance: https://public.flourish.studio/visualisation/12766366/
  - WPCA: https://public.flourish.studio/visualisation/14119299/
  - rebelity: https://public.flourish.studio/visualisation/12766926/

## Structure

```
config/sources.yml   — source endpoint(s)
scripts/              — downloader + standardizer (task C7, not built yet)
work/                 — ephemeral, gitignored (raw downloads, intermediate files)
data/                 — committed dt-standard tables: persons.csv, organizations.csv,
                        memberships.csv, votes.csv, vote_events.json, motions.json
analyses/<slug>/      — attendance, rebelity, govity, wpca: definition.json (needs owner
                        sign-off, see analyses/README.md) + outputs/<slug>.json (committed)
```

See the repo root [`README.md`](../README.md) for the full per-city contract and quality gates.
