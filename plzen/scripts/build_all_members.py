"""Build an all-members.dt.analyses-shaped JSON from Plzeň's standard tables.

The shared `legislature-data-analyses` scripts (attendance.py, rebelity.py, govity.py, wpca.py)
all take `--persons` in the `all-members.dt.analyses` shape (see
https://michalskop.github.io/legislature-data-standard/dt.analyses/all-members/latest/schemas/
all-members.dt.analyses.json) — a flat persons.csv is not enough, because govity/rebelity need
each person's *group* membership (with since/until) to know which faction they belonged to at each
vote-event date.

Plzeň's `plzen/scripts/party_affiliation.py` populates real `classification: "group"`
organizations/memberships derived from per-vote klub text (mode value per meeting, contiguous-run
interval detection — D7's preferred live-group case; see that script's module docstring for the
real 3-way "Spolu" coalition split it captures, and the one confirmed pure-relabeling merge). This
script reads those into `memberships.groups` only — no dual-write into `memberships.candidate_list`,
since that field is meant for genuine candidate-list-origin data, which this isn't.

Output is NOT committed (plzen/work/ is gitignored) — regenerate on demand before running an
analysis, same as the other work/ artifacts in this pipeline.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"
_DEFAULT_OUT = _CITY_ROOT / "work" / "analysis_inputs" / "all_members.json"

_ASSEMBLY_CLASSIFICATION = "assembly"
_GROUP_CLASSIFICATION = "group"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _none_if_empty(v: str | None) -> str | None:
    return v if v else None


def build_all_members(data_dir: Path) -> list[dict[str, Any]]:
    persons = _read_csv(data_dir / "persons.csv")
    organizations = _read_csv(data_dir / "organizations.csv")
    memberships = _read_csv(data_dir / "memberships.csv")

    org_by_id = {o["id"]: o for o in organizations}
    assembly_org_ids = {oid for oid, o in org_by_id.items() if o["classification"] == _ASSEMBLY_CLASSIFICATION}
    group_org_ids = {oid for oid, o in org_by_id.items() if o["classification"] == _GROUP_CLASSIFICATION}

    if len(assembly_org_ids) != 1:
        raise ValueError(f"Expected exactly 1 assembly organization, found {len(assembly_org_ids)}: {assembly_org_ids}")

    memberships_by_person: dict[str, list[dict[str, str]]] = {}
    for m in memberships:
        memberships_by_person.setdefault(m["person_id"], []).append(m)

    records: list[dict[str, Any]] = []
    for p in persons:
        pid = p["id"]
        person_memberships = memberships_by_person.get(pid, [])

        parliament = []
        groups = []
        for m in person_memberships:
            item = {
                "id": m["organization_id"],
                "name": org_by_id.get(m["organization_id"], {}).get("name", m["organization_id"]),
                "start_date": _none_if_empty(m.get("start_date")),
                "end_date": _none_if_empty(m.get("end_date")),
            }
            if m["organization_id"] in assembly_org_ids:
                parliament.append(item)
            elif m["organization_id"] in group_org_ids:
                groups.append(item)

        identifiers = json.loads(p["identifiers"]) if p.get("identifiers") else []
        sources = json.loads(p["sources"]) if p.get("sources") else []

        records.append(
            {
                "id": pid,
                "name": p["name"],
                "given_name": p.get("given_name") or None,
                "family_name": p.get("family_name") or None,
                "identifiers": identifiers,
                "sources": sources,
                "memberships": {
                    "parliament": parliament,
                    "groups": groups,
                    "candidate_list": [],
                    "constituency": [],
                },
            }
        )

    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    parser.add_argument("--out", default=str(_DEFAULT_OUT))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    records = build_all_members(Path(args.data_dir))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    logging.info("Wrote %s (%d persons)", out_path, len(records))


if __name__ == "__main__":
    main()
