"""Build České Budějovice's real klub (assembly group) organizations + memberships from the cached
vote pages (C4, mechanical part).

Source: standardize.py's own resolve_all_events() — the per-vote `Klub` column already captured
while building votes.csv. No separate dated roster feed exists (like Pardubice / Plzeň / Ústí);
klub affiliation is only ever observed per vote. Dated intervals are derived by contiguous-run
detection over a per-MEETING mode klub per person.

UNLIKE Pardubice, České Budějovice's 10 raw klub labels are already clean — no case / spacing /
word-order / coalition-prefix variants — so there is NO canonicalisation map here. Every label
passes through as-is.

The klub changes in the data are GENUINE mid-term political events, not label noise:
  - The entire ODS klub (~14 members incl. primátor Kuba) is labelled "ODS" from the term start,
    then "NEZAŘAZENÍ" from meeting 2025026 (2025-12-15), then "NAŠE ČESKO" from meeting 2026029
    (2026-05-11) — the ODS klub left and re-formed as NAŠE ČESKO after a ~5-month unaffiliated gap.
  - 3 SPD members (Stierandová, Kroutilová, Cisler) go "SPD" -> "NEZÁVISLÍ" at meeting 2025020
    (2025-03-24).
Both are captured as real dated interval boundaries. The government-side implication (does
government_groups need to follow ODS -> NAŠE ČESKO?) is a D7 question for the owner — see the
report's open_questions_for_owner.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"
_DEFAULT_REPORT = _CITY_ROOT / "work" / "reports" / "party_affiliation_report.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standardize  # noqa: E402


def build_meeting_klub_history(raw_dir: Path) -> dict[str, dict[str, Any]]:
    events, _persons, _report = standardize.resolve_all_events(raw_dir)
    by_meeting: dict[str, dict[str, Any]] = {}
    for e in events:
        m = by_meeting.setdefault(e["meeting_number"], {"date": e["date"], "votes": {}})
        if e["date"]:
            m["date"] = min(m["date"] or e["date"], e["date"])
        for o in e["options"]:
            m["votes"].setdefault(o["person_key"], Counter())[o["klub"]] += 1
    history: dict[str, dict[str, Any]] = {}
    for mno, m in by_meeting.items():
        history[mno] = {"date": m["date"], "klub_by_person": {pk: c.most_common(1)[0][0] for pk, c in m["votes"].items()}}
    return history


def build_org_and_membership_rows(history: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_klubs = sorted({k for h in history.values() for k in h["klub_by_person"].values()})
    src = standardize._PORTAL
    orgs = [
        {
            "id": f"ceske-budejovice:org:group:{standardize._slugify(k)}",
            "name": k, "classification": "group",
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps([{"url": src, "note": f"c-budejovice.cz per-vote klub column (mode per meeting), klub {k!r}"}], ensure_ascii=False),
        }
        for k in all_klubs
    ]

    ordered = sorted(history.keys(), key=lambda mno: history[mno]["date"] or mno)
    open_iv: dict[tuple[str, str], str] = {}
    intervals: list[dict[str, Any]] = []
    prev: dict[str, str] = {}
    for mno in ordered:
        date = history[mno]["date"]
        cur = history[mno]["klub_by_person"]
        for pk in set(prev) | set(cur):
            p, c = prev.get(pk), cur.get(pk)
            if p == c:
                continue
            if p is not None and (pk, p) in open_iv:
                intervals.append({"person_key": pk, "klub": p, "start": open_iv.pop((pk, p)), "end": date})
            if c is not None:
                open_iv[(pk, c)] = date
        prev = cur
    for (pk, k), start in open_iv.items():
        intervals.append({"person_key": pk, "klub": k, "start": start, "end": ""})

    rows = []
    for iv in sorted(intervals, key=lambda x: (x["person_key"], x["klub"], x["start"])):
        rows.append({
            "id": f"ceske-budejovice:membership:group:{iv['person_key']}:{standardize._slugify(iv['klub'])}:{iv['start']}",
            "person_id": f"ceske-budejovice:person:{iv['person_key']}",
            "organization_id": f"ceske-budejovice:org:group:{standardize._slugify(iv['klub'])}",
            "start_date": iv["start"], "end_date": iv["end"],
            "sources": json.dumps([{"url": src, "note": (
                "Derived from the mode klub label across each meeting's vote events for this "
                "person; start/end mark the first/last meeting observed under this klub, an open "
                "end_date means still in it as of the last processed meeting.")}], ensure_ascii=False),
        })
    return orgs, rows


_OPEN_QUESTIONS = [
    "government_groups for govity/wpca: the ODS klub (primátor Kuba's) is 'ODS' until meeting "
    "2025026 (2025-12-15), then 'NEZAŘAZENÍ', then 'NAŠE ČESKO' from meeting 2026029 (2026-05-11). "
    "This project's schema supports only ONE static government_groups list. Confirm whether to (a) "
    "use ceske-budejovice:org:group:ods for the whole term (wrong for the last ~7 months), (b) use "
    "all three ods + nezarazeni + nase-cesko, or (c) something else. Same class as Hradec "
    "Králové's RH problem.",
    "3 ex-SPD members (Stierandová, Kroutilová, Cisler) become 'NEZÁVISLÍ' at meeting 2025020 "
    "(2025-03-24). Opposition either way — confirm this doesn't affect government_groups.",
]


def apply(raw_dir: Path = _DEFAULT_RAW_DIR, data_dir: Path = _DEFAULT_DATA_DIR, report_path: Path = _DEFAULT_REPORT) -> dict[str, Any]:
    history = build_meeting_klub_history(raw_dir)
    orgs, memberships = build_org_and_membership_rows(history)

    org_path = data_dir / "organizations.csv"
    existing = pd.read_csv(org_path, dtype=str, keep_default_na=False)
    new_df = pd.DataFrame(orgs)
    kept = existing[~existing["id"].isin(set(new_df["id"]))]
    pd.concat([kept, new_df], ignore_index=True).to_csv(org_path, index=False, encoding="utf-8")
    logging.info("organizations.csv: %d existing + %d klub = %d rows", len(kept), len(new_df), len(kept) + len(new_df))

    mem_path = data_dir / "memberships.csv"
    existing_m = pd.read_csv(mem_path, dtype=str, keep_default_na=False)
    new_m = pd.DataFrame(memberships)
    kept_m = existing_m[~existing_m["id"].isin(set(new_m["id"]))]
    pd.concat([kept_m, new_m], ignore_index=True).to_csv(mem_path, index=False, encoding="utf-8")
    logging.info("memberships.csv: %d existing + %d klub = %d rows", len(kept_m), len(new_m), len(kept_m) + len(new_m))

    report = {
        "klubs": [o["name"] for o in orgs], "klub_count": len(orgs),
        "membership_interval_count": len(memberships),
        "open_questions_for_owner": _OPEN_QUESTIONS,
        "approval_status": "PENDING",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote %s", report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    parser.add_argument("--report-out", default=str(_DEFAULT_REPORT))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    r = apply(Path(args.raw_dir), Path(args.data_dir), Path(args.report_out))
    logging.info("klubs: %s | %d intervals | %d open questions", r["klubs"], r["membership_interval_count"], len(r["open_questions_for_owner"]))


if __name__ == "__main__":
    main()
