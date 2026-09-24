#!/usr/bin/env python3
"""Standardize Plasy's manually reviewed roll-call CSV into shared data tables.

This script is deliberately offline: it never downloads or updates its input.
After a human has updated and checked source/roll_call_votes.csv, run it from
the municipality-data repository root with `python plasy/scripts/standardize.py`.
"""
import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

SOURCE_URL = "https://www.plasy.cz/mesto/samosprava/zastupitelstvo-mesta-plasy/zapisy-a-usneseni/?page=1"
ROSTER_URL = "https://www.plasy.cz/mesto/samosprava/zastupitelstvo-mesta-plasy/"
ORG_ID = "plasy:org:zastupitelstvo-mesta-plasy"
ORG_NAME = "Zastupitelstvo města Plasy"
OLD_MEMBER = "Bezdíčková Jitka"
NEW_MEMBER = "Kornatovský Ivo"
CURRENT_MEMBERS = {
    "Belbl Emanuel", "Gross Václav", "Hanzlíček Zdeněk", "Ježková Martina",
    "Kantor Pořádková Eva", "Kornatovský Ivo", "Kouba Tomáš", "Kovářík Petr",
    "Neuman Petr", "Novotný Jiří", "Palmová Eliška", "Pfeifer Lukáš",
    "Škop Michal", "Tyrpeklová Zdenka", "Urbanová Veronika",
}
OPTIONS = ("yes", "no", "abstain", "absent")
GROUPS = {
    "nezavisli-pro-plasko": "Nezávislí pro Plasko",
    "cssd": "ČSSD",
    "my": "MY",
    "ods": "ODS",
    "kdu-csl": "KDU-ČSL",
    "jdeto-s-podporou-top-09": "JdeTo s podporou TOP 09",
}
GROUP_BY_MEMBER = {
    "Gross Václav": "nezavisli-pro-plasko",
    "Kantor Pořádková Eva": "nezavisli-pro-plasko",
    "Škop Michal": "nezavisli-pro-plasko",
    "Neuman Petr": "nezavisli-pro-plasko",
    "Ježková Martina": "nezavisli-pro-plasko",
    "Pfeifer Lukáš": "nezavisli-pro-plasko",
    "Hanzlíček Zdeněk": "cssd",
    "Kovářík Petr": "cssd",
    "Belbl Emanuel": "cssd",
    "Novotný Jiří": "my",
    "Kouba Tomáš": "my",
    "Bezdíčková Jitka": "ods",
    "Kornatovský Ivo": "ods",
    "Tyrpeklová Zdenka": "kdu-csl",
    "Urbanová Veronika": "jdeto-s-podporou-top-09",
    "Palmová Eliška": "jdeto-s-podporou-top-09",
}


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    city_root = Path(__file__).resolve().parents[1]
    p.add_argument("--input", type=Path, default=city_root / "source" / "roll_call_votes.csv")
    p.add_argument("--out", type=Path, default=city_root)
    args = p.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as f:
        source = list(csv.DictReader(f, delimiter=";"))
    if not source:
        raise ValueError("Input vote CSV is empty")

    members = sorted({row["member"] for row in source})
    if len(members) != 16 or set(members) != CURRENT_MEMBERS | {OLD_MEMBER}:
        raise ValueError(
            "Expected the 15 current members plus Jitka Bezdíčková; got "
            + ", ".join(members)
        )
    if set(members) != set(GROUP_BY_MEMBER):
        raise ValueError("Political-affiliation table does not cover the 15 current members plus the predecessor")
    id_by_member = {m: f"plasy:person:{slug(m)}" for m in members}
    if len(set(id_by_member.values())) != len(members):
        raise ValueError("Canonical member names collide after ID slugification")
    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in source:
        if row["vote"] not in OPTIONS:
            raise ValueError(f"Unexpected option {row['vote']!r}")
        by_event[row["vote_event_id"]].append(row)

    persons = []
    for member in members:
        parts = member.split()
        given, family = parts[-1], " ".join(parts[:-1])
        source_urls = sorted({r["source_url"] for r in source if r["member"] == member})
        persons.append({
            "id": id_by_member[member], "name": f"{given} {family}",
            "given_name": given, "family_name": family,
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps([{"url": u} for u in source_urls], ensure_ascii=False),
        })

    orgs = [{
        "id": ORG_ID, "name": ORG_NAME, "classification": "assembly",
        "identifiers": json.dumps([], ensure_ascii=False),
        "sources": json.dumps([{"url": SOURCE_URL}], ensure_ascii=False),
    }] + [{
        "id": f"plasy:org:group:{group_slug}", "name": group_name,
        "classification": "group", "identifiers": json.dumps([], ensure_ascii=False),
        "sources": json.dumps([{"url": ROSTER_URL}], ensure_ascii=False),
    } for group_slug, group_name in GROUPS.items()]
    memberships = []
    for member in members:
        start = "2024-03-13" if member == NEW_MEMBER else "2022-10-12"
        end = "2024-02-20" if member == OLD_MEMBER else ""
        group_slug = GROUP_BY_MEMBER[member]
        group_start = start
        group_end = end
        group_id = f"plasy:org:group:{group_slug}"
        source = [{"url": ROSTER_URL, "note": "Political affiliation shown on the official council roster."}]
        if member == OLD_MEMBER:
            source.append({"url": "https://www.plasy.cz/modules/file_storage/download.php?file=fd132e0c%7C368&inline=1", "note": "Opening council minutes identify Bezdíčková as elected for ODS."})
        memberships.append({
            "id": f"plasy:membership:{slug(member)}:zastupitelstvo-mesta-plasy",
            "person_id": id_by_member[member], "organization_id": ORG_ID,
            "start_date": start, "end_date": end,
            "sources": json.dumps([{"url": SOURCE_URL, "note": "Term membership; Bezdíčková resigned effective 2024-02-20; Kornatovský took the oath at ZM 9 on 2024-03-13."}], ensure_ascii=False),
        })
        memberships.append({
            "id": f"plasy:membership:{slug(member)}:{group_slug}",
            "person_id": id_by_member[member], "organization_id": group_id,
            "start_date": group_start, "end_date": group_end,
            "sources": json.dumps(source, ensure_ascii=False),
        })

    vote_rows, events, motions = [], [], []
    for event_id in sorted(by_event):
        rows = by_event[event_id]
        if len({row["member"] for row in rows}) != len(rows):
            raise ValueError(f"Duplicate person row for {event_id}")
        expected_roster = 14 if event_id == "2024-03-13-001" else 15
        if len(rows) != expected_roster:
            raise ValueError(f"Expected {expected_roster} active member rows for {event_id}; got {len(rows)}")
        first = rows[0]
        counts = Counter(r["vote"] for r in rows)
        expected = {k: int(first[f"{k}_count"]) for k in ("yes", "no", "abstain")}
        actual = {k: counts[k] for k in ("yes", "no", "abstain")}
        if expected != actual or sum(counts[x] for x in OPTIONS) != len(rows):
            raise ValueError(f"Source tally mismatch for {event_id}: counts={counts}, expected={expected}")
        date = first["date"]
        vote_event_id = f"plasy:vote-event:{event_id}"
        motion_id = f"plasy:motion:{event_id}"
        for row in rows:
            vote_rows.append({
                "vote_event_id": vote_event_id, "voter_id": id_by_member[row["member"]],
                "voter_type": "person", "option": row["vote"],
            })
        counts_list = [{"option": key, "value": counts[key]} for key in OPTIONS]
        url = first["source_url"]
        identifier = f"ZM {first['sitting_number']} / hlasování {event_id.rsplit('-', 1)[1]}"
        events.append({
            "id": vote_event_id, "identifier": identifier, "motion_id": motion_id,
            "organization_id": ORG_ID, "start_date": f"{date}T00:00:00", "status": "valid",
            "counts": counts_list, "sources": [{"url": url}],
            "extras": {
                "sitting_number": int(first["sitting_number"]), "topic": first["topic"],
                "topic_description": first["topic_description"],
                "source_event_id": event_id, "source_present_count": int(first["present_count"]),
                "result": None,
            },
        })
        description = first["topic_description"].strip()
        text = first["topic"].strip() + (f"\n{description}" if description else "")
        motions.append({
            "id": motion_id, "identifier": identifier, "organization_id": ORG_ID,
            "date": date, "text": text, "sources": [{"url": url}],
            "extras": {"sitting_number": int(first["sitting_number"]), "source_event_id": event_id},
        })

    data = args.out / "data"
    write_csv(data / "persons.csv", persons,
              ["id", "name", "given_name", "family_name", "identifiers", "sources"])
    write_csv(data / "organizations.csv", orgs,
              ["id", "name", "classification", "identifiers", "sources"])
    write_csv(data / "memberships.csv", memberships,
              ["id", "person_id", "organization_id", "start_date", "end_date", "sources"])
    write_csv(data / "votes.csv", vote_rows, ["vote_event_id", "voter_id", "voter_type", "option"])
    (data / "vote_events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (data / "motions.json").write_text(json.dumps(motions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Prepared {len(members)} people, {len(vote_rows)} member votes, {len(events)} events")


if __name__ == "__main__":
    main()
