# Brno

Status: **scaffolded (C1)** — downloader/standardizer not yet built (task C2).

- Source: `config/sources.yml`. **Caution:** the primary JSON endpoint
  (`kod.brno.cz/zastupitelstvo/`) returned HTTP 503 on 2026-08-04 across several retries — verify
  it's back before building C2, or fall back to the ArcGIS source noted in the config.
- Legacy (frozen, pre-standard) scripts and CSV outputs: [`../legacy/brno/`](../legacy/brno/) —
  useful as a vocabulary reference and a regression baseline, not to be updated.
- Charts from the legacy pipeline (pre-standard, kept for context):
  - govity: https://public.flourish.studio/visualisation/12763909/
  - attendance: https://public.flourish.studio/visualisation/12764879/
  - rebelity: https://public.flourish.studio/visualisation/12765133/
  - WPCA: https://public.flourish.studio/visualisation/14119828/

## Structure

```
config/sources.yml   — source endpoint(s)
scripts/              — downloader + standardizer (task C2, not built yet)
work/                 — ephemeral, gitignored (raw downloads, intermediate files)
data/                 — committed dt-standard tables: persons.csv, organizations.csv,
                        memberships.csv, votes.csv, vote_events.json, motions.json
analyses/<slug>/      — attendance, rebelity, govity, wpca: definition.json (needs owner
                        sign-off, see analyses/README.md) + outputs/<slug>.json (committed)
```

See the repo root [`README.md`](../README.md) for the full per-city contract and quality gates.
