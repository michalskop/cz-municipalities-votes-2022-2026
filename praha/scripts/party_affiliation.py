"""Source Praha councilors' candidate-list (party) affiliation from the official 2022 municipal
election results (volby.cz), per plan.md decision D7's standing rule.

D7 rule: prefer a live/current group-membership source when scrapable; if not scrapable (Praha's
current praha.eu/seznam-zastupitelu is a client-rendered SPA with no discoverable API — confirmed
dead-end, see plan.md C8 task description), fall back to each member's original candidate-list
(party) affiliation from the official election results. This is what this script does. Model it
honestly: candidate-list origin is `classification: "candidate_list"`, distinct from a live
`"group"`/klub (D7's own wording, echoed in the org rows this script writes).

Source: volby.cz's 2022 municipal election results for Praha (xobec=554782, xnumnuts=1100).
  - Aggregate list results: kv1111?...&xnumnuts=1100&xobec=554782...
  - Per-list candidate results: kv111111?...&xstrana=<id>...  ("Mandát" column: literal "*" for
    elected candidates — the "přepočtené pořadí" (recalculated order after preference votes) can
    differ from the original list order, so `*` is the only reliable elected marker.)

Encoding note (deviation from the pre-session assumption in plan.md's C8 task text): the site now
redirects www.volby.cz -> volby.gov.cz and serves `Content-Type: text/html;charset=utf-8` (verified
via response headers 2026-08-04), not windows-1250/cp1250. Decoded as UTF-8 accordingly; if this
regresses, the `_fetch` guard below will raise loudly on non-UTF-8 mojibake rather than silently
garbling diacritics.

Matching approach: volby.cz's "příjmení, jméno, tituly" free-text field doesn't split cleanly into
(family_name, given_name) on its own (e.g. "Freitas Lopesová Zuzana Mgr." has a two-word family
name). Instead of parsing that string in isolation, this script uses `praha/data/persons.csv`
(already split into given_name/family_name by C7's standardizer, see standardize.py's
`_parse_person_header`) as ground truth: a candidate row matches a person iff BOTH the person's
normalized given_name and normalized family_name appear as whole tokens in the normalized
candidate string. Every match decision (clean / ambiguous / no match) is logged — see
`build_affiliations` and the JSON report this script writes to work/reports/.

Known limitation handled explicitly, not silently: mid-term substitute councilors (elected
candidates in C7's persons.csv who never appear as `*` on any list page, because they entered
after the 2022 election as the next-in-line candidate replacing a departing member) cannot be
resolved by this script's list-scrape alone. These are handled via `_KNOWN_SUBSTITUTES`, a small,
explicitly cited table — never guessed silently. See that table's docstring for Praha's one
instance (Tomáš Kaněra replacing Martin Hrubčík, ANO 2011, per prazskypatriot.cz).
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import requests

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA_DIR = _CITY_ROOT / "data"
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw" / "volby"
_DEFAULT_REPORT = _CITY_ROOT / "work" / "reports" / "party_affiliation_report.json"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cz-cities-research-bot/1.0)"}

_XOBEC = "554782"
_XNUMNUTS = "1100"
_AGGREGATE_URL = (
    "https://www.volby.cz/pls/kv2022/kv1111"
    f"?xjazyk=CZ&xid=1&xdz=4&xnumnuts={_XNUMNUTS}&xobec={_XOBEC}&xstat=0&xvyber=0"
)
_CANDIDATE_URL_TMPL = (
    "https://www.volby.cz/pls/kv2022/kv111111"
    f"?xjazyk=CZ&xid=1&xdz=4&xnumnuts={_XNUMNUTS}&xobec={_XOBEC}"
    "&xstrana={xstrana}&xstat=0&xvyber=0"
)

_AGGREGATE_ROW_RE = re.compile(
    r'<td class="cislo"[^>]*>(\d+)</td>\s*'
    r"<td[^>]*>(.*?)</td>\s*"
    r'<td class="cislo"[^>]*>([\d&nbsp;]*)</td>\s*'
    r'<td class="cislo"[^>]*>([\d,]*)</td>\s*'
    r'<td class="cislo"[^>]*>([\d&nbsp;]*)</td>\s*'
    r'<td class="cislo"[^>]*>([\d,\.&nbsp;]*)</td>\s*'
    r'<td class="cislo"[^>]*>([\d,]*)</td>\s*'
    r'<td[^>]*>([\d&nbsp;]*)</td>\s*'
    r'<td[^>]*>(X|-)</td>',
    re.S,
)
_AGGREGATE_LINK_RE = re.compile(r'xstrana=(\d+)[^"]*">([^<]+)</a>')

_CANDIDATE_ROW_RE = re.compile(
    r'<td class="cislo" headers="sa1 sb1">(\d+)</td>\s*'
    r'<td headers="sa1 sb2">(.*?)</td>\s*'
    r'<td class="cislo" headers="sa2 sb3">[^<]*</td>\s*'
    r'<td class="cislo" headers="sa2 sb4">[^<]*</td>\s*'
    r'<td class="cislo" headers="sa3">([^<]*)</td>\s*'
    r'<td[^>]*headers="sa4">([^<]*)</td>',
    re.S,
)

# Explicitly cited, manually verified mid-term substitute -> candidate-list mappings that cannot
# be derived from the list-scrape alone (the substitute was never marked "*" on any list page,
# since they entered the assembly after the election, not at it). Never add to this table without
# a citation — an unexplained/uncertain substitute must be left unmatched and logged instead.
_KNOWN_SUBSTITUTES: dict[str, dict[str, Any]] = {
    "praha:person:tomas-kanera": {
        "org_key": "ANO 2011",
        "note": (
            "Tomáš Kaněra (mayor of Praha 22) was list position #15 on ANO 2011's 2022 candidate "
            "list (i.e. the first non-elected/'náhradník' candidate) — confirmed by this script's "
            "own scrape of the ANO 2011 candidate page. He took his assembly oath 2025-01-23 "
            "(matches the start_date C7 already derived in memberships.csv), replacing Martin "
            "Hrubčík (ANO 2011), whose own membership ended 2024-12-12 per the same data. "
            "Cross-confirmed via news: 'Novým pražským zastupitelem za ANO se stal starosta Prahy "
            "22 Tomáš Kaněra', https://www.prazskypatriot.cz/"
            "novym-prazskym-zastupitelem-za-ano-se-stal-starosta-prahy-22-tomas-kanera/ "
            "(accessed 2026-08-04)."
        ),
        "sources": [
            {
                "url": "https://www.prazskypatriot.cz/novym-prazskym-zastupitelem-za-ano-se-stal-starosta-prahy-22-tomas-kanera/",
                "note": "News confirmation of Kaněra replacing Hrubčík as ANO 2011's Prague councilor.",
            },
            {
                "url": _CANDIDATE_URL_TMPL.format(xstrana=768),
                "note": "volby.cz ANO 2011 candidate list, row 15 = Kaněra Tomáš (not elected in 2022, i.e. first substitute in line).",
            },
        ],
    }
}


def _fetch(url: str) -> str:
    logging.info("Fetching %s", url)
    resp = requests.get(url, headers=_HEADERS, timeout=60)
    resp.raise_for_status()
    if resp.encoding and resp.encoding.lower() not in ("utf-8", "utf8"):
        logging.warning(
            "Unexpected response encoding %r for %s — forcing utf-8 (see module docstring)",
            resp.encoding,
            url,
        )
    resp.encoding = "utf-8"
    text = resp.text
    if "�" in text or "Ă" in text:
        raise ValueError(
            f"Decoded content for {url} looks garbled (mojibake) — the site's encoding may have "
            "changed again. Refusing to proceed with corrupted Czech text."
        )
    return text


def _clean_int(raw: str) -> int:
    raw = raw.replace("&nbsp;", "").replace("\xa0", "").strip()
    return int(raw) if raw else 0


def parse_aggregate(html: str) -> list[dict[str, Any]]:
    """Parse the aggregate list-results page into one row per candidate list."""
    # xstrana ids + list names appear as anchor hrefs alongside each list's row; the row's other
    # cells (votes, mandates) are plain <td> siblings within the same <tr>. Parse row-by-row.
    rows: list[dict[str, Any]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        link = re.search(r"xstrana=(\d+)", tr)
        if not link:
            continue
        xstrana = int(link.group(1))
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        cells_clean = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", "").strip() for c in cells]
        if len(cells_clean) < 8:
            continue
        list_number = cells_clean[0]
        list_name = cells_clean[1]
        mandates_raw = cells_clean[7]
        try:
            list_number_int = int(list_number)
            mandates = int(mandates_raw) if mandates_raw else 0
        except ValueError:
            continue
        rows.append(
            {
                "xstrana": xstrana,
                "list_number": list_number_int,
                "list_name": list_name,
                "mandates": mandates,
            }
        )
    if not rows:
        raise ValueError("Parsed zero list rows from the aggregate page — page structure may have changed.")
    return rows


def parse_candidate_list(html: str) -> list[dict[str, Any]]:
    """Parse a per-list candidate-results page. Returns all rows; `elected` marks the '*' rows."""
    matches = _CANDIDATE_ROW_RE.findall(html)
    if not matches:
        raise ValueError("Parsed zero candidate rows — page structure may have changed.")
    rows = []
    for poradi, raw_name, recalc_order, mandat in matches:
        name = re.sub(r"<[^>]+>", "", raw_name).replace("&nbsp;", " ").strip()
        rows.append(
            {
                "poradi": int(poradi),
                "raw_name": name,
                "recalc_order": recalc_order.strip() or None,
                "elected": mandat.strip() == "*",
            }
        )
    return rows


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.lower()


def _slugify(text: str) -> str:
    text = _normalize(text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def load_persons(data_dir: Path) -> list[dict[str, str]]:
    with open(data_dir / "persons.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_memberships_index(data_dir: Path) -> dict[str, dict[str, str]]:
    """person_id -> {start_date, end_date} for their existing assembly membership."""
    idx: dict[str, dict[str, str]] = {}
    with open(data_dir / "memberships.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            idx[row["person_id"]] = {
                "start_date": row.get("start_date") or "",
                "end_date": row.get("end_date") or "",
            }
    return idx


def match_candidate_to_person(raw_name: str, persons: list[dict[str, str]]) -> list[dict[str, str]]:
    cand_words = set(re.split(r"[\s.,]+", _normalize(raw_name)))
    cand_words.discard("")
    matches = []
    for p in persons:
        gn = _normalize(p["given_name"])
        fn = _normalize(p["family_name"])
        if gn in cand_words and fn in cand_words:
            matches.append(p)
    return matches


def build_affiliations(
    data_dir: Path,
    raw_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    persons = load_persons(data_dir)
    membership_idx = load_memberships_index(data_dir)

    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    aggregate_html = _fetch(_AGGREGATE_URL)
    if raw_dir:
        (raw_dir / "kv1111_aggregate.html").write_text(aggregate_html, encoding="utf-8")

    list_rows = parse_aggregate(aggregate_html)
    seat_winning = [r for r in list_rows if r["mandates"] > 0]
    total_seats = sum(r["mandates"] for r in seat_winning)
    logging.info(
        "Aggregate page: %d lists total, %d won seats, %d total seats",
        len(list_rows),
        len(seat_winning),
        total_seats,
    )
    for r in seat_winning:
        logging.info(
            "  list #%d %r: %d seat(s) (xstrana=%d)",
            r["list_number"],
            r["list_name"],
            r["mandates"],
            r["xstrana"],
        )

    organizations: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    matched_person_ids: set[str] = set()
    log: dict[str, Any] = {
        "seat_table": seat_winning,
        "total_seats": total_seats,
        "matches": [],
        "unmatched_candidates": [],
        "ambiguous_candidates": [],
        "substitutes": [],
        "unmatched_persons": [],
    }
    org_key_to_id: dict[str, str] = {}

    for list_row in seat_winning:
        list_name = list_row["list_name"]
        xstrana = list_row["xstrana"]
        org_slug = _slugify(list_name)
        org_id = f"praha:org:candidate-list:{org_slug}"
        org_key_to_id[list_name] = org_id

        candidate_html = _fetch(_CANDIDATE_URL_TMPL.format(xstrana=xstrana))
        if raw_dir:
            (raw_dir / f"kv111111_xstrana_{xstrana}.html").write_text(candidate_html, encoding="utf-8")

        candidate_rows = parse_candidate_list(candidate_html)
        elected_rows = [r for r in candidate_rows if r["elected"]]
        logging.info(
            "List %r (xstrana=%d): %d candidates parsed, %d elected (expected %d seats)",
            list_name,
            xstrana,
            len(candidate_rows),
            len(elected_rows),
            list_row["mandates"],
        )
        if len(elected_rows) != list_row["mandates"]:
            raise ValueError(
                f"List {list_name!r} (xstrana={xstrana}): parsed {len(elected_rows)} elected "
                f"candidates but the aggregate page reports {list_row['mandates']} seats. "
                "Refusing to proceed with a seat-count mismatch."
            )

        organizations.append(
            {
                "id": org_id,
                "name": list_name,
                "classification": "candidate_list",
                "identifiers": json.dumps(
                    [{"scheme": "volby.cz:xstrana", "identifier": str(xstrana)}],
                    ensure_ascii=False,
                ),
                "sources": json.dumps(
                    [
                        {
                            "url": _AGGREGATE_URL,
                            "note": f"2022 municipal election, list #{list_row['list_number']} ({list_row['mandates']} seats).",
                        },
                        {
                            "url": _CANDIDATE_URL_TMPL.format(xstrana=xstrana),
                            "note": "Per-list candidate results, elected candidates marked '*'.",
                        },
                    ],
                    ensure_ascii=False,
                ),
            }
        )

        for row in elected_rows:
            matches = match_candidate_to_person(row["raw_name"], persons)
            if len(matches) == 1:
                person = matches[0]
                matched_person_ids.add(person["id"])
                mem = membership_idx.get(person["id"], {"start_date": "", "end_date": ""})
                memberships.append(
                    {
                        "id": f"praha:membership:{person['id'].split(':')[-1]}:{org_id}",
                        "person_id": person["id"],
                        "organization_id": org_id,
                        "start_date": mem["start_date"],
                        "end_date": mem["end_date"],
                        "sources": json.dumps(
                            [
                                {
                                    "url": _CANDIDATE_URL_TMPL.format(xstrana=xstrana),
                                    "note": (
                                        f"Elected candidate on {list_name!r} (list poradi="
                                        f"{row['poradi']}); start/end mirror this person's "
                                        "zastupitelstvo membership interval (data/memberships.csv)."
                                    ),
                                }
                            ],
                            ensure_ascii=False,
                        ),
                    }
                )
                log["matches"].append(
                    {
                        "status": "matched",
                        "list": list_name,
                        "raw_name": row["raw_name"],
                        "person_id": person["id"],
                    }
                )
                logging.info("  MATCHED  %-40s -> %s", row["raw_name"], person["id"])
            elif len(matches) == 0:
                log["unmatched_candidates"].append({"list": list_name, "raw_name": row["raw_name"]})
                logging.warning(
                    "  UNMATCHED %-40s (list %r) — no persons.csv entry found; NOT written to "
                    "memberships.csv. Needs manual review.",
                    row["raw_name"],
                    list_name,
                )
            else:
                log["ambiguous_candidates"].append(
                    {
                        "list": list_name,
                        "raw_name": row["raw_name"],
                        "candidate_person_ids": [m["id"] for m in matches],
                    }
                )
                logging.error(
                    "  AMBIGUOUS %-40s (list %r) matched %d persons.csv entries: %s — refusing "
                    "to guess, NOT written to memberships.csv.",
                    row["raw_name"],
                    list_name,
                    len(matches),
                    [m["id"] for m in matches],
                )

    # Explicitly cited mid-term substitutes (see _KNOWN_SUBSTITUTES docstring above).
    for person_id, sub in _KNOWN_SUBSTITUTES.items():
        person = next((p for p in persons if p["id"] == person_id), None)
        if person is None:
            logging.warning("Known substitute %s not found in persons.csv — skipping.", person_id)
            continue
        org_id = org_key_to_id.get(sub["org_key"])
        if org_id is None:
            logging.warning(
                "Known substitute %s references org_key %r which is not a seat-winning list this "
                "run — skipping.",
                person_id,
                sub["org_key"],
            )
            continue
        mem = membership_idx.get(person_id, {"start_date": "", "end_date": ""})
        memberships.append(
            {
                "id": f"praha:membership:{person_id.split(':')[-1]}:{org_id}",
                "person_id": person_id,
                "organization_id": org_id,
                "start_date": mem["start_date"],
                "end_date": mem["end_date"],
                "sources": json.dumps(sub["sources"], ensure_ascii=False),
            }
        )
        matched_person_ids.add(person_id)
        log["substitutes"].append({"person_id": person_id, "org_id": org_id, "note": sub["note"]})
        logging.info("  SUBSTITUTE %s -> %s (%s)", person_id, org_id, sub["org_key"])

    unmatched_persons = [p for p in persons if p["id"] not in matched_person_ids]
    for p in unmatched_persons:
        log["unmatched_persons"].append({"id": p["id"], "name": p["name"]})
        logging.warning("Person %s (%s) has NO candidate-list affiliation resolved.", p["id"], p["name"])

    log["summary"] = {
        "persons_total": len(persons),
        "persons_matched": len(matched_person_ids),
        "persons_unmatched": len(unmatched_persons),
        "organizations_written": len(organizations),
        "memberships_written": len(memberships),
    }
    logging.info(
        "Summary: %d/%d persons matched to a candidate-list org, %d unmatched, %d org rows, %d membership rows",
        len(matched_person_ids),
        len(persons),
        len(unmatched_persons),
        len(organizations),
        len(memberships),
    )

    return organizations, memberships, log


def write_outputs(
    data_dir: Path,
    new_organizations: list[dict[str, Any]],
    new_memberships: list[dict[str, Any]],
) -> None:
    """Append this script's rows to organizations.csv/memberships.csv.

    C6 ordering note: the nightly workflow always runs this script immediately after
    `praha/pipelines/run_pipeline.py`, whose `standardize.py` step rewrites both CSVs from
    scratch each run (see run_pipeline.py's module docstring) — so in normal operation
    `existing_*` here never already contains this script's own `praha:org:candidate-list:*`/
    matching-membership rows, and a plain append is correct and safe to run once per nightly
    cycle. The dedup-by-id below is a defensive second layer for the *abnormal* case (e.g. someone
    re-runs just this script twice in a row without an intervening standardize.py pass, such as a
    manual debug session) — it drops any pre-existing row whose id collides with a row this run is
    about to write, so re-running never silently doubles the candidate-list rows.
    """
    org_path = data_dir / "organizations.csv"
    existing_orgs = pd.read_csv(org_path, dtype=str, keep_default_na=False)
    new_orgs_df = pd.DataFrame(new_organizations)
    new_org_ids = set(new_orgs_df["id"]) if not new_orgs_df.empty else set()
    kept_orgs = existing_orgs[~existing_orgs["id"].isin(new_org_ids)]
    dropped_org_dupes = len(existing_orgs) - len(kept_orgs)
    if dropped_org_dupes:
        logging.warning(
            "%s: dropped %d pre-existing row(s) whose id collided with this run's output "
            "(re-run without an intervening standardize.py pass?) before re-appending them.",
            org_path,
            dropped_org_dupes,
        )
    combined_orgs = pd.concat([kept_orgs, new_orgs_df], ignore_index=True)
    combined_orgs.to_csv(org_path, index=False, encoding="utf-8")
    logging.info(
        "Wrote %s (%d existing + %d new - %d deduped = %d rows)",
        org_path,
        len(existing_orgs),
        len(new_orgs_df),
        dropped_org_dupes,
        len(combined_orgs),
    )

    mem_path = data_dir / "memberships.csv"
    existing_mems = pd.read_csv(mem_path, dtype=str, keep_default_na=False)
    new_mems_df = pd.DataFrame(new_memberships)
    new_mem_ids = set(new_mems_df["id"]) if not new_mems_df.empty else set()
    kept_mems = existing_mems[~existing_mems["id"].isin(new_mem_ids)]
    dropped_mem_dupes = len(existing_mems) - len(kept_mems)
    if dropped_mem_dupes:
        logging.warning(
            "%s: dropped %d pre-existing row(s) whose id collided with this run's output "
            "(re-run without an intervening standardize.py pass?) before re-appending them.",
            mem_path,
            dropped_mem_dupes,
        )
    combined_mems = pd.concat([kept_mems, new_mems_df], ignore_index=True)
    combined_mems.to_csv(mem_path, index=False, encoding="utf-8")
    logging.info(
        "Wrote %s (%d existing + %d new - %d deduped = %d rows)",
        mem_path,
        len(existing_mems),
        len(new_mems_df),
        dropped_mem_dupes,
        len(combined_mems),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=str(_DEFAULT_DATA_DIR))
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR), help="where to save fetched HTML for reproducibility")
    parser.add_argument("--report-out", default=str(_DEFAULT_REPORT))
    parser.add_argument("--dry-run", action="store_true", help="parse and log only, do not write CSVs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    data_dir = Path(args.data_dir)
    raw_dir = Path(args.raw_dir) if args.raw_dir else None

    organizations, memberships, log = build_affiliations(data_dir, raw_dir)

    report_path = Path(args.report_out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote match-decision report to %s", report_path)

    if args.dry_run:
        logging.info("--dry-run given: not writing organizations.csv/memberships.csv")
        return

    write_outputs(data_dir, organizations, memberships)


if __name__ == "__main__":
    main()
