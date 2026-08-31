"""Standardize České Budějovice's VOATT "Jak se hlasovalo" HTML pages into dt-standard tables (C2).

Source shape: ceske-budejovice/config/sources.yml (research pass 2026-08-31 — see this repo's
memory: ceske-budejovice-source-research). Plain-GET HTML tables, one consistent format across the
whole term, no auth, no encoding issues.

Per meeting file (work/raw/meeting_<mid>.html): a table of rows
  `Bod č. NN. | <cislo>. | <title> | <link ?bod=voteId>`.
Per vote file (work/raw/votes/<mid>_<voteId>.html): the first `table-stripped sticky-enabled`
table has 4 columns `Zastupitel | Klub | Hlasoval | Hlasování klubu`, one row per councillor
(45). The 4th column (the person's klub's collective vote) is not used.

The per-vote page has NO explicit přijato/nepřijato outcome and NO aggregate tally, so `result` is
DERIVED: pass iff the count of "Hlasoval pro" >= 23 — nadpoloviční většina všech 45 členů
zastupitelstva (§ 87 zákona č. 128/2000 Sb.). Recorded as derived in every vote_event's sources
note, not presented as coming from the portal. There is therefore no G2 self-consistency
cross-check against a published tally (none exists) — G2 here is roster completeness (row count
per event vs the 45-member council) + vote-vocabulary coverage, the same "self-consistency is
acceptable when no independent aggregate exists" stance as Ostrava's sources.yml.

Name order is Czech "GivenName FamilyName(s)" (opposite of Pardubice/Ústí): strip a leading run of
academic-title tokens (any word ending in ".", e.g. "doc.", "Dr.", "Ing.", "Mgr.") and a trailing
", <credential>" suffix, then first word = given name, the rest = family name.
"""
from __future__ import annotations

import argparse
import html as html_module
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "ceske-budejovice:org:zastupitelstvo-mesta-ceske-budejovice"
ORG_NAME = "Zastupitelstvo statutárního města České Budějovice"
_PORTAL = "https://www.c-budejovice.cz"
COUNCIL_SIZE = 45
_PASS_THRESHOLD = 23  # nadpoloviční většina všech 45 členů, § 87 zákona č. 128/2000 Sb.

_OPTION_MAP = {
    "Hlasoval pro": "yes",
    "Hlasoval proti": "no",
    "Zdržel se hlasování": "abstain",
    "Byl přítomen a nehlasoval": "not voting",
    "Nebyl přítomen": "absent",
}

