# Analyses

Four analyses at launch (attendance, rebelity, govity, wpca — vote-corrections excluded, cities
don't publish corrections). Each runs the *shared, unmodified* scripts from
`michalskop/legislature-data-analyses`; all city-specific content goes in this repo's definition
files, never in the analysis code itself.

## Per analysis

```
<slug>/<slug>_definition.json   — the only place with city-specific content: vote-option
                                   vocabularies, since/until dates, government_groups (coalition).
                                   Must validate against the published <slug>-definition.dt.analyses
                                   schema. REQUIRES OWNER SIGN-OFF before first publish (project
                                   plan decision D7) — a wrong government_groups value silently
                                   produces a wrong govity score.
<slug>/outputs/<slug>.json      — committed output. Exact path — the dashboard's fetch URL is
                                   built from it. Do not rename.
```

## Coalition / government_groups

This is the highest-risk factual input in the whole pipeline. Draft it with citations to public
sources (coalition agreements, council composition pages), and get it reviewed against the owner
sign-off requirement before the definition file's first commit. If the coalition changes mid-term,
express it as multiple government_groups entries with since/until — do not silently overwrite.

## Status (2026-08-04, C8)

All four `*_definition.json` files in this directory are **Sonnet-drafted DRAFTS, not yet
owner-approved** (plan.md D7). `govity_definition.json` in particular carries citations for its
`government_groups` claim (SPOLU pro Prahu + Česká pirátská strana + STAROSTOVÉ A NEZÁVISLÍ,
coalition signed 2023-02-15 — PRAHA SOBĚ explicitly excluded) in its own `extras` field; the owner
must verify those citations before this file is used to publish real output. See each
`extras.draft_status` field for the same caveat repeated in-file.

The `outputs/<slug>.json` files currently committed alongside each definition were produced by
`praha/scripts/analyses/run_<slug>.py` as a **pipeline smoke test** (proving the runner ->
shared-script -> schema-validated-output chain works end to end) — they are **provisional** for
the same reason as the definitions that produced them. Do not wire them into a nightly workflow or
the dashboard (C6/A2) until D7 sign-off lands; re-run the four `run_*.py` scripts to refresh them
once it does.

### Running a analysis locally

```
python praha/scripts/analyses/run_attendance.py --script /path/to/legislature-data-analyses/attendance/attendance.py
python praha/scripts/analyses/run_rebelity.py   --script /path/to/legislature-data-analyses/rebelity/rebelity.py
python praha/scripts/analyses/run_govity.py     --script /path/to/legislature-data-analyses/govity/govity.py
python praha/scripts/analyses/run_wpca.py       --script /path/to/legislature-data-analyses/wpca/wpca.py
```

`--script` has no default because `legislature-data-analyses` is a separate repository not checked
out inside this one (same convention as cz-psp-data-2025-202x's own `scripts/analyses/run_*.py`).
Each runner regenerates `praha/work/analysis_inputs/all_members.json` (gitignored, see
`praha/scripts/build_all_members.py`) from `praha/data/{persons,organizations,memberships}.csv`
before invoking the shared script — pass `--skip-build-persons` to reuse an existing one.
