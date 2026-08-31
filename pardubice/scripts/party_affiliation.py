"""Build Pardubice's real klub (assembly group) organizations + memberships from the cached voting
PDFs (C4, mechanical part).

Source: standardize.py's own resolve_all_events() — the per-event klub text already captured while
building votes.csv. No separate dated interval feed exists (like Plzeň / Ústí / most-rada); klub
affiliation is only ever observed per vote. Dated intervals are derived by contiguous-run
detection over a per-MEETING mode klub per person.

CANONICALIZATION — Pardubice's raw klub labels carry a lot of cosmetic drift that would otherwise
produce dozens of one-meeting alternating "intervals". Each mapping below was verified as
same-person, zero date gap across the label change (the Plzeň discontinuity-merge check), and each
is a label-form difference (case / word order / abbreviation / coalition-member prefix), NOT a
real klub switch:

  bare "SPOLU"  and  "ODS/SPOLU" / "TOP 09/SPOLU" / "KDU-ČSL/SPOLU"   -> "SPOLU"
     (every SPOLU councillor is labelled bare "SPOLU" for meetings 2-5, then gains the
      member-party prefix from meeting 6; SPOLU is the klub, the prefix names the person's party)
  "nezávislá/SPD"  /  "Nezávislá/SPD"                                 -> "Nezávislá/SPD"   (case)
  "SPD/Trikolora"  /  "Trikolora/SPD"  (and bare "SPD" *for Janda*)   -> "SPD"
     (Trikolora is a coalition-partner label on the SPD klub, same pattern as the SPOLU prefixes;
      one councillor, Janda, toggles the three forms meeting to meeting with no gap)
  "Progres. Pard."                                                    -> "Progresivní Pce"  (abbrev)

Two judgment calls are DEFERRED to D7 owner review (see the report's open_questions; nothing here
is silently resolved):
  - "Piráti" is renamed "Progresivní Pce" from meeting 38 (2026-05-25). Kept here as TWO adjacent
    klub orgs with a clean 2026-05-25 boundary (rename, not split — to be confirmed).
  - Robert Hrdina (sole "Zelení") is labelled "Zelení" / "Zelení/Piráti" in alternating meetings,
    then "Progres. Pard." from meeting 38. Treated here as a member of the Piráti klub throughout
    (-> "Piráti", then "Progresivní Pce"); flagged for confirmation.
  - Helena Dvořáčková is kept as a one-person "Nezávislá/SPD" klub (independent elected on the SPD
    list), distinct from the SPD klub proper; flagged.
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

# raw klub label -> canonical klub. Anything not listed passes through unchanged.
_CANON = {
    "SPOLU": "SPOLU",
    "ODS/SPOLU": "SPOLU",
    "TOP 09/SPOLU": "SPOLU",
    "KDU-ČSL/SPOLU": "SPOLU",
    "nezávislá/SPD": "Nezávislá/SPD",
    "Nezávislá/SPD": "Nezávislá/SPD",
    "SPD/Trikolora": "SPD",
    "Trikolora/SPD": "SPD",
    "Progres. Pard.": "Progresivní Pce",
    # deferred-but-applied (see module docstring / report open_questions):
    "Zelení": "Piráti",
    "Zelení/Piráti": "Piráti",
}


def _canon(klub: str) -> str:
    return _CANON.get(klub.strip(), klub.strip())


def build_meeting_klub_history(raw_dir: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Counter]]:
    """{meeting_no: {"date": iso, "klub_by_person": {pk: canon_klub}}} using the mode raw klub per
    meeting per person, then canonicalized. Also returns raw-label usage counts for the report."""
    events, _persons, _report = standardize.resolve_all_events(raw_dir)

    by_meeting: dict[int, dict[str, Any]] = {}
    raw_usage: dict[str, Counter] = {}
    for e in events:
        m = by_meeting.setdefault(e["meeting_no"], {"date": e["date"], "votes": {}})
        m["date"] = min(m["date"], e["date"])
        for o in e["options"]:
            m["votes"].setdefault(o["person_key"], Counter())[o["klub"]] += 1

    history: dict[int, dict[str, Any]] = {}
    for meeting_no, m in by_meeting.items():
        klub_by_person = {}
        for pk, counter in m["votes"].items():
            raw_mode = counter.most_common(1)[0][0]
            raw_usage.setdefault(raw_mode, Counter())[_canon(raw_mode)] += 1
            klub_by_person[pk] = _canon(raw_mode)
        history[meeting_no] = {"date": m["date"], "klub_by_person": klub_by_person}
    return history, raw_usage


def build_org_and_membership_rows(history: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_klub_names = sorted({k for h in history.values() for k in h["klub_by_person"].values()})
    src = standardize._SOURCE_URL
    orgs = [
        {
            "id": f"pardubice:org:group:{standardize._slugify(klub)}",
            "name": klub,
            "classification": "group",
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps(
                [{"url": src, "note": f"pardubice.eu per-vote klub column (mode per meeting, canonicalized), klub {klub!r}"}],
                ensure_ascii=False,
            ),
        }
        for klub in all_klub_names
    ]

    ordered = sorted(history.keys(), key=lambda mno: history[mno]["date"])
    open_interval: dict[tuple[str, str], str] = {}
    intervals: list[dict[str, Any]] = []
    prev: dict[str, str] = {}
    for meeting_no in ordered:
        date = history[meeting_no]["date"]
        cur = history[meeting_no]["klub_by_person"]
        for pk in set(prev) | set(cur):
            pk_prev, pk_cur = prev.get(pk), cur.get(pk)
            if pk_cur == pk_prev:
                continue
            if pk_prev is not None and (pk, pk_prev) in open_interval:
                intervals.append({"person_key": pk, "klub": pk_prev, "start": open_interval.pop((pk, pk_prev)), "end": date})
            if pk_cur is not None:
                open_interval[(pk, pk_cur)] = date
        prev = cur
    for (pk, klub), start in open_interval.items():
        intervals.append({"person_key": pk, "klub": klub, "start": start, "end": ""})

    membership_rows = []
    for iv in sorted(intervals, key=lambda x: (x["person_key"], x["klub"], x["start"])):
        membership_rows.append(
            {
                "id": f"pardubice:membership:group:{iv['person_key']}:{standardize._slugify(iv['klub'])}:{iv['start']}",
                "person_id": f"pardubice:person:{iv['person_key']}",
                "organization_id": f"pardubice:org:group:{standardize._slugify(iv['klub'])}",
                "start_date": iv["start"],
                "end_date": iv["end"],
                "sources": json.dumps(
                    [{"url": src, "note": (
                        "Derived from the mode klub label across each meeting's vote events for this "
                        "person, canonicalized (see party_affiliation.py). start/end mark the first/"
                        "last meeting observed under this klub; an open end_date means still in it as "
                        "of the last processed meeting.")}],
                    ensure_ascii=False,
                ),
            }
        )
    return orgs, membership_rows


_OPEN_QUESTIONS = [
    "Piráti -> 'Progresivní Pce' rename from meeting 38 (2026-05-25): modeled as two adjacent klub "
    "orgs with a 2026-05-25 boundary. Confirm this is a rename of one continuous klub, not a split.",
    "Robert Hrdina (sole 'Zelení', labelled 'Zelení'/'Zelení/Piráti' alternately, then 'Progres. "
    "Pard.' from m38) is modeled as a Piráti-klub member throughout. Confirm.",
    "Helena Dvořáčková kept as a one-person 'Nezávislá/SPD' klub (independent on the SPD list), "
    "distinct from the SPD klub. Confirm, or fold into SPD.",
    "'Trikolora/SPD' / 'SPD/Trikolora' folded into 'SPD' as a coalition-partner label (same "
    "treatment as the X/SPOLU prefixes). Confirm.",
]


def apply(raw_dir: Path = _DEFAULT_RAW_DIR, data_dir: Path = _DEFAULT_DATA_DIR, report_path: Path = _DEFAULT_REPORT) -> dict[str, Any]:
    history, raw_usage = build_meeting_klub_history(raw_dir)
    orgs, memberships = build_org_and_membership_rows(history)

    org_path = data_dir / "organizations.csv"
    existing_orgs = pd.read_csv(org_path, dtype=str, keep_default_na=False)
    new_orgs_df = pd.DataFrame(orgs)
    kept = existing_orgs[~existing_orgs["id"].isin(set(new_orgs_df["id"]))]
    pd.concat([kept, new_orgs_df], ignore_index=True).to_csv(org_path, index=False, encoding="utf-8")
    logging.info("organizations.csv: %d existing + %d klub = %d rows", len(kept), len(new_orgs_df), len(kept) + len(new_orgs_df))

    mem_path = data_dir / "memberships.csv"
    existing_mems = pd.read_csv(mem_path, dtype=str, keep_default_na=False)
    new_mems_df = pd.DataFrame(memberships)
    kept_m = existing_mems[~existing_mems["id"].isin(set(new_mems_df["id"]))]
    pd.concat([kept_m, new_mems_df], ignore_index=True).to_csv(mem_path, index=False, encoding="utf-8")
    logging.info("memberships.csv: %d existing + %d klub = %d rows", len(kept_m), len(new_mems_df), len(kept_m) + len(new_mems_df))

    report = {
        "klubs": [o["name"] for o in orgs],
        "klub_count": len(orgs),
        "membership_interval_count": len(memberships),
        "raw_label_to_canonical": {raw: list(canons.keys())[0] for raw, canons in sorted(raw_usage.items())},
        "raw_label_meeting_usage": {raw: sum(c.values()) for raw, c in sorted(raw_usage.items())},
        "open_questions_for_owner": _OPEN_QUESTIONS,
        "approval_status": "APPROVED by project owner 2026-08-31 (D7) — all four open questions accepted as modeled; see pardubice/analyses/govity/govity_definition.json's resolved_questions.",
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
    report = apply(Path(args.raw_dir), Path(args.data_dir), Path(args.report_out))
    logging.info("klubs: %s", report["klubs"])
    logging.info("open questions for owner: %d", len(report["open_questions_for_owner"]))


if __name__ == "__main__":
    main()
