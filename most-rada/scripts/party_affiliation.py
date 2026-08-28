"""Build most-rada's REAL, live party/klub organizations + memberships from the zastupko.cz feed
(C4).

Same live-data shape as most/scripts/party_affiliation.py's source (same backend, same JSON
schema): each `zastupitele[].politickeSubjekty[]` entry is a real (idPolitickySubjekt, od, do)
interval — D7's *preferred* case. This script does no scraping and no guessing; it is a
mechanical, sourced-in-place transcription of that data into dt-standard organizations/memberships
rows, run against the SAME already-downloaded raw JSON standardize.py reads.

Scope boundary (per standardize.py's own docstring): this script builds real party/klub
organizations (`classification: "group"`) and real membership intervals for both political
entities held by rada members. It does NOT decide which of those groups form the governing
coalition — that is a `government_groups` fact for govity_definition.json/wpca_definition.json,
requiring the project owner's sign-off (D7). Note (already flagged in sources.yml, re-confirmed
here mechanically): rada has only 2 political subjects at all — both of Most's own governing-
coalition parties — so this D7 fact is close to trivial for this specific body (no opposition
klub exists to exclude).

End-date handling: ported with the fix already in place from the start (see most/'s own
party_affiliation.py docstring for the real bug this avoids rediscovering independently — the
source's `politickeSubjekty[].do` field is populated with the dataset's last-observed snapshot
date even for people STILL in that group, not just real departures).
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
_DEFAULT_RAW = _CITY_ROOT / "work" / "raw" / "zastupko_rada_dataset_8.json"
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
                "id": f"most-rada:org:group:{_slugify(s['zkrNazev'])}",
                "name": name,
                "classification": "group",
                "identifiers": json.dumps(
                    [{"scheme": "most-rada:zastupko_politicky_subjekt_id", "identifier": str(s["id"])}],
                    ensure_ascii=False,
                ),
                "sources": json.dumps(
                    [
                        {
                            "url": source_url,
                            "note": (
                                f"zastupko.cz politickeSubjekty id={s['id']}, zkrNazev={s['zkrNazev']!r}. "
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
            org_id = f"most-rada:org:group:{_slugify(subjekt['zkrNazev'])}"
            membership_id = f"most-rada:membership:group:{person_id.split(':', 2)[2]}:{org_id.split(':', 2)[2]}:{iv['od']}"
            raw_do = iv.get("do") or ""
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
                                    f"zastupko.cz zastupitele[].politickeSubjekty[] real interval "
                                    f"(idPolitickySubjekt={iv['idPolitickySubjekt']}). end_date "
                                    "blanked when equal to the dataset's global last-observed "
                                    "date (last-snapshot artifact, not a real departure)."
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
    """Add-or-replace by id — safe to re-run every time standardize.py has just rewritten
    organizations.csv/memberships.csv from scratch."""
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
    else:
        logging.info("No incomplete politickeSubjekty histories found (every person's earliest "
                      "political-entity interval starts on/before their own assembly start).")

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
