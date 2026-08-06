# Brno

Status: **C2 done (2026-08-07)** — downloader + standardizer + pipeline runner built and run
against a live source; G1 (schema) and G2 (source cross-check) both pass. C3 (golden sample) and
C4 (definition files incl. coalition) are not started — both need owner sign-off (D7), out of
scope for C2.

- Source: `config/sources.yml`. The original primary (`kod.brno.cz/zastupitelstvo/`) is still
  returning HTTP 503 (re-verified 2026-08-04, 2026-08-06, 2026-08-07 — endpoint-specific, not
  sitewide). The pipeline instead uses a genuine live fallback found via the ArcGIS Hub item
  search: `zastupko.cz`'s per-person JSON feed (`zastupko_current` in sources.yml) — a different
  platform (FIT VUT Brno) than kod.brno.cz, covering the same council with real per-person votes
  and documented schema. See `config/sources.yml` for the full discovery trail, coverage gap
  (the feed itself lags the council's real meeting schedule by ~9 months as of 2026-08-07 — worth
  re-checking on every pipeline run), and data-quality findings.
- Output: `data/` has 58 persons, 1 organization, 58 memberships, 154,667 votes, 2,813 vote events,
  2,813 motions, covering 2022-10-20 through 2025-11-11 (the feed's current coverage — see the gap
  note above).
- Legacy (frozen, pre-standard) scripts and CSV outputs: [`../legacy/brno/`](../legacy/brno/) —
  used as the C2 reconciliation baseline: for the overlapping window (2022-10-20 → 2024-05-14),
  distinct voters match exactly (56 = 56), vote-event counts are a 99.7% match (1589 vs legacy's
  1584 — legacy's own filter dropped 5 near-empty votes this pipeline still includes), and a
  5-event random spot-check of yes/no/abstain tallies against legacy's independently-sourced
  aggregate (the old kod.brno.cz `details` field) matched exactly on all 5.
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
