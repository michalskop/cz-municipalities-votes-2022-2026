"""Build an all-members.dt.analyses-shaped JSON from Praha's standard tables.

The shared `legislature-data-analyses` scripts (attendance.py, rebelity.py, govity.py, wpca.py)
all take `--persons` in the `all-members.dt.analyses` shape (see
https://michalskop.github.io/legislature-data-standard/dt.analyses/all-members/latest/schemas/
all-members.dt.analyses.json) — a flat persons.csv is not enough, because govity/rebelity need
each person's *group* membership (with since/until) to know which faction they belonged to at each
vote-event date.

Praha has no live klub/group data this session (see praha/config/sources.yml,
`group_affiliation_source` and plan.md decision D7's standing rule) — `praha/scripts/
party_affiliation.py` populated `praha:org:candidate-list:*` organizations and memberships as the
documented fallback instead. This script folds those into the all-members `memberships.groups`
field (which is what govity.py/rebelity.py actually read — see their `build_group_memberships`),
*and* into `memberships.candidate_list` (the field the schema intends for this exact kind of data),
so both a govity/rebelity run and any future generic tooling that expects `candidate_list` in its
proper place both work. This dual-write is a deliberate, documented modeling choice — not a
mistake — because the upstream analysis scripts hard-code "groups" as the group-membership lookup
key and Praha has no other source of "groups" to give them (D7's classification distinction is
preserved on the organizations.csv side: these orgs are `classification: candidate_list`, never
relabeled as `"group"`).

Output is NOT committed (praha/work/ is gitignored) — regenerate on demand before running an
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
_CANDIDATE_LIST_CLASSIFICATION = "candidate_list"


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
    candidate_list_org_ids = {oid for oid, o in org_by_id.items() if o["classification"] == _CANDIDATE_LIST_CLASSIFICATION}

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
        candidate_list = []
        for m in person_memberships:
            item = {
                "id": m["organization_id"],
                "name": org_by_id.get(m["organization_id"], {}).get("name", m["organization_id"]),
                "start_date": _none_if_empty(m.get("start_date")),
                "end_date": _none_if_empty(m.get("end_date")),
            }
            if m["organization_id"] in assembly_org_ids:
                parliament.append(item)
            elif m["organization_id"] in candidate_list_org_ids:
                # Dual-write: see module docstring for why both "groups" and "candidate_list".
                groups.append(dict(item))
                candidate_list.append(dict(item))

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
                    "candidate_list": candidate_list,
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
