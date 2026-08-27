"""Build Ostrava's real klub (assembly group) organizations + memberships from the cached vote
pages (C4, mechanical part).

Source: the same cached raw/ pages standardize.py reads (see downloader.py) — no separate fetch.
Every vote page's per-person breakdown is grouped by klub under a `<th>` header (see
standardize.py's module docstring for why this can't be relied on per-vote: most votes omit it).
What DOES turn out to be reliable (confirmed 2026-08-27 across the full downloaded corpus): the
FIRST vote of a meeting has klub grouping whenever ANY vote in that meeting does, and this holds
for every meeting from 202201 through 202505 (24 of 31 meetings, 2022-10-19 through 2025-06-18) —
then the site stops publishing klub grouping entirely from meeting 202506 onward (the most recent
~7 meetings, roughly the last year of the term as of this writing). This script therefore reads
ONLY each meeting's first vote page, giving 24 real, precisely DATED klub-composition snapshots —
a genuinely dated history, not a single undated current-state fallback.

This dated history directly captures the real, cited, mid-term coalition event flagged in
ostrava/README.md: meeting 202302 (2023-02-22) still shows the original 8-klub structure; meeting
202303 (2023-03-22) shows a new transitional "Nezařazení" (unaffiliated) klub; meeting 202304
(2023-04-26) shows "Nezařazení" replaced by a new klub, "JDETO!!!" — bounding the ANO 2011 club
split (independently reported in the press as happening "in February 2023", see
govity_definition.json's citations once drafted) to a precise window directly from primary vote
data, not just news reporting.

Known, real, undocumented gap (not glossed over): no klub data exists for any vote from meeting
202506 onward — the most recent confirmed klub snapshot is 2025-06-18 (meeting 202505), over a
year old as of this writing. Memberships built here are therefore left OPEN-ENDED past that last
confirmed date (standard "last verified on DATE, presumed ongoing absent contrary evidence"
semantics — the same convention every other membership interval in this pipeline already uses for
its own "last observed" date), NOT because continuity through the gap is confirmed. See
`check_klub_staleness.py` (mirrors praha/scripts/check_roster_overlay_staleness.py) for the
non-blocking nightly reminder this gap needs a periodic re-check — it already fires today, since
the gap is already over a year old.

Explicitly NOT attempted here: reconciling this klub history against the live composition page's
"Zvolena za: X, nyní Y" field (ostrava.cz/.../slozeni-zastupitelstva-1). That field tracks
*party-membership status* ("nyní nestraník" = "now not a registered party member"), a materially
different concept from *klub* (assembly voting-group) affiliation — e.g. it shows former ANO
members Bajgarová/Macura as "nyní nestraník" with no klub name at all, while the vote-page data
independently shows them still voting as part of a real, named klub ("JDETO!!!") through the last
dated snapshot. Treating that page's party-status field as a klub-history source would conflate
two different things — flagged for the owner (see README) rather than silently merged.

Person-id consistency: imports `standardize`'s `_slugify`/`_split_deputy` so klub-membership rows
use the exact same person ids `standardize.py` already assigns.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standardize  # noqa: E402

_HEADER_RE = re.compile(r'<th nowrap=\"\"[^>]*>([^<]*)</th>')
_DATE_RE = re.compile(r"Dne\s*([\d.]+)")

# Normalizes every real-observed header variant to one canonical klub name. Confirmed by
# enumerating every distinct header string across all 24 dated meetings (2026-08-27) — the only
# variation is a cosmetic "klub " prefix added starting meeting 202302, and one single meeting
# (202307, 2023-09-20) that rendered four klub names abbreviated ("SPOLU"/"Starost."/"OLE") for
# that page only, verified NOT a real structural change (the same 9-klub set appears unabbreviated
# in the immediately preceding and following meetings, 202306 and 202308).
_KLUB_ALIASES = {
    "ano-2011": "ANO 2011",
    "ostravak": "Ostravak",
    "spd": "SPD",
    "ods-top09": "ODS + TOP09",
    "spolu": "ODS + TOP09",  # 202307-only abbreviation, verified not a real merge — see docstring
    "starostove-pro-ostravu": "STAROSTOVÉ pro OSTRAVU",
    "starost": "STAROSTOVÉ pro OSTRAVU",  # 202307-only abbreviation
    "kdu-csl": "KDU-ČSL",
    "ostravska-levice": "Ostravská levice",
    "ole": "Ostravská levice",  # 202307-only abbreviation
    "pirati": "Piráti",
    "nezarazeni": "Nezařazení",
    "jdeto": "JDETO!!!",
}


def _normalize_klub_name(raw: str) -> str:
    text = raw.strip()
    if text.lower().startswith("klub "):
        text = text[len("klub "):]
    key = standardize._slugify(text)
    if key not in _KLUB_ALIASES:
        raise ValueError(f"Unrecognized klub header {raw!r} (normalized key {key!r}) — refusing to guess.")
    return _KLUB_ALIASES[key]


def parse_meeting_klubs(raw_html: str) -> dict[str, list[dict[str, str]]]:
    """Returns {klub_name: [deputy_parts, ...]} for one meeting's first vote page."""
    header_matches = list(re.finditer(r'<th nowrap=\"\"[^>]*>([^<]*)</th>', raw_html))
    if not header_matches:
        return {}

    result: dict[str, list[dict[str, str]]] = {}
    for i, m in enumerate(header_matches):
        raw_name = m.group(1).strip()
        if not raw_name or "Pro:" in raw_name:
            continue  # the second <th> of each pair is the party's own vote tally, not a name
        klub = _normalize_klub_name(raw_name)
        start = m.end()
        end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(raw_html)
        segment = raw_html[start:end]
        deputies = [standardize._split_deputy(dm.group(1), dm.group(2)) for dm in standardize._DEPUTY_RE.finditer(segment)]
        result.setdefault(klub, []).extend(deputies)
    return result


