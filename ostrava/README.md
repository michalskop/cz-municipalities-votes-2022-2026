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
- **C4 mechanical part done (2026-08-27)**: real, precisely DATED klub (assembly group)
  organizations + memberships — found (not assumed) that the first vote of every meeting has
  klub grouping whenever any vote in that meeting does, for 24 of 31 meetings (2022-10-19 through
  2025-06-18); no klub data published since. This directly captured the ANO 2011 club split with
  exact dates: still intact at meeting 202302 (2023-02-22), a transitional "Nezařazení" klub
  appears at 202303 (2023-03-22), replaced by a new klub "JDETO!!!" by 202304 (2023-04-26). See
  `scripts/party_affiliation.py`. `scripts/check_klub_staleness.py` (non-blocking) already warns
  — the last confirmed klub snapshot is 400+ days old as of this writing.
- Four analysis definitions drafted (`analyses/*/`_definition.json`, validated against their
  schemas). attendance/rebelity need no political judgment and are ready to use. govity/wpca's
  `government_groups` is drafted with full citations (a stable 6-klub coalition confirmed from
  2023-04-25 onward — ANO 2011, ODS+TOP09, KDU-ČSL, Ostravak, STAROSTOVÉ pro OSTRAVU, Piráti) but
  marked **PENDING PROJECT OWNER SIGN-OFF (D7)** — one specific open question (were Ostravak/
  STAROSTOVÉ pro OSTRAVU ALSO in the coalition for the term's first ~6 months, or only from
  2023-04-25?) is listed in `govity_definition.json`'s `open_questions_for_owner` rather than
  guessed. WPCA's government-axis auto-detection already shows a strong 0.72 correlation with
  this draft classification — a good sanity check, not proof either way on the open question.
- **Not yet done**: D7 sign-off, nightly workflow wiring, dashboard `CityConfig` entry.

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
