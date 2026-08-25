"""Re-apply the manually reconciled praha.eu roster overlay after every standardize.py +
party_affiliation.py run.

Why this exists: both of Praha's automated sources have a structural blind spot for personnel
changes. `standardize.py` derives persons/memberships entirely from the Golemio roll-call CSV,
which only reveals a personnel change once that person casts a recorded vote (invisible for a
brand-new substitute who hasn't voted yet, or a departure with no later roll call). And
`party_affiliation.py` derives candidate-list affiliation from the fixed 2022 election results,
which cannot reflect a later klub change (e.g. someone excluded from their party's assembly klub
while remaining a councilor). Neither script regenerates a wrong answer *maliciously* — they are
each correctly modeling their own source; the gap is that no automated source sees the full
picture.

`praha/scripts/fetch_praha_roster.py` cross-checks against the live praha.eu roster to diagnose
these gaps (Playwright-based, not run nightly — see that script's docstring). Each diagnosis, once
independently re-verified (praha.eu status, volby.cz candidate-list position, news/Wikipedia for
precise dates), is hardcoded below as a small, explicitly cited overlay — the same
`_KNOWN_SUBSTITUTES`-style pattern `party_affiliation.py` already uses for its own undiscoverable
substitutes, never guessed silently.

Both `standardize.py` and `party_affiliation.py` rewrite persons.csv/organizations.csv/
memberships.csv from scratch (or append-with-dedup) every run, with no knowledge of this overlay
— so without this script re-applying it every night, the 3 mid-term substitutes below silently
disappear (caught loudly by G4, since they're already committed — see the 2026-08-25 nightly
failure this script was written in response to) and the 2 override end_dates silently revert to
each script's own (incomplete) view (NOT caught loudly by G4, since a single changed row among ~140
sits under G4's 1% change-rate threshold — the more dangerous half of this gap).

This script's rows are the authoritative last word: every id below unconditionally overwrites
whatever standardize.py/party_affiliation.py produced for that id (add if missing, replace if
present) — see `_apply` and `check_overlay_staleness.py` (the companion script that flags when
`VERIFIED_AS_OF` is old enough that this static table itself may now be missing a *new*,
undiagnosed gap; this script cannot detect that on its own since it has no live source of its own).

Usage:
    python praha/scripts/roster_overlay.py --data-dir praha/data
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"

# The date of the praha.eu live-roster fetch (via fetch_praha_roster.py) that diagnosed and
# verified every entry below. Read by check_overlay_staleness.py — bump this (and re-verify/extend
# the tables below) whenever fetch_praha_roster.py is re-run against praha.eu.
VERIFIED_AS_OF = "2026-08-06"

# New persons the Golemio CSV cannot see yet (no recorded vote under their name as of
# VERIFIED_AS_OF). Row shape matches persons.csv exactly; see d433ceb's commit message for the
# full per-person citation trail (volby.cz candidate-list position, praha.eu detail page, and
# predecessor's end_date used as a start_date proxy where no more precise oath date exists).
_OVERLAY_PERSONS: list[dict[str, Any]] = [
    {
        "id": "praha:person:maria-sevcikova",
        "name": "Mária Ševčíková",
        "given_name": "Mária",
        "family_name": "Ševčíková",
        "identifiers": [
            {
                "scheme": "praha:praha_eu_detail_url",
                "identifier": "https://praha.eu/web/praha/seznam-zastupitelu#/detail/101141",
            },
            {"scheme": "praha:academic_title", "identifier": "Mgr."},
        ],
        "sources": [
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "praha.eu live roster, fetched 2026-08-06 via Playwright "
                    "(praha/scripts/fetch_praha_roster.py). Not present in the Golemio roll-call "
                    "CSV export as of this fetch (that export is stale since 2026-05-28 and has "
                    "never recorded a vote under her name), so — unlike this table's other rows — "
                    "she has no praha:csv_column_header identifier."
                ),
            }
        ],
    },
    {
        "id": "praha:person:klara-cingrosova",
        "name": "Klára Cingrošová",
        "given_name": "Klára",
        "family_name": "Cingrošová",
        "identifiers": [
            {
                "scheme": "praha:praha_eu_detail_url",
                "identifier": "https://praha.eu/web/praha/seznam-zastupitelu#/detail/101101",
            },
            {"scheme": "praha:academic_title", "identifier": "MUDr."},
        ],
        "sources": [
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "praha.eu live roster, fetched 2026-08-06 via Playwright "
                    "(praha/scripts/fetch_praha_roster.py). Not present in the Golemio roll-call "
                    "CSV export as of this fetch — no praha:csv_column_header identifier for the "
                    "same reason as Ševčíková above."
                ),
            }
        ],
    },
    {
        "id": "praha:person:michal-biskup",
        "name": "Michal Biskup",
        "given_name": "Michal",
        "family_name": "Biskup",
        "identifiers": [
            {
                "scheme": "praha:praha_eu_detail_url",
                "identifier": "https://praha.eu/web/praha/seznam-zastupitelu#/detail/-6110",
            },
            {"scheme": "praha:academic_title", "identifier": "Ing."},
        ],
        "sources": [
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "praha.eu live roster, fetched 2026-08-06 via Playwright "
                    "(praha/scripts/fetch_praha_roster.py). Not present in the Golemio roll-call "
                    "CSV export as of this fetch — no praha:csv_column_header identifier for the "
                    "same reason as Ševčíková above."
                ),
            }
        ],
    },
]

# New + overridden membership rows. Row shape matches memberships.csv exactly. New rows (the 3
# substitutes' zastupitelstvo-hmp + candidate-list memberships) are additions standardize.py/
# party_affiliation.py never produce on their own. Override rows (Prokop's departure, Kordová
# Marvanová's klub exclusion) already exist under these exact ids by the time this script runs —
# unconditionally replacing them is what re-applies the correction each night.
_OVERLAY_MEMBERSHIPS: list[dict[str, Any]] = [
    {
        "id": "praha:membership:maria-sevcikova:praha:org:zastupitelstvo-hmp",
        "person_id": "praha:person:maria-sevcikova",
        "organization_id": "praha:org:zastupitelstvo-hmp",
        "start_date": "2026-06-30",
        "end_date": "",
        "sources": [
            {
                "url": "https://cs.wikipedia.org/wiki/Ond%C5%99ej_Prokop",
                "note": (
                    "start_date is a proxy, not a directly observed oath date: predecessor "
                    "Ondřej Prokop's ANO 2011 mandate formally ended 2026-06-30 per this page's "
                    "infobox ('Ve funkci: 26. listopadu 2015 – 30. června 2026'); no more precise "
                    "oath-taking date for Ševčíková was discoverable."
                ),
            },
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=768&xstat=0&xvyber=0"
                ),
                "note": (
                    "Ševčíková Mária Mgr., original list position 16, přepočtené pořadí=2 "
                    "(2nd-in-line non-elected substitute on ANO 2011's 2022 candidate list, not "
                    "marked '*'/elected in 2022)."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "Confirmed current ANO 2011 member as of fetch 2026-08-06.",
            },
        ],
    },
    {
        "id": "praha:membership:maria-sevcikova:praha:org:candidate-list:ano-2011",
        "person_id": "praha:person:maria-sevcikova",
        "organization_id": "praha:org:candidate-list:ano-2011",
        "start_date": "2026-06-30",
        "end_date": "",
        "sources": [
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=768&xstat=0&xvyber=0"
                ),
                "note": (
                    "Not an originally elected candidate — přepočtené pořadí=2 (2nd-in-line) "
                    "non-elected substitute on ANO 2011's 2022 list (original list position 16, "
                    "70,741 votes); start/end mirror this person's zastupitelstvo membership "
                    "interval (data/memberships.csv)."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "Confirmed current ANO 2011 member as of fetch 2026-08-06.",
            },
        ],
    },
    {
        "id": "praha:membership:klara-cingrosova:praha:org:zastupitelstvo-hmp",
        "person_id": "praha:person:klara-cingrosova",
        "organization_id": "praha:org:zastupitelstvo-hmp",
        "start_date": "2026-03-26",
        "end_date": "",
        "sources": [
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "start_date is a proxy, not a directly observed oath date: predecessor "
                    "Josef Nerušil's SPD zastupitelstvo membership (data/memberships.csv) ended "
                    "2026-03-26 (derived from the Golemio CSV's last recorded vote for him); no "
                    "more precise oath-taking date for Cingrošová was discoverable. Confirmed "
                    "current 'Zastupitelský klub SPD' member as of fetch 2026-08-06."
                ),
            },
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=1545&xstat=0&xvyber=0"
                ),
                "note": (
                    "Cingrošová Klára MUDr., original list position 4, přepočtené pořadí=1 "
                    "(1st-in-line non-elected substitute on 'SPD,Trik.,PES a nez. pro Prahu''s "
                    "2022 candidate list, not marked '*'/elected in 2022)."
                ),
            },
        ],
    },
    {
        "id": "praha:membership:klara-cingrosova:praha:org:candidate-list:spd-trik-pes-a-nez-pro-prahu",
        "person_id": "praha:person:klara-cingrosova",
        "organization_id": "praha:org:candidate-list:spd-trik-pes-a-nez-pro-prahu",
        "start_date": "2026-03-26",
        "end_date": "",
        "sources": [
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=1545&xstat=0&xvyber=0"
                ),
                "note": (
                    "Not an originally elected candidate — přepočtené pořadí=1 (1st-in-line) "
                    "non-elected substitute on 'SPD,Trik.,PES a nez. pro Prahu''s 2022 list "
                    "(original list position 4, 19,666 votes); start/end mirror this person's "
                    "zastupitelstvo membership interval (data/memberships.csv)."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "Confirmed current 'Zastupitelský klub SPD' member as of fetch 2026-08-06.",
            },
        ],
    },
    {
        "id": "praha:membership:michal-biskup:praha:org:zastupitelstvo-hmp",
        "person_id": "praha:person:michal-biskup",
        "organization_id": "praha:org:zastupitelstvo-hmp",
        "start_date": "2025-04-24",
        "end_date": "",
        "sources": [
            {
                "url": (
                    "https://praha.eu/documents/42409/13647403/Stenozapis_zhmp250424-bez-udaju.pdf/"
                    "f4176e61-3973-74e4-f73b-07527fc9f552?version=1.0&t=1746601671272&download=true"
                ),
                "note": (
                    "Stenographic record of the 2025-04-24 zastupitelstvo session at which "
                    "Michal Biskup took the councilor's oath, replacing David Procházka (whose "
                    "own membership ended 2025-03-27 per the Golemio-derived start/end in "
                    "data/memberships.csv — a ~4-week gap is consistent with convening the next "
                    "regular session to seat a substitute)."
                ),
            },
            {
                "url": "https://prahatv.eu/zprava/michal-biskup-z-hnuti-stan-se-stal-novym-prazskym-zastupitelem/",
                "note": "News confirmation (published 2025-04-25) of Biskup replacing Procházka as STAN's Prague councilor.",
            },
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=166&xstat=0&xvyber=0"
                ),
                "note": (
                    "Biskup Michal Ing., original list position 6, přepočtené pořadí=2 "
                    "non-elected substitute on STAROSTOVÉ A NEZÁVISLÍ's 2022 candidate list (not "
                    "marked '*'/elected in 2022)."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "Confirmed current STAN member as of fetch 2026-08-06.",
            },
        ],
    },
    {
        "id": "praha:membership:michal-biskup:praha:org:candidate-list:starostove-a-nezavisli",
        "person_id": "praha:person:michal-biskup",
        "organization_id": "praha:org:candidate-list:starostove-a-nezavisli",
        "start_date": "2025-04-24",
        "end_date": "",
        "sources": [
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=166&xstat=0&xvyber=0"
                ),
                "note": (
                    "Not an originally elected candidate — přepočtené pořadí=2 non-elected "
                    "substitute on STAROSTOVÉ A NEZÁVISLÍ's 2022 list (original list position 6, "
                    "30,778 votes); start/end mirror this person's zastupitelstvo membership "
                    "interval (data/memberships.csv)."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "Confirmed current STAN member as of fetch 2026-08-06.",
            },
        ],
    },
    # --- overrides: ids already produced by standardize.py/party_affiliation.py, corrected here ---
    {
        "id": "praha:membership:ondrej-prokop:praha:org:zastupitelstvo-hmp",
        "person_id": "praha:person:ondrej-prokop",
        "organization_id": "praha:org:zastupitelstvo-hmp",
        "start_date": "2022-11-03",
        "end_date": "2026-06-30",
        "sources": [
            {
                "url": "https://storage.golemio.cz/ckan/obis/Vysledky_hlasovani_ZHMP_2022_-_2026.csv",
                "note": (
                    "start_date derived from first non-empty vote cell for this councilor's "
                    "column. end_date is NOT derivable from this export (stale since 2026-05-28, "
                    "no vote gap visible) — see the following sources for the actual departure."
                ),
            },
            {
                "url": "https://cs.wikipedia.org/wiki/Ond%C5%99ej_Prokop",
                "note": (
                    "Mandate formally ended 2026-06-30 per infobox ('Ve funkci: 26. listopadu "
                    "2015 – 30. června 2026'). He announced ending all political functions "
                    "2026-06-25 amid a controversy over undisclosed apartment ownership in his "
                    "asset declarations."
                ),
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "praha.eu confirms Prokop on its former-members list (fetched 2026-08-06 via "
                    "Playwright) — he has left the assembly entirely, not merely changed party "
                    "status (contrast with Kordová Marvanová below, who remains a current "
                    "assembly member)."
                ),
            },
        ],
    },
    {
        "id": "praha:membership:ondrej-prokop:praha:org:candidate-list:ano-2011",
        "person_id": "praha:person:ondrej-prokop",
        "organization_id": "praha:org:candidate-list:ano-2011",
        "start_date": "2022-11-03",
        "end_date": "2026-06-30",
        "sources": [
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=768&xstat=0&xvyber=0"
                ),
                "note": (
                    "Elected candidate on 'ANO 2011' (list poradi=2); start/end mirror this "
                    "person's zastupitelstvo membership interval (data/memberships.csv)."
                ),
            },
            {
                "url": "https://cs.wikipedia.org/wiki/Ond%C5%99ej_Prokop",
                "note": "Mandate formally ended 2026-06-30 (see the zastupitelstvo membership row's sources for the full citation).",
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": "praha.eu confirms Prokop on its former-members list (fetched 2026-08-06).",
            },
        ],
    },
    {
        "id": "praha:membership:hana-marvanova:praha:org:candidate-list:spolu-pro-prahu",
        "person_id": "praha:person:hana-marvanova",
        "organization_id": "praha:org:candidate-list:spolu-pro-prahu",
        "start_date": "2022-11-03",
        "end_date": "2023-02-17",
        "sources": [
            {
                "url": (
                    "https://www.volby.cz/pls/kv2022/kv111111?xjazyk=CZ&xid=1&xdz=4&"
                    "xnumnuts=1100&xobec=554782&xstrana=1327&xstat=0&xvyber=0"
                ),
                "note": (
                    "Elected candidate on 'SPOLU pro Prahu' (list poradi=16); start_date mirrors "
                    "this person's zastupitelstvo membership interval (data/memberships.csv)."
                ),
            },
            {
                "url": "https://cs.wikipedia.org/wiki/Hana_Kordov%C3%A1_Marvanov%C3%A1",
                "note": (
                    "'...vyloučil ji dne 17. února 2023 ze svých řad pražský zastupitelský klub "
                    "koalice SPOLU' — the SPOLU pro Prahu klub formally excluded her on "
                    "2023-02-17 after she did not support Bohuslav Svoboda's primátor candidacy "
                    "at the December 2022 constituting session. She has sat as an independent "
                    "('nezávislá') assembly member since. NOTE: this end_date reflects her live "
                    "klub/party status (per this project's D7 rule, preferred when scrapable — "
                    "now that praha.eu is confirmed scrapable via Playwright), which is a "
                    "distinct concept from 'candidate_list' origin; documented here rather than "
                    "silently reclassified, since D7 explicitly distinguishes the two."
                ),
            },
            {
                "url": "https://www.forum24.cz/hana-kordova-marvanova-konci-v-prazskem-spolu-klub-ji-vyloucil-protoze-nepodporila-vznik-koalice/",
                "note": "Contemporary news report (published 2023-02-17) of the exclusion, corroborating the Wikipedia date.",
            },
            {
                "url": "https://praha.eu/seznam-zastupitelu",
                "note": (
                    "praha.eu confirms she remains a CURRENT assembly member as of fetch "
                    "2026-08-06, listed as 'Nezařazení' (independent/unaffiliated) — her "
                    "zastupitelstvo-hmp membership is intentionally left untouched (open "
                    "end_date), only this candidate-list membership is closed."
                ),
            },
        ],
    },
]


def _apply(csv_path: Path, overlay_rows: list[dict[str, Any]], id_column: str = "id") -> tuple[int, int]:
    """Drop any existing row whose id matches an overlay row, then append the overlay rows —
    an unconditional add-or-replace, so this is safe (and a no-op in row count/content) to run
    every night regardless of what standardize.py/party_affiliation.py just produced.

    Returns (replaced_count, added_count).
    """
    existing = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    overlay_df = pd.DataFrame(
        [
            {
                **row,
                "identifiers": json.dumps(row["identifiers"], ensure_ascii=False),
                "sources": json.dumps(row["sources"], ensure_ascii=False),
            }
            if "identifiers" in row
            else {**row, "sources": json.dumps(row["sources"], ensure_ascii=False)}
            for row in overlay_rows
        ]
    )
    overlay_ids = set(overlay_df[id_column])
    existing_ids = set(existing[id_column])
    replaced = len(overlay_ids & existing_ids)
    added = len(overlay_ids - existing_ids)

    kept = existing[~existing[id_column].isin(overlay_ids)]
    combined = pd.concat([kept, overlay_df], ignore_index=True)
    combined.to_csv(csv_path, index=False, encoding="utf-8")
    return replaced, added


def apply_overlay(data_dir: Path) -> dict[str, Any]:
    persons_path = data_dir / "persons.csv"
    mem_path = data_dir / "memberships.csv"

    persons_replaced, persons_added = _apply(persons_path, _OVERLAY_PERSONS)
    logging.info(
        "%s: %d row(s) replaced, %d row(s) added from the roster overlay (verified_as_of=%s)",
        persons_path,
        persons_replaced,
        persons_added,
        VERIFIED_AS_OF,
    )

    mem_replaced, mem_added = _apply(mem_path, _OVERLAY_MEMBERSHIPS)
    logging.info(
        "%s: %d row(s) replaced, %d row(s) added from the roster overlay (verified_as_of=%s)",
        mem_path,
        mem_replaced,
        mem_added,
        VERIFIED_AS_OF,
    )

    return {
        "verified_as_of": VERIFIED_AS_OF,
        "persons_replaced": persons_replaced,
        "persons_added": persons_added,
        "memberships_replaced": mem_replaced,
        "memberships_added": mem_added,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    apply_overlay(Path(args.data_dir))


if __name__ == "__main__":
    main()