_MEETING_VOTE_ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>\s*Bod č\.\s*([^<]+?)\s*</td>\s*"  # bod label, e.g. "00." or "20.01."
    r"<td[^>]*>\s*(\d+)\.\s*</td>\s*"                          # cislo, "1." .. "47."
    r"<td[^>]*>(.*?)</td>\s*"                                  # title (may contain &nbsp;)
    r"<td[^>]*>.*?[?&]bod=(\d+).*?</td>\s*</tr>",              # link with ?bod=<voteId>
    re.S,
)
_TABLE_RE = re.compile(r'<table[^>]*class="[^"]*sticky-enabled[^"]*"[^>]*>(.*?)</table>', re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_HEADING_RE = re.compile(
    r"Výsledek hlasování k bodu číslo\s*([0-9A-Za-z.]+?)\.?\s*"
    r"(?:\(tisk\s*([^)]+)\))?\s*,\s*zasedání číslo\s*(\d{7})",
    re.S,
)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


_TITLE_TOKEN_RE = re.compile(r"^[A-Za-zÁ-Žá-ž]{1,6}\.$")


def _split_name(full: str) -> tuple[str, str]:
    """'doc. Dr. Ing. Dagmar Škodová Parmo, Ph.D.' -> ('Dagmar', 'Škodová Parmo'). Czech order is
    GivenName FamilyName(s); strip a leading run of dotted title tokens and a trailing ', cred'."""
    full = full.split(",")[0].strip()
    parts = full.split()
    while len(parts) > 2 and _TITLE_TOKEN_RE.match(parts[0]):
        parts.pop(0)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _person_key(given: str, family: str) -> str:
    return _slugify(f"{given}-{family}")


def parse_meeting_votes(meeting_html: str) -> list[dict[str, str]]:
    """[{"bod": "00", "cislo": "1", "title": "...", "vote_id": "19605"}, ...] in page order."""
    out = []
    for bod, cislo, title, vid in _MEETING_VOTE_ROW_RE.findall(meeting_html):
        out.append({"bod": bod.strip().rstrip("."), "cislo": cislo, "title": _clean(title), "vote_id": vid})
    return out


def parse_vote_page(vote_html: str) -> dict[str, Any]:
    """Returns {"bod_number", "tisk", "meeting_number", "rows": [{"name","klub","option_raw"}]}."""
    hm = _HEADING_RE.search(vote_html)
    meta = {
        "bod_number": hm.group(1).strip(".") if hm else None,
        "tisk": (hm.group(2).strip() if hm and hm.group(2) else None),
        "meeting_number": hm.group(3) if hm else None,
    }

    rows: list[dict[str, str]] = []
    for table_body in _TABLE_RE.findall(vote_html):
        table_rows: list[dict[str, str]] = []
        for tr in _TR_RE.findall(table_body):
            tds = _TD_RE.findall(tr)
            if len(tds) != 4:
                continue  # the per-klub table has 2 cells; the header row has <th> not <td>
            name, klub, vote, _klub_vote = (_clean(x) for x in tds)
            if name:
                table_rows.append({"name": name, "klub": klub, "option_raw": vote})
        if table_rows:
            rows = table_rows  # the first sticky-enabled table with 4-col <td> rows is per-person
            break
    return {**meta, "rows": rows}


def resolve_all_events(raw_dir: Path = _DEFAULT_RAW_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "total_meetings": 0, "total_events": 0,
        "unmapped_options": [], "skipped_failed_fetch": 0,
        "roster_vs_council": {"match": 0, "mismatch": 0}, "roster_mismatches": [],
        "heading_meeting_mismatch": [],
    }
    persons: dict[str, dict[str, str]] = {}
    events: list[dict[str, Any]] = []

    for m in manifest["meetings"]:
        if m.get("index_fetch_failed"):
            logging.warning("Meeting %s: page fetch failed upstream — skipping", m["number"])
            continue
        report["total_meetings"] += 1
        mid = m["meeting_id"]
        meeting_html = (raw_dir / f"meeting_{mid}.html").read_text(encoding="utf-8")
        vote_list = parse_meeting_votes(meeting_html)
        vote_meta = {v["vote_id"]: v for v in vote_list}

        # A "block vote" is one physical hlasování applied to several agenda items: the meeting
        # page lists N rows sharing one Číslo hlasování, each with its own ?bod= id, and every one
        # of those ?bod= pages returns the SAME combined table (45 councillors x N items, votes
        # identical across a person's N rows). Collapse to one event per (meeting, cislo), using
        # the first ?bod= id for that cislo and de-tiling the table to one row per councillor.
        cislo_to_first_vid: dict[str, str] = {}
        cislo_titles: dict[str, list[str]] = {}
        for v in vote_list:
            cislo_titles.setdefault(v["cislo"], []).append(v["title"])
            cislo_to_first_vid.setdefault(v["cislo"], v["vote_id"])

        crawled = set(m["votes"])
        for cislo, vid in cislo_to_first_vid.items():
            if vid not in crawled:
                # fall back to any crawled ?bod= id sharing this cislo (they're interchangeable)
                vid = next((v["vote_id"] for v in vote_list if v["cislo"] == cislo and v["vote_id"] in crawled), None)
                if vid is None:
                    report["skipped_failed_fetch"] += 1
                    continue
            vpath = raw_dir / "votes" / f"{mid}_{vid}.html"
            if not vpath.exists():
                report["skipped_failed_fetch"] += 1
                continue
            parsed = parse_vote_page(vpath.read_text(encoding="utf-8"))
            meta = vote_meta.get(vid, {})
            titles = cislo_titles.get(cislo, [meta.get("title", "")])
            title = titles[0] + (f"  (blok {len(titles)} bodů)" if len(titles) > 1 else "")
            report["total_events"] += 1

            # de-tile a block-vote table (N*45 rows, each councillor repeated N times, identical)
            rows = parsed["rows"]
            if rows and len(rows) % COUNCIL_SIZE == 0 and len(rows) > COUNCIL_SIZE:
                seen_names: set[str] = set()
                deduped = [r for r in rows if not (r["name"] in seen_names or seen_names.add(r["name"]))]
                if len(deduped) == COUNCIL_SIZE:
                    rows = deduped
            parsed = {**parsed, "rows": rows}
            cislo = str(cislo)

            if parsed["meeting_number"] and parsed["meeting_number"] != m["number"]:
                report["heading_meeting_mismatch"].append(
                    {"meeting": m["number"], "vote_id": vid, "heading_says": parsed["meeting_number"]}
                )

            options: list[dict[str, Any]] = []
            counts = {"yes": 0, "no": 0, "abstain": 0, "absent": 0, "not voting": 0}
            for r in parsed["rows"]:
                opt = _OPTION_MAP.get(r["option_raw"])
                if opt is None:
                    report["unmapped_options"].append(
                        {"meeting": m["number"], "vote_id": vid, "option_raw": r["option_raw"], "name": r["name"]}
                    )
                    logging.warning("Meeting %s vote %s: unmapped %r for %s — skipped", m["number"], vid, r["option_raw"], r["name"])
                    continue
                given, family = _split_name(r["name"])
                key = _person_key(given, family)
                persons.setdefault(key, {"given_name": given, "family_name": family})
                counts[opt] += 1
                options.append({"person_key": key, "option": opt, "klub": r["klub"]})

            n_rows = len(options) + sum(1 for r in parsed["rows"] if _OPTION_MAP.get(r["option_raw"]) is None)
            if n_rows == COUNCIL_SIZE:
                report["roster_vs_council"]["match"] += 1
            else:
                report["roster_vs_council"]["mismatch"] += 1
                report["roster_mismatches"].append({"meeting": m["number"], "vote_id": vid, "rows": n_rows})

            result = "pass" if counts["yes"] >= _PASS_THRESHOLD else "fail"
            events.append(
                {
                    "meeting_number": m["number"], "cislo": int(cislo), "date": m["date"],
                    "bod": meta.get("bod"), "tisk": parsed["tisk"],
                    "title": title, "result": result, "counts": counts, "options": options,
                }
            )

    events.sort(key=lambda e: (e["date"] or "", e["meeting_number"], e["cislo"]))
    return events, persons, report


def standardize(raw_dir: Path = _DEFAULT_RAW_DIR, out_dir: Path = _DEFAULT_OUT) -> dict[str, Any]:
    events, persons, report = resolve_all_events(raw_dir)

    global_max_date = max((e["date"] for e in events if e["date"]), default=None)
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        if not e["date"]:
            continue
        for o in e["options"]:
            k = o["person_key"]
            first_seen[k] = min(first_seen.get(k, e["date"]), e["date"])
            last_seen[k] = max(last_seen.get(k, e["date"]), e["date"])

    person_rows, memberships = [], []
    for key in sorted(persons):
        if key not in first_seen:
            continue
        p = persons[key]
        pid = f"ceske-budejovice:person:{key}"
        person_rows.append({
            "id": pid, "name": f"{p['given_name']} {p['family_name']}".strip(),
            "given_name": p["given_name"], "family_name": p["family_name"],
            "identifiers": "[]",
            "sources": json.dumps([{"url": _PORTAL, "note": "c-budejovice.cz 'Jak se hlasovalo' portal"}], ensure_ascii=False),
        })
        end_date = "" if last_seen[key] == global_max_date else last_seen[key]
        memberships.append({
            "id": f"ceske-budejovice:membership:{key}:zastupitelstvo-mesta-ceske-budejovice",
            "person_id": pid, "organization_id": ORG_ID,
            "start_date": first_seen[key], "end_date": end_date,
            "sources": json.dumps([{"url": _PORTAL, "note": "start/end derived from first/last recorded vote"}], ensure_ascii=False),
        })

    organization = {
        "id": ORG_ID, "name": ORG_NAME, "classification": "assembly", "identifiers": "[]",
        "sources": json.dumps([{"url": _PORTAL, "note": "c-budejovice.cz"}], ensure_ascii=False),
    }

    votes_rows, vote_events, motions = [], [], []
    for e in events:
        ek = f"{e['meeting_number']}-{e['cislo']}"
        veid = f"ceske-budejovice:vote-event:{ek}"
        mid_ = f"ceske-budejovice:motion:{ek}"
        for o in e["options"]:
            votes_rows.append({"vote_event_id": veid, "voter_id": f"ceske-budejovice:person:{o['person_key']}", "voter_type": "person", "option": o["option"]})
        ident = f"{e['meeting_number']}/{e['cislo']}"
        note = (f"meeting {e['meeting_number']}, hlasování {e['cislo']}"
                + (f", tisk {e['tisk']}" if e["tisk"] else "")
                + f"; result DERIVED (pass iff >=%d 'Hlasoval pro', § 87 zákona č. 128/2000 Sb.) — the portal publishes no outcome field" % _PASS_THRESHOLD)
        src = [{"url": _PORTAL, "note": note}]
        vote_events.append({
            "id": veid, "identifier": ident, "motion_id": mid_, "organization_id": ORG_ID,
            "start_date": e["date"], "result": e["result"],
            "counts": [{"option": opt, "value": e["counts"][opt]} for opt in ("yes", "no", "abstain", "absent", "not voting")],
            "sources": src, "extras": {},
        })
        motions.append({
            "id": mid_, "identifier": ident, "organization_id": ORG_ID, "date": e["date"],
            "text": e["title"], "result": e["result"], "sources": src, "extras": {},
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(person_rows).fillna("").to_csv(out_dir / "persons.csv", index=False, encoding="utf-8")
    pd.DataFrame([organization]).fillna("").to_csv(out_dir / "organizations.csv", index=False, encoding="utf-8")
    pd.DataFrame(memberships).fillna("").to_csv(out_dir / "memberships.csv", index=False, encoding="utf-8")
    pd.DataFrame(votes_rows).to_csv(out_dir / "votes.csv", index=False, encoding="utf-8")
    (out_dir / "vote_events.json").write_text(json.dumps(vote_events, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out_dir / "motions.json").write_text(json.dumps(motions, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    logging.info("persons=%d memberships=%d votes=%d vote_events=%d", len(person_rows), len(memberships), len(votes_rows), len(vote_events))
    logging.info("roster vs %d-member council: %d match / %d mismatch", COUNCIL_SIZE,
                 report["roster_vs_council"]["match"], report["roster_vs_council"]["mismatch"])
    logging.info("unmapped options=%d, skipped(failed fetch)=%d, heading/meeting mismatches=%d",
                 len(report["unmapped_options"]), report["skipped_failed_fetch"], len(report["heading_meeting_mismatch"]))
    report.update(persons_count=len(person_rows), memberships_count=len(memberships),
                  votes_count=len(votes_rows), vote_events_count=len(vote_events), motions_count=len(motions))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = standardize(Path(args.raw_dir), Path(args.out_dir))
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
