# Legacy (pre-standard, frozen)

The `brno/` and `praha/` directories here are the original Flourish-era analysis scripts and
outputs, tagged as `v1-flourish` before this freeze. They predate the Legislature Data Standard
(popolo/dt schemas) and use ad hoc CSV shapes (`voter_id` = person name, no persons/organizations/
memberships tables).

**These directories are frozen: they will not be updated again.** They are kept only as a
regression baseline — new pipelines (see the repo root) can diff their output against this data
for the overlapping period to catch parsing bugs.

The Praha scraper here is broken (the source website was rebuilt); Brno's scraper still worked as
of the freeze date.
