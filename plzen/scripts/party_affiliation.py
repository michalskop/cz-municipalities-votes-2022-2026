"""Build Plzeň's real klub (assembly group) organizations + memberships from the cached vote
protocols (C4, mechanical part).

Source: standardize.py's own `resolve_all_events()` -- the SAME per-event klub text already
resolved while building votes.csv (see that file's module docstring's Scope boundary: klub text
is captured but not turned into organizations/memberships there; that's this script's job).

Unlike zastupko-network cities (Brno/Most), Plzeň's source has no separate dated
politickeSubjekty[]-style interval list -- klub affiliation is only ever observed PER VOTE. This
script derives dated intervals the same way ostrava/scripts/party_affiliation.py does: contiguous-
run detection over a per-MEETING (not per-vote) klub value, smoothing over the occasional
per-event parsing noise standardize.py's own module docstring documents (era3's rare unresolved
klub cell, kept as flagged raw/garbled text rather than fabricated) by taking the MODE (most
common) klub across all of a person's votes within one meeting, not any single vote's raw value.

Known klub-naming drift across the term (not glossed over): era1's very first meeting shows a
combined coalition list "ODS, KDU-ČSL, TOP 09" and "STAROSTOVÉ A NEZÁVISLÍ" where later
eras show these as separate/renamed klubs ("ODS", "KDU-ČSL", "TOP 09" and "STAN") -- each distinct
observed string becomes its own organization row here, mechanically, with no attempt to merge or
rename; that judgment call belongs to D7 (owner-approved government_groups research), same
precedent as Brno's ANO-club-split handling.
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


# Confirmed via the raw corpus (2026-08-28): every person who has an era1 "STAROSTOVÉ A
# NEZÁVISLÍ" interval transitions DIRECTLY (same date, no gap -- e.g. Aleš Tolar:
# 2022-10-18..2024-09-19 then 2024-09-19..) into "STAN", exactly at the era1/era2 protocol-format
# boundary -- a pure system relabeling artifact of that format change, not a real political event.
# Deliberately NOT merging "ODS, KDU-ČSL, TOP 09" (era1) into its era2/3 successors: that one is a
# REAL 3-way split (all 15 people land across ODS/KDU-ČSL/TOP 09 as 9/3/3, matching the "SPOLU"
# coalition's real dissolution reported in local news) -- not a single-entity rename.
_KLUB_RENAME_ALIASES = {"STAROSTOVÉ A NEZÁVISLÍ": "STAN"}


def _canonicalize_klub_names(raw_names: set[str]) -> dict[str, str]:
    """Merges case-variant spellings of the same real klub (confirmed real, not just an era3
    corruption artifact: "PRO PLZEŇ"/"Pro Plzeň" both genuinely appear verbatim in the source
    across different meetings) into one canonical spelling per casefold-group -- the most
    FREQUENTLY-shaped one wins by picking the alphabetically-first among the max-length ones is
    arbitrary, so instead: prefer the all-uppercase form (matches era2/3's general labeling
    convention), falling back to the first seen otherwise. Returns {raw_name: canonical_name}."""
    renamed = {name: _KLUB_RENAME_ALIASES.get(name, name) for name in raw_names}
    by_casefold: dict[str, list[str]] = {}
    for name in set(renamed.values()):
        by_casefold.setdefault(name.casefold(), []).append(name)
    case_canonical: dict[str, str] = {}
    for variants in by_casefold.values():
        best = max(variants, key=lambda v: (v == v.upper(), v))
        for v in variants:
            case_canonical[v] = best
    return {raw: case_canonical[renamed[raw]] for raw in raw_names}


def build_meeting_klub_history(raw_dir: Path) -> dict[int, dict[str, Any]]:
    """Returns {meeting_id: {"date": iso_date, "klub_by_person": {person_key: klub_name}}},
    one entry per meeting, using the MODE klub across that meeting's events for each person."""
    events, _persons, _report = standardize.resolve_all_events(raw_dir)

    all_klub_names_seen = {o["klub"] for e in events for o in e["options"]}
    canonical = _canonicalize_klub_names(all_klub_names_seen)

    by_meeting: dict[int, dict[str, Any]] = {}
    for e in events:
        if not e["date"]:
            continue
        m = by_meeting.setdefault(e["meeting_id"], {"date": e["date"], "votes": {}})
        m["date"] = min(m["date"], e["date"])
        for o in e["options"]:
            m["votes"].setdefault(o["person_key"], Counter())[canonical[o["klub"]]] += 1

    history: dict[int, dict[str, Any]] = {}
    for meeting_id, m in by_meeting.items():
        klub_by_person = {pk: counter.most_common(1)[0][0] for pk, counter in m["votes"].items()}
        history[meeting_id] = {"date": m["date"], "klub_by_person": klub_by_person}
    logging.info("Klub data derived for %d meetings, %s through %s", len(history),
                 min(h["date"] for h in history.values()) if history else None,
                 max(h["date"] for h in history.values()) if history else None)
    return history


def build_org_and_membership_rows(
    history: dict[int, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_klub_names = sorted({klub for h in history.values() for klub in h["klub_by_person"].values()})
    source_url_base = "https://usneseni.plzen.eu/"
    orgs = [
        {
            "id": f"plzen:org:group:{standardize._slugify(klub)}",
            "name": klub,
            "classification": "group",
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps(
                [{"url": source_url_base, "note": f"usneseni.plzen.eu per-vote klub grouping (mode per meeting), klub {klub!r}"}],
                ensure_ascii=False,
            ),
        }
        for klub in all_klub_names
    ]

    ordered_meeting_ids = sorted(history.keys(), key=lambda mid: history[mid]["date"])
    open_interval: dict[tuple[str, str], str] = {}  # (person_key, klub) -> start_date
    intervals: list[dict[str, Any]] = []
    prev_klub_by_person: dict[str, str] = {}

    for meeting_id in ordered_meeting_ids:
        date = history[meeting_id]["date"]
        current_klub_by_person = history[meeting_id]["klub_by_person"]

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
        person_id = f"plzen:person:{iv['person_key']}"
        org_id = f"plzen:org:group:{standardize._slugify(iv['klub'])}"
        membership_rows.append(
            {
                "id": f"plzen:membership:group:{iv['person_key']}:{standardize._slugify(iv['klub'])}:{iv['start']}",
                "person_id": person_id,
                "organization_id": org_id,
                "start_date": iv["start"],
                "end_date": iv["end"],
                "sources": json.dumps(
                    [
                        {
                            "url": "https://usneseni.plzen.eu/",
                            "note": (
                                "Derived from the mode klub value across each meeting's vote "
                                "events for this person (see build_meeting_klub_history). start/"
                                "end mark the first/last meeting observed under this klub; an "
                                "open end_date means still in this klub as of the last processed "
                                "meeting, not independently reconfirmed today."
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

    return {
        "meetings_with_klub_data": len(history),
        "klub_count": len(orgs),
        "membership_interval_count": len(memberships),
    }


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
