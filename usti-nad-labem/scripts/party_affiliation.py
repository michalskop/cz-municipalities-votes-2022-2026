"""Build Ústí nad Labem's real klub (assembly group) organizations + memberships from the cached
vote PDFs (C4, mechanical part).

Source: standardize.py's own `resolve_all_events()` -- the SAME per-event klub text already
resolved while building votes.csv (see that file's module docstring's Scope boundary: klub text
is captured but not turned into organizations/memberships there; that's this script's job).

Same situation as Plzeň's source: no separate dated interval list exists (unlike zastupko-network
cities' politickeSubjekty[]) -- klub affiliation is only ever observed PER VOTE. This script
derives dated intervals the same way plzen/scripts/party_affiliation.py does: contiguous-run
detection over a per-MEETING (not per-vote) klub value, taking the MODE (most common) klub across
all of a person's votes within one meeting to smooth over any occasional per-event parsing noise.

Klub roster confirmed stable across the term (see sources.yml's corrected klub_history_note): 7
distinct klubs total (ANO2011, PRO!Ústí, SPD, ODS, VašeÚstí, UFO, nezařazení), all present from
meeting 2 onward -- meeting 1 (constitutive session) is the only one missing "nezařazení", which
makes sense (no one is unaffiliated before klub assignments happen) rather than being a real
mid-term event needing special handling.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standardize  # noqa: E402


def build_meeting_klub_history(raw_dir: Path) -> dict[int, dict[str, Any]]:
    """Returns {meeting_no: {"date": iso_date, "klub_by_person": {person_key: klub_name}}}, one
    entry per meeting, using the MODE klub across that meeting's events for each person."""
    events, _persons, _report = standardize.resolve_all_events(raw_dir)

    by_meeting: dict[int, dict[str, Any]] = {}
    for e in events:
        m = by_meeting.setdefault(e["meeting_no"], {"date": e["date"], "votes": {}})
        m["date"] = min(m["date"], e["date"])
        for o in e["options"]:
            m["votes"].setdefault(o["person_key"], Counter())[o["klub"]] += 1

    history: dict[int, dict[str, Any]] = {}
    for meeting_no, m in by_meeting.items():
        klub_by_person = {pk: counter.most_common(1)[0][0] for pk, counter in m["votes"].items()}
        history[meeting_no] = {"date": m["date"], "klub_by_person": klub_by_person}
    logging.info(
        "Klub data derived for %d meetings, %s through %s", len(history),
        min(h["date"] for h in history.values()) if history else None,
        max(h["date"] for h in history.values()) if history else None,
    )
    return history


def build_org_and_membership_rows(history: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_klub_names = sorted({klub for h in history.values() for klub in h["klub_by_person"].values()})
    source_url = standardize._LISTING_URL
    orgs = [
        {
            "id": f"usti-nad-labem:org:group:{standardize._slugify(klub)}",
            "name": klub,
            "classification": "group",
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps(
                [{"url": source_url, "note": f"usti.cz per-vote klub grouping (mode per meeting), klub {klub!r}"}],
                ensure_ascii=False,
            ),
        }
        for klub in all_klub_names
    ]

    ordered_meeting_nos = sorted(history.keys(), key=lambda mno: history[mno]["date"])
    open_interval: dict[tuple[str, str], str] = {}
    intervals: list[dict[str, Any]] = []
    prev_klub_by_person: dict[str, str] = {}

    for meeting_no in ordered_meeting_nos:
        date = history[meeting_no]["date"]
        current_klub_by_person = history[meeting_no]["klub_by_person"]

        all_people = set(prev_klub_by_person) | set(current_klub_by_person)
        for pk in all_people:
            prev_klub = prev_klub_by_person.get(pk)
            cur_klub = current_klub_by_person.get(pk)
            if cur_klub == prev_klub:
                continue
            if prev_klub is not None and (pk, prev_klub) in open_interval:
                intervals.append({"person_key": pk, "klub": prev_klub, "start": open_interval.pop((pk, prev_klub)), "end": date})
            if cur_klub is not None:
                open_interval[(pk, cur_klub)] = date

        prev_klub_by_person = current_klub_by_person

    for (pk, klub), start in open_interval.items():
        intervals.append({"person_key": pk, "klub": klub, "start": start, "end": ""})

    membership_rows = []
    for iv in sorted(intervals, key=lambda x: (x["person_key"], x["klub"], x["start"])):
        person_id = f"usti-nad-labem:person:{iv['person_key']}"
        org_id = f"usti-nad-labem:org:group:{standardize._slugify(iv['klub'])}"
        membership_rows.append(
            {
                "id": f"usti-nad-labem:membership:group:{iv['person_key']}:{standardize._slugify(iv['klub'])}:{iv['start']}",
                "person_id": person_id,
                "organization_id": org_id,
                "start_date": iv["start"],
                "end_date": iv["end"],
                "sources": json.dumps(
                    [
                        {
                            "url": source_url,
                            "note": (
                                "Derived from the mode klub value across each meeting's vote "
                                "events for this person. start/end mark the first/last meeting "
                                "observed under this klub; an open end_date means still in this "
                                "klub as of the last processed meeting."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return orgs, membership_rows


def apply(raw_dir: Path = _DEFAULT_RAW_DIR, data_dir: Path = _DEFAULT_DATA_DIR) -> dict[str, Any]:
    history = build_meeting_klub_history(raw_dir)
    orgs, memberships = build_org_and_membership_rows(history)

    org_path = data_dir / "organizations.csv"
    existing_orgs = pd.read_csv(org_path, dtype=str, keep_default_na=False)
    new_orgs_df = pd.DataFrame(orgs)
    kept_orgs = existing_orgs[~existing_orgs["id"].isin(set(new_orgs_df["id"]))]
    pd.concat([kept_orgs, new_orgs_df], ignore_index=True).to_csv(org_path, index=False, encoding="utf-8")
    logging.info("Wrote %s (%d existing + %d new = %d rows)", org_path, len(existing_orgs), len(new_orgs_df), len(kept_orgs) + len(new_orgs_df))

    mem_path = data_dir / "memberships.csv"
    existing_mems = pd.read_csv(mem_path, dtype=str, keep_default_na=False)
    new_mems_df = pd.DataFrame(memberships)
    kept_mems = existing_mems[~existing_mems["id"].isin(set(new_mems_df["id"]))]
    pd.concat([kept_mems, new_mems_df], ignore_index=True).to_csv(mem_path, index=False, encoding="utf-8")
    logging.info("Wrote %s (%d existing + %d new = %d rows)", mem_path, len(existing_mems), len(new_mems_df), len(kept_mems) + len(new_mems_df))

    return {"meetings_with_klub_data": len(history), "klub_count": len(orgs), "membership_interval_count": len(memberships)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = apply(Path(args.raw_dir), Path(args.data_dir))
    logging.info("Done: %s", report)


if __name__ == "__main__":
    main()
