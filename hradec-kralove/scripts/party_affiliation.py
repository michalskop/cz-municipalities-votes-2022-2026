"""Build Hradec Králové's REAL, live party/klub organizations + memberships from the
zastupko.fit.vutbr.cz feed (C4). Near-verbatim port of Brno's/Most's own party_affiliation.py
(same feed shape) — only this docstring and a few identifier strings differ.

Like Brno/Most, this feed gives real, dated party/klub membership directly: each
`zastupitele[].politickeSubjekty[]` entry is a real (idPolitickySubjekt, od, do) interval — D7's
*preferred* case. This script does no scraping and no guessing; it is a mechanical, sourced-in-place
transcription of that data into dt-standard organizations/memberships rows, run against the SAME
already-downloaded raw JSON standardize.py reads (see hradec-kralove/config/sources.yml's
`zastupko_current`).

Scope boundary (per standardize.py's own docstring, which deliberately left this to C4): this
script builds real party/klub organizations (`classification: "group"`) and real membership
intervals for all 8 political entities actually held by at least one councilor. It does NOT decide
which of those groups form the governing coalition — that is a `government_groups`/
`government_members` fact for govity_definition.json/wpca_definition.json, requiring the project
owner's sign-off (D7). See that definition file's own citations for a genuinely interesting D7 case
here: 3 named individuals defected from their original klub (ANO 2011 x2, Rozvíjíme Hradec x1) into
"NEZ" (nezařazení) mid-term while continuing to vote with the governing coalition, after Rozvíjíme
Hradec itself was pushed out of the coalition — this script's job is only to transcribe the real
dated intervals that make that finding possible, not to interpret them.

Person-id consistency: imports `standardize._build_persons` and calls it on the same raw
`zastupitele` array, so every membership row here uses the exact same person_id as
persons.csv/memberships.csv.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_RAW = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standardize  # noqa: E402


def _slugify(text: str) -> str:
    return standardize._slugify(text)


def build_party_organizations(politicke_subjekty: list[dict[str, Any]], source_url: str) -> list[dict[str, Any]]:
    orgs = []
    for s in politicke_subjekty:
        name = s.get("plnyNazev") or s["zkrNazev"]
        orgs.append(
            {
                "id": f"hradec-kralove:org:group:{_slugify(s['zkrNazev'])}",
                "name": name,
                "classification": "group",
                "identifiers": json.dumps(
                    [{"scheme": "hradec-kralove:zastupko_politicky_subjekt_id", "identifier": str(s["id"])}],
                    ensure_ascii=False,
                ),
                "sources": json.dumps(
                    [
                        {
                            "url": source_url,
                            "note": (
                                f"zastupko.fit.vutbr.cz politickeSubjekty id={s['id']}, zkrNazev={s['zkrNazev']!r}. "
                                "Real live klub, not a 2022 candidate-list fallback (D7 preferred case)."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return orgs


def build_party_memberships(
    zastupitele: list[dict[str, Any]],
    id_to_person_id: dict[int, str],
    politicke_subjekty_by_id: dict[int, dict[str, Any]],
    source_url: str,
    assembly_start_by_person: dict[str, str],
    global_max_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    memberships: list[dict[str, Any]] = []
    incomplete_history: list[dict[str, Any]] = []

    for z in zastupitele:
        person_id = id_to_person_id[z["id"]]
        intervals = z.get("politickeSubjekty") or []
        if not intervals:
            continue
        # Flag anyone whose earliest recorded political-entity interval starts after their OWN
        # assembly-membership start date (not the fixed term start — a mid-term substitute
        # correctly has both start around the same later date, that's not a gap). A real gap
        # means the source's party-history record is incomplete for them, not something to
        # backfill by assumption (see module docstring's René Černý / Karin Podivinská example).
        earliest_start = min(iv["od"] for iv in intervals)
        own_assembly_start = assembly_start_by_person.get(person_id)
        if own_assembly_start and earliest_start > own_assembly_start:
            incomplete_history.append(
                {
                    "person_id": person_id,
                    "zastupko_id": z["id"],
                    "own_assembly_start": own_assembly_start,
                    "earliest_recorded_political_entity_start": earliest_start,
                }
            )

        for iv in intervals:
            subjekt = politicke_subjekty_by_id[iv["idPolitickySubjekt"]]
            org_id = f"hradec-kralove:org:group:{_slugify(subjekt['zkrNazev'])}"
            membership_id = f"hradec-kralove:membership:group:{person_id.split(':', 2)[2]}:{org_id.split(':', 2)[2]}:{iv['od']}"
            raw_do = iv.get("do") or ""
            # The source's own "do" field is populated with the dataset's last-observed snapshot
            # date even for people who are STILL in this group — it is not a real departure date
            # unless it's earlier than the whole dataset's last date. Same artifact already
            # documented for koalice/lidr in sources.yml's coverage_and_known_gap note (before that
            # note's 2026-08-26 update), and handled identically to standardize.py's own
            # _build_memberships (global_max_date comparison) for the bare assembly membership.
            # BUG (found 2026-08-27 via the dashboard's "no current groups render" symptom): this
            # function originally stored `raw_do` verbatim as end_date, silently closing every
            # still-current group membership.
            end_date_str = "" if raw_do == global_max_date else raw_do
            memberships.append(
                {
                    "id": membership_id,
                    "person_id": person_id,
                    "organization_id": org_id,
                    "start_date": iv["od"],
                    "end_date": end_date_str,
                    "sources": json.dumps(
                        [
                            {
                                "url": source_url,
                                "note": (
                                    f"zastupko.fit.vutbr.cz zastupitele[].politickeSubjekty[] real interval "
                                    f"(idPolitickySubjekt={iv['idPolitickySubjekt']}). end_date "
                                    "blanked when equal to the dataset's global last-observed "
                                    "date (last-snapshot artifact, not a real departure) — see "
                                    "build_party_memberships's inline comment."
                                ),
                            }
                        ],
                        ensure_ascii=False,
                    ),
                }
            )

    report = {"incomplete_history": incomplete_history}
    return memberships, report


def write_outputs(
    data_dir: Path,
    new_organizations: list[dict[str, Any]],
    new_memberships: list[dict[str, Any]],
) -> None:
    """Add-or-replace by id, mirroring Praha's party_affiliation.py write_outputs — safe to
    re-run every time standardize.py has just rewritten organizations.csv/memberships.csv from
    scratch (which has no knowledge of these `hradec-kralove:org:group:*` rows)."""
    org_path = data_dir / "organizations.csv"
    existing_orgs = pd.read_csv(org_path, dtype=str, keep_default_na=False)
    new_orgs_df = pd.DataFrame(new_organizations)
    new_org_ids = set(new_orgs_df["id"]) if not new_orgs_df.empty else set()
    kept_orgs = existing_orgs[~existing_orgs["id"].isin(new_org_ids)]
    combined_orgs = pd.concat([kept_orgs, new_orgs_df], ignore_index=True)
    combined_orgs.to_csv(org_path, index=False, encoding="utf-8")
    logging.info(
        "Wrote %s (%d existing + %d new = %d rows)",
        org_path,
        len(existing_orgs),
        len(new_orgs_df),
        len(combined_orgs),
    )

    mem_path = data_dir / "memberships.csv"
    existing_mems = pd.read_csv(mem_path, dtype=str, keep_default_na=False)
    new_mems_df = pd.DataFrame(new_memberships)
    new_mem_ids = set(new_mems_df["id"]) if not new_mems_df.empty else set()
    kept_mems = existing_mems[~existing_mems["id"].isin(new_mem_ids)]
    combined_mems = pd.concat([kept_mems, new_mems_df], ignore_index=True)
    combined_mems.to_csv(mem_path, index=False, encoding="utf-8")
    logging.info(
        "Wrote %s (%d existing + %d new = %d rows)",
        mem_path,
        len(existing_mems),
        len(new_mems_df),
        len(combined_mems),
    )


def run(raw_path: Path, data_dir: Path, sources_path: Path) -> dict[str, Any]:
    import yaml

    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    source_url = cfg["zastupko_current"]["url"]

    data = standardize._load_raw(raw_path)
    persons, id_to_person_id = standardize._build_persons(data["zastupitele"], source_url)

    existing_mems = pd.read_csv(data_dir / "memberships.csv", dtype=str, keep_default_na=False)
    assembly_start_by_person = dict(
        existing_mems.loc[
            existing_mems["organization_id"] == standardize.ORG_ID, ["person_id", "start_date"]
        ].itertuples(index=False, name=None)
    )

    politicke_subjekty_by_id = {s["id"]: s for s in data["politickeSubjekty"]}
    orgs = build_party_organizations(data["politickeSubjekty"], source_url)

    term = data["zastupitelstva"][0]
    global_max_date = max(standardize._session_date(session) for session in term["zasedani"])

    memberships, report = build_party_memberships(
        data["zastupitele"],
        id_to_person_id,
        politicke_subjekty_by_id,
        source_url,
        assembly_start_by_person,
        global_max_date,
    )

    write_outputs(data_dir, orgs, memberships)

    if report["incomplete_history"]:
        logging.warning(
            "%d person(s) have a politickeSubjekty history starting after their OWN assembly "
            "membership start date — the source's party-history record is incomplete for them, "
            "not backfilled: %s",
            len(report["incomplete_history"]),
            report["incomplete_history"],
        )

    report_path = data_dir.parent / "work" / "reports" / "party_affiliation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote report to %s", report_path)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(_DEFAULT_RAW))
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(Path(args.raw), Path(args.data_dir), Path(args.sources))


if __name__ == "__main__":
    main()
