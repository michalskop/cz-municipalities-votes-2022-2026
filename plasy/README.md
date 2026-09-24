# Plasy city assembly

Plasy data for the 2022–2026 city assembly term. This dataset is maintained and published manually; it is intentionally not included in `.github/workflows/nightly.yml` and has no scheduled downloader.

## Coverage and source

The source is the [official Plasy council minutes and resolutions archive](https://www.plasy.cz/mesto/samosprava/zastupitelstvo-mesta-plasy/zapisy-a-usneseni/?page=1). `source/roll_call_votes.csv` is the current event-by-member extraction from the available minutes: 22 sittings (ZM 1–21 and ZM 23) and 274 vote events, from 12 October 2022 to 16 September 2026. The archive has no ZM 22 record. Each row retains its official PDF URL.

The minutes are prose/PDF records rather than a structured per-member feed. The extraction infers unlisted yes votes only when the recorded counts balance, records mid-sitting arrivals and departures, and validates each event against its individual vote statuses. `scripts/standardize.py` creates the Legislature Data Standard tables from the checked CSV; shared schema validation is run before publishing.

The roster has 15 seats and 16 distinct people over the term. Martina Ježková was excused only at ZM 1 and took her oath at ZM 2. Jitka Bezdíčková resigned effective 20 February 2024; Ivo Kornatovský took his oath at ZM 9 on 13 March 2024. Bezdíčková and Kornatovský are treated as separate membership intervals. The first ZM 9 vote is before Kornatovský’s oath, so he has no vote row for that event. Political affiliations come from the [official council roster](https://www.plasy.cz/mesto/samosprava/zastupitelstvo-mesta-plasy/); Bezdíčková’s ODS affiliation is recorded in the opening council minutes. The governing coalition is Nezávislí pro Plasko and JdeTo s podporou TOP 09, as specified by the project owner and consistent with the [official council](https://www.plasy.cz/mesto/samosprava/zastupitelstvo-mesta-plasy/) and [city council](https://www.plasy.cz/mesto/samosprava/rada-mesta-plasy/) rosters.

The dashboard publishes attendance, member profiles, vote data, group rebellion, coalition alignment, and a WPCA voting map. The group assignment table is explicit in `scripts/standardize.py`; update it only against official sources. NpP is shown in light green, following the project owner's color instruction. Do not add Plasy to the nightly workflow.

## Manual refresh procedure

1. Check the official archive for newly published `Zápis ZM` PDFs. Do not include `RM` minutes.
2. In the source-extraction project, add the new PDF and its metadata to the meeting manifest, run its extractor, and review `review_events.csv`. Resolve tally/roster issues against the PDF before proceeding.
3. Replace `source/roll_call_votes.csv` with the reviewed full-period export. Preserve stable event IDs (`YYYY-MM-DD-NNN`) and update this README's coverage if the archive coverage changes.
4. From the repository root, run:

   ```sh
   python plasy/scripts/standardize.py
   python plasy/scripts/build_all_members.py --data-dir plasy/data --out plasy/work/all_members.json
   python /path/to/legislature-data-analyses/attendance/attendance.py \
     --definition plasy/analyses/attendance/attendance_definition.json \
     --votes plasy/data/votes.csv \
     --vote_events plasy/data/vote_events.json \
     --persons plasy/work/all_members.json \
     --output plasy/analyses/attendance/outputs/attendance.json
   python /path/to/legislature-data-analyses/rebelity/rebelity.py \
     --definition plasy/analyses/rebelity/rebelity_definition.json \
     --votes plasy/data/votes.csv --vote_events plasy/data/vote_events.json \
     --persons plasy/work/all_members.json --output plasy/analyses/rebelity/outputs/rebelity.json
   python /path/to/legislature-data-analyses/govity/govity.py \
     --definition plasy/analyses/govity/govity_definition.json \
     --votes plasy/data/votes.csv --vote_events plasy/data/vote_events.json \
     --persons plasy/work/all_members.json --output plasy/analyses/govity/outputs/govity.json
   python /path/to/legislature-data-analyses/wpca/wpca.py \
     --definition plasy/analyses/wpca/wpca_definition.json \
     --votes plasy/data/votes.csv --vote-events plasy/data/vote_events.json \
     --persons plasy/work/all_members.json --output plasy/analyses/wpca/outputs/wpca.json
   python plasy/scripts/detect_government_axis.py \
     --wpca-output plasy/analyses/wpca/outputs/wpca.json \
     --wpca-definition plasy/analyses/wpca/wpca_definition.json \
     --govity-definition plasy/analyses/govity/govity_definition.json \
     --memberships plasy/data/memberships.csv \
     --output plasy/analyses/wpca/outputs/government_axis.json
   python scripts/validate_tables.py --data-dir plasy/data
   python scripts/validate_records.py --data-dir plasy/data
   ```

5. Check counts, roster changes, vote tallies, all four analysis outputs, and the government-axis sidecar; commit the refreshed `plasy/source`, `plasy/data`, and `plasy/analyses/*/outputs` manually. The city dashboard reads committed files from this repository. Do not add Plasy to the nightly workflow unless the owner later requests automated collection.

`plasy/scripts/standardize.py` uses only Python's standard library and makes no network requests. `build_all_members.py` is likewise local. The shared attendance analysis needs its normal Python dependencies and the shared `legislature-data-standard` schemas.
