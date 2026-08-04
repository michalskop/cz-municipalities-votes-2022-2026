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

## Status (2026-08-04, C6 — updated from C8's now-stale text)

**All four `*_definition.json` files are APPROVED by the project owner** (commit `77e8009`, per
plan.md decision D7). Each file's own `extras.approval_status` field records the sign-off directly:
`govity_definition.json`'s `government_groups` claim (SPOLU pro Prahu + Česká pirátská strana +
STAROSTOVÉ A NEZÁVISLÍ, coalition signed 2023-02-15 — PRAHA SOBĚ explicitly excluded) was
independently re-verified against all four cited sources before approval — this was the
highest-risk factual input in the pipeline (D7's own framing) and it is now closed for Praha.

These definitions are cleared to publish real output and are wired into the nightly workflow
(`.github/workflows/nightly.yml`, task C6): every night, after the pipeline regenerates
`praha/data/` and the G4 monotonicity guard passes, all four `run_<slug>.py` scripts re-run against
the shared, unmodified `legislature-data-analyses` scripts, their outputs pass the G7 range-sanity
check (`scripts/g7_sanity_check.py` — no NaN/Infinity, share-like fields in `[0, 1]`), and the
refreshed `outputs/<slug>.json` files are committed alongside the data update. The
`outputs/<slug>.json` files currently in this directory therefore reflect real, approved,
nightly-refreshed output, not the C8 smoke-test placeholders they used to be.

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
