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