def build_klub_history(raw_dir: Path) -> dict[str, dict[str, Any]]:
    """Returns {meeting_code: {"date": iso_date, "klubs": {klub_name: [person_key, ...]}}} for
    every meeting that has klub data on its first vote page."""
    manifest = json.loads((raw_dir / "meetings.json").read_text(encoding="utf-8"))
    history: dict[str, dict[str, Any]] = {}
    for code in sorted(manifest.keys()):
        first_vote = raw_dir / code / "0001.html"
        if not first_vote.exists():
            continue
        raw_html = first_vote.read_text(encoding="utf-8")
        klubs = parse_meeting_klubs(raw_html)
        if not klubs:
            continue
        date_match = _DATE_RE.search(raw_html)
        if not date_match:
            continue
        date_iso = standardize._parse_cz_date(date_match.group(1))
        klub_keys = {
            klub: [(standardize._slugify(d["given_name"]), standardize._slugify(d["family_name"])) for d in deps]
            for klub, deps in klubs.items()
        }
        history[code] = {"date": date_iso, "klubs": klub_keys}
    logging.info("Klub data found on %d/%d meeting(s), %s through %s", len(history), len(manifest), min(history.values(), key=lambda h: h["date"])["date"] if history else None, max(history.values(), key=lambda h: h["date"])["date"] if history else None)
    return history


def build_org_and_membership_rows(history: dict[str, dict[str, Any]], source_url_base: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_klub_names = sorted({klub for h in history.values() for klub in h["klubs"]})
    orgs = [
        {
            "id": f"ostrava:org:group:{standardize._slugify(klub)}",
            "name": klub,
            "classification": "group",
            "identifiers": json.dumps([], ensure_ascii=False),
            "sources": json.dumps(
                [{"url": source_url_base, "note": f"ostrava.cz vysledky_hlasovani per-vote klub grouping, klub {klub!r}"}],
                ensure_ascii=False,
            ),
        }
        for klub in all_klub_names
    ]

    # For each (person, klub) pair, find every contiguous run of meetings where that person was
    # observed under that klub, so a person who left and later rejoined the SAME klub (not
    # observed in this corpus, but the logic must not assume it can't happen) gets two intervals,
    # not one incorrectly spanning the gap.
    ordered_meetings = sorted(history.keys())
    person_klub_at: dict[tuple[str, str], str] = {}  # person_key -> klub at each meeting, filled per-loop
    intervals: list[dict[str, Any]] = []
    open_interval: dict[tuple[str, str], str] = {}  # (person_key, klub) -> start_date of the currently-open run

    last_meeting_seen: dict[tuple[str, str], str] = {}  # (person_key) -> last meeting_code where seen at all
    prev_klub_by_person: dict[tuple[str, str], str] = {}

    for code in ordered_meetings:
        date = history[code]["date"]
        current_klub_by_person: dict[tuple[str, str], str] = {}
        for klub, person_keys in history[code]["klubs"].items():
            for pk in person_keys:
                current_klub_by_person[pk] = klub

        all_people = set(prev_klub_by_person) | set(current_klub_by_person)
        for pk in all_people:
            prev_klub = prev_klub_by_person.get(pk)
            cur_klub = current_klub_by_person.get(pk)
            if cur_klub == prev_klub:
                continue  # unchanged (or still absent from klub data) — no interval boundary here
            if prev_klub is not None and (pk, prev_klub) in open_interval:
                intervals.append(
                    {"person_key": pk, "klub": prev_klub, "start": open_interval.pop((pk, prev_klub)), "end": date}
                )
            if cur_klub is not None:
                open_interval[(pk, cur_klub)] = date

        prev_klub_by_person = current_klub_by_person

    # Close out: anyone still in open_interval was in that klub as of the LAST dated snapshot —
    # left open-ended (see module docstring: "last confirmed, presumed ongoing").
    for (pk, klub), start in open_interval.items():
        intervals.append({"person_key": pk, "klub": klub, "start": start, "end": ""})

    membership_rows = []
    for iv in sorted(intervals, key=lambda x: (x["person_key"], x["klub"], x["start"])):
        given_slug, family_slug = iv["person_key"]
        person_id = f"ostrava:person:{given_slug}-{family_slug}"
        org_id = f"ostrava:org:group:{standardize._slugify(iv['klub'])}"
        membership_rows.append(
            {
                "id": f"ostrava:membership:group:{given_slug}-{family_slug}:{standardize._slugify(iv['klub'])}:{iv['start']}",
                "person_id": person_id,
                "organization_id": org_id,
                "start_date": iv["start"],
                "end_date": iv["end"],
                "sources": json.dumps(
                    [
                        {
                            "url": source_url_base,
                            "note": (
                                "Derived from the first vote page's klub grouping of each meeting "
                                "with klub data present (2022-10-19 through 2025-06-18; no klub "
                                "data published after that — see module docstring). start/end mark "
                                "the first/last meeting this person was observed under this klub; "
                                "an open end_date means still in this klub as of the last meeting "
                                "with any klub data (2025-06-18), NOT confirmed current today."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return orgs, membership_rows


def apply(raw_dir: Path = _DEFAULT_RAW_DIR, data_dir: Path = _DEFAULT_DATA_DIR) -> dict[str, Any]:
    history = build_klub_history(raw_dir)
    source_url_base = "https://www.ostrava.cz/uloziste/zastupitelstvo/vysledky_hlasovani/vo2226/"
    orgs, memberships = build_org_and_membership_rows(history, source_url_base)

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
        "last_confirmed_klub_date": max(h["date"] for h in history.values()) if history else None,
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
