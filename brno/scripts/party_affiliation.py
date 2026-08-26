"""Build Brno's REAL, live party/klub organizations + memberships from the zastupko.cz feed (C4).

Unlike Praha (which had to fall back to volby.cz's fixed 2022 candidate-list results per D7's
fallback rule, because praha.eu's live roster was originally unscrapable), Brno's feed gives real,
dated party/klub membership directly: each `zastupitele[].politickeSubjekty[]` entry is a real
(idPolitickySubjekt, od, do) interval — D7's *preferred* case. This script does no scraping and no
guessing; it is a mechanical, sourced-in-place transcription of that data into dt-standard
organizations/memberships rows, run against the SAME already-downloaded raw JSON standardize.py
reads (see brno/config/sources.yml's `zastupko_current`).

Scope boundary (per standardize.py's own docstring, which deliberately left this to C4): this
script builds real party/klub organizations (`classification: "group"`, not `"candidate_list"` —
see Praha's party_affiliation.py docstring for that distinction) and real membership intervals for
all 11 political entities actually held by at least one councilor. It does NOT decide which of
those groups form the governing coalition — that is a `government_groups` fact for
`govity_definition.json`/`wpca_definition.json`, requiring the project owner's sign-off (D7), same
as Praha.

Person-id consistency: imports `standardize._build_persons` and calls it on the same raw
`zastupitele` array, so every membership row here uses the exact same person_id as
persons.csv/memberships.csv (including the `_KNOWN_ID_RENUMBERINGS` merge — e.g. Petr Bořecký's
three political-entity intervals under source id 125 all land on the already-established
`brno:person:petr-borecky-3`, not a new id).

Known data-quality note, not silently smoothed over: `zastupitele[].politickeSubjekty[]` is
sometimes an incomplete history, not a full one. Compare Petr Bořecký (source id 125, 3 intervals:
ANO 2011 2022-10-20→2025-12-10, Nezávislí 2026-01-20→2026-03-03, Brno klidem 2026-04-14→2026-06-23)
against René Černý and Karin Podivinská (both real ANO 2011 members/deputy mayor since the 2022
coalition formation per contemporary news, see govity_definition.json's citations) — their
`politickeSubjekty[]` shows ONLY a single 2026-04-14→2026-06-23 "Brno klidem" interval, with no
earlier ANO 2011 entry at all. This script does not fabricate the missing earlier interval; it
transcribes exactly what the source has for each person, and this gap is logged.
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
                "id": f"brno:org:group:{_slugify(s['zkrNazev'])}",
                "name": name,
                "classification": "group",
                "identifiers": json.dumps(
                    [{"scheme": "brno:zastupko_politicky_subjekt_id", "identifier": str(s["id"])}],
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
            org_id = f"brno:org:group:{_slugify(subjekt['zkrNazev'])}"
            membership_id = f"brno:membership:group:{person_id.split(':', 2)[2]}:{org_id.split(':', 2)[2]}:{iv['od']}"
            memberships.append(
                {
                    "id": membership_id,
                    "person_id": person_id,
                    "organization_id": org_id,
                    "start_date": iv["od"],
                    "end_date": iv.get("do") or "",
                    "sources": json.dumps(
                        [
                            {
                                "url": source_url,
                                "note": (
                                    f"zastupko.cz zastupitele[].politickeSubjekty[] real interval "
                                    f"(idPolitickySubjekt={iv['idPolitickySubjekt']})."
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
    scratch (which has no knowledge of these `brno:org:group:*` rows)."""
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
    memberships, report = build_party_memberships(
        data["zastupitele"], id_to_person_id, politicke_subjekty_by_id, source_url, assembly_start_by_person
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
