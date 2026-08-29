"""Standardize Ústí nad Labem's ZM roll-call vote PDFs into dt-standard tables.

Source shape fully documented in usti-nad-labem/config/sources.yml (research pass 2026-08-29 —
see this repo's memory: usti-nad-labem-source-research). Summary: the cleanest source of any city
built so far — one plain-text PDF per MEETING (not per vote), no login, no session cookies, no
format drift across the whole term, no font-encoding corruption (confirmed across 5 samples
spread through the term during research). Extracted via `pdftotext -layout` (poppler-utils, a
required system dependency).

Parsing approach: each meeting PDF bundles multiple "Hlasování č.N" blocks; each block has a Bod
(agenda item) title (sometimes wrapping multiple lines), a result line ("Přijato usnesení" /
"Nepřijato usnesení"), a totals line (Hlasoval/Pro/Proti/Zdržel se/Nehlasoval), and repeated
per-klub sections (klub name + its own (Pro/Proti/Zdržel se) subtotal, then "FamilyName GivenName:
<vote>" pairs in a 3-column layout with no structural HTML markup — reconstructed via regex on
whitespace-delimited text, not column position, since (unlike Plzeň's PDFs) there's no font-
encoding corruption to work around, just plain multi-column text layout).

Person name split: NO structural marker exists here (unlike zastupko-network cities' JSON or
Plzeň's era2 HTML `<b>` tags) to disambiguate family/given name for compound names. Uses the
standard Czech "last word = given name, everything before = family name" heuristic — correct for
the vast majority of real samples checked, but a compound family name (e.g. two-word surnames)
could in principle mis-split. Not verified against an independent source; flagged here rather than
silently assumed perfect.

Vote option vocabulary (see sources.yml): "Pro" (yes), "Proti" (no), "Zdržel se" (abstain), a
BLANK vote (no text after the colon) for someone listed but ABSENT (confirmed via roster-count
cross-check against the totals line's "Hlasoval" figure), and "Nehlasoval" (not voting) which
appears as a named field in every totals line but was never observed as a real per-person value
during research -- kept in the vocabulary map on the assumption it uses the same literal string,
verified properly by this file's own full-corpus run (see the unmapped-value report).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "usti-nad-labem:org:zastupitelstvo-mesta-usti-nad-labem"
ORG_NAME = "Zastupitelstvo města Ústí nad Labem"
_LISTING_URL = "https://www.usti.cz/cz/uredni-portal/sprava-mesta/mesto-jeho-organy/zastupitelstvo-mesta/zapisy-z-jednani-zm.html"

_OPTION_MAP = {
    "Pro": "yes",
    "Proti": "no",
    "Zdržel se": "abstain",
    "Nehlasoval": "not voting",
    None: "absent",  # a blank vote (no text after the colon) -- see module docstring
}

# Tolerant of the exact city-name wording between "Zastupitelstva" and "ze dne" -- confirmed real
# variants in the corpus: most meetings say "města Ústí nad Labem", one says "Statutárního města
# Ústí nad Labem", and one has a genuine source typo ("Ústí nas Labem"). Matching only the meeting
# number and date (never assumed the middle text was fixed after finding the first variant).
_MEETING_HEADER_RE = re.compile(r"(\d+)\. ?zasedání Zastupitelstva[^\n]*? ze dne (\d{2})\.(\d{2})\.(\d{4})")
_VOTE_SPLIT_RE = re.compile(r"Hlasování č\.(\d+)")
# Agenda-item headings have more format variance than any other field in this source, confirmed
# across the full 24-meeting corpus (not assumed from one sample): "Bod N: Title" (colon, the
# common case), "Bod N. Title" (period), "Body N-M: Title" (plural, a merged/block vote across
# several agenda items), "Body: <range description>" (plural, no number token at all), and at
# least one real occurrence of a colon INSIDE the identifier itself ("Bod 0b:p Blažej ...",
# apparently a mangled "protinávrh p. Blažej"). Splitting a clean bod_number out from the title
# reliably isn't possible across all these shapes without guessing -- so this deliberately does
# NOT try; the whole "Bod"/"Body"-prefixed text up to the result line is kept as one combined
# `title` field instead, sourced-in-place rather than fabricating a split the data doesn't support.
_BOD_RE = re.compile(r"Bod(?:y)?\b\s*(.*?)\n\s*\n\s*(Přijato usnesení|Nepřijato usnesení)", re.S)
_TOTALS_RE = re.compile(
    r"Hlasoval: *(\d+) *Pro: *(\d+) *Proti: *(\d+) *Zdržel se: *(\d+) *Nehlasoval: *(\d+)"
)
# Leading whitespace before the klub name varies by meeting (some blocks indent it, others
# don't) -- confirmed across the corpus, tolerate both rather than anchor to column 0.
_KLUB_RE = re.compile(
    r"^[ \t]*([A-Za-zÁ-Žá-ž0-9!.]+(?: [A-Za-zÁ-Žá-ž0-9!.]+)*) \(Pro: *(\d+) *, *Proti: *(\d+) *, *Zdržel se: *(\d+)\)[ \t]*$",
    re.M,
)
_PAIR_RE = re.compile(
    r"([A-ZÁ-Ž][\wá-žÁ-Ž]*(?:\s+[A-ZÁ-Ž][\wá-žÁ-Ž]*)*?):[ \t]*(Pro|Proti|Zdržel se|Nehlasoval)?(?=\s{2,}|\n|\Z)"
)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _pdftotext_layout(raw: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(raw)
        tmp.flush()
        result = subprocess.run(["pdftotext", "-layout", tmp.name, "-"], capture_output=True, check=True)
    return result.stdout.decode("utf-8")


def _split_name(full_name: str) -> tuple[str, str]:
    """'FamilyName GivenName' -> (given, family), last-word-is-given heuristic (see module
    docstring: no structural marker exists to do this reliably for compound names)."""
    parts = full_name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def parse_meeting_pdf(raw: bytes) -> dict[str, Any]:
    text = _pdftotext_layout(raw)

    header = _MEETING_HEADER_RE.search(text)
    if not header:
        raise ValueError("Meeting header not found -- page shape changed?")
    meeting_no = int(header.group(1))
    date = f"{header.group(4)}-{header.group(3)}-{header.group(2)}"

    # Split into per-"Hlasování č.N" blocks; each block runs until the next split marker.
    split_points = list(_VOTE_SPLIT_RE.finditer(text))
    events: list[dict[str, Any]] = []
    for i, sm in enumerate(split_points):
        vote_no = int(sm.group(1))
        start = sm.end()
        end = split_points[i + 1].start() if i + 1 < len(split_points) else len(text)
        block = text[start:end]

        bod_m = _BOD_RE.search(block)
        if not bod_m:
            raise ValueError(f"Meeting {meeting_no} vote {vote_no}: Bod/result block not found")
        bod_title = re.sub(r"\s+", " ", bod_m.group(1)).strip()
        result = "pass" if bod_m.group(2) == "Přijato usnesení" else "fail"

        totals_m = _TOTALS_RE.search(block)
        if not totals_m:
            raise ValueError(f"Meeting {meeting_no} vote {vote_no}: totals line not found")
        totals = {
            "hlasoval": int(totals_m.group(1)),
            "pro": int(totals_m.group(2)),
            "proti": int(totals_m.group(3)),
            "zdrzel": int(totals_m.group(4)),
            "nehlasoval": int(totals_m.group(5)),
        }

        klub_matches = list(_KLUB_RE.finditer(block))
        votes: list[dict[str, Any]] = []
        for ki, km in enumerate(klub_matches):
            klub = km.group(1)
            seg_start = km.end()
            seg_end = klub_matches[ki + 1].start() if ki + 1 < len(klub_matches) else len(block)
            segment = block[seg_start:seg_end]
            for pm in _PAIR_RE.finditer(segment):
                full_name = re.sub(r"\s+", " ", pm.group(1)).strip()
                votes.append({"full_name": full_name, "klub": klub, "option_raw": pm.group(2)})

        events.append(
            {
                "meeting_no": meeting_no,
                "vote_no": vote_no,
                "date": date,
                "title": bod_title,
                "result": result,
                "totals": totals,
                "votes": votes,
            }
        )

    return {"meeting_no": meeting_no, "date": date, "events": events}


# ── Orchestration ────────────────────────────────────────────────────────────────────────────
def _person_key(given: str, family: str) -> str:
    return _slugify(f"{given}-{family}")


def resolve_all_events(raw_dir: Path = _DEFAULT_RAW_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    """Parses every cached meeting PDF and resolves each vote into a unified per-event structure.
    Returns (events, persons, report). Each event's "options" list carries "klub" per person (not
    just "option") -- mirrors most-rada/plzen's resolve_all_events shape, reused by a later C4
    party_affiliation.py the same way."""
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "total_meetings": 0,
        "total_events": 0,
        "unmapped_options": [],
        "count_consistency": {"match": 0, "mismatch": 0},
        "count_mismatches": [],
    }

    persons: dict[str, dict[str, str]] = {}
    events: list[dict[str, Any]] = []

    for m in manifest["meetings"]:
        pdf_path = raw_dir / "pdfs" / m["pdf_file"]
        raw = pdf_path.read_bytes()
        parsed = parse_meeting_pdf(raw)
        report["total_meetings"] += 1

        for e in parsed["events"]:
            report["total_events"] += 1
            options: list[dict[str, Any]] = []
            counts = {"yes": 0, "no": 0, "abstain": 0, "absent": 0, "not voting": 0}
            for v in e["votes"]:
                if v["option_raw"] not in _OPTION_MAP:
                    report["unmapped_options"].append(
                        {"meeting_no": e["meeting_no"], "vote_no": e["vote_no"], "option_raw": v["option_raw"], "full_name": v["full_name"]}
                    )
                    logging.warning(
                        "Meeting %d vote %d: unmapped vote option %r for %s -- skipped, not fabricated",
                        e["meeting_no"], e["vote_no"], v["option_raw"], v["full_name"],
                    )
                    continue
                option = _OPTION_MAP[v["option_raw"]]
                given, family = _split_name(v["full_name"])
                key = _person_key(given, family)
                persons.setdefault(key, {"given_name": given, "family_name": family})
                counts[option] += 1
                options.append({"person_key": key, "option": option, "klub": v["klub"]})

            # G2: this event's recomputed per-option counts must match the source's own totals
            # line exactly -- see run_pipeline.py's module docstring for why this is a hard gate
            # here (a mismatch means the regex-based column reconstruction is unreliable), not a
            # bounded-tolerance check like other cities' supermajority/quorum exceptions.
            t = e["totals"]
            present = counts["yes"] + counts["no"] + counts["abstain"] + counts["not voting"]
            if (
                counts["yes"] != t["pro"]
                or counts["no"] != t["proti"]
                or counts["abstain"] != t["zdrzel"]
                or counts["not voting"] != t["nehlasoval"]
                or present != t["hlasoval"]
            ):
                report["count_consistency"]["mismatch"] += 1
                report["count_mismatches"].append(
                    {"meeting_no": e["meeting_no"], "vote_no": e["vote_no"], "computed": dict(counts), "totals": t}
                )
            else:
                report["count_consistency"]["match"] += 1

            events.append(
                {
                    "meeting_no": e["meeting_no"],
                    "vote_no": e["vote_no"],
                    "date": e["date"],
                    "title": e["title"],
                    "result": e["result"],
                    "counts": counts,
                    "options": options,
                }
            )

    events.sort(key=lambda e: (e["date"], e["meeting_no"], e["vote_no"]))
    return events, persons, report


def standardize(raw_dir: Path = _DEFAULT_RAW_DIR, out_dir: Path = _DEFAULT_OUT) -> dict[str, Any]:
    events, persons, report = resolve_all_events(raw_dir)

    # ── Build persons.csv / organizations.csv / memberships.csv ────────────────────────────────
    global_max_date = max((e["date"] for e in events), default=None)
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        for o in e["options"]:
            k = o["person_key"]
            first_seen[k] = min(first_seen.get(k, e["date"]), e["date"])
            last_seen[k] = max(last_seen.get(k, e["date"]), e["date"])

    person_rows = []
    memberships = []
    for key in sorted(persons):
        if key not in first_seen:
            continue
        p = persons[key]
        person_id = f"usti-nad-labem:person:{key}"
        person_rows.append(
            {
                "id": person_id,
                "name": f"{p['given_name']} {p['family_name']}".strip(),
                "given_name": p["given_name"],
                "family_name": p["family_name"],
                "identifiers": "[]",
                "sources": json.dumps(
                    [{"url": _LISTING_URL, "note": "usti.cz ZM vote-protocol PDFs"}], ensure_ascii=False
                ),
            }
        )
        end_date = "" if last_seen[key] == global_max_date else last_seen[key]
        memberships.append(
            {
                "id": f"usti-nad-labem:membership:{key}:{ORG_ID.split(':', 2)[2]}",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": first_seen[key],
                "end_date": end_date,
                "sources": json.dumps(
                    [{"url": _LISTING_URL, "note": "start/end derived from first/last recorded vote"}],
                    ensure_ascii=False,
                ),
            }
        )

    organization = {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": "[]",
        "sources": json.dumps([{"url": _LISTING_URL, "note": "usti.cz"}], ensure_ascii=False),
    }

    # ── Build votes.csv / vote_events.json / motions.json ───────────────────────────────────────
    votes_rows = []
    vote_events = []
    motions = []
    for e in events:
        event_key = f"{e['meeting_no']}-{e['vote_no']}"
        vote_event_id = f"usti-nad-labem:vote-event:{event_key}"
        motion_id = f"usti-nad-labem:motion:{event_key}"
        for o in e["options"]:
            votes_rows.append(
                {"vote_event_id": vote_event_id, "voter_id": f"usti-nad-labem:person:{o['person_key']}", "voter_type": "person", "option": o["option"]}
            )

        identifier = f"{e['meeting_no']}/{e['vote_no']}"
        sources = [{"url": _LISTING_URL, "note": f"meeting {e['meeting_no']}, hlasovani {e['vote_no']}"}]
        vote_events.append(
            {
                "id": vote_event_id,
                "identifier": identifier,
                "motion_id": motion_id,
                "organization_id": ORG_ID,
                "start_date": e["date"],
                "result": e["result"],
                "counts": [{"option": opt, "value": e["counts"][opt]} for opt in ("yes", "no", "abstain", "absent", "not voting")],
                "sources": sources,
                "extras": {},
            }
        )
        motions.append(
            {
                "id": motion_id,
                "identifier": identifier,
                "organization_id": ORG_ID,
                "date": e["date"],
                "text": e["title"],
                "result": e["result"],
                "sources": sources,
                "extras": {},
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(person_rows).fillna("").to_csv(out_dir / "persons.csv", index=False, encoding="utf-8")
    pd.DataFrame([organization]).fillna("").to_csv(out_dir / "organizations.csv", index=False, encoding="utf-8")
    pd.DataFrame(memberships).fillna("").to_csv(out_dir / "memberships.csv", index=False, encoding="utf-8")
    pd.DataFrame(votes_rows).to_csv(out_dir / "votes.csv", index=False, encoding="utf-8")
    (out_dir / "vote_events.json").write_text(json.dumps(vote_events, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out_dir / "motions.json").write_text(json.dumps(motions, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    logging.info("Wrote persons.csv (%d rows)", len(person_rows))
    logging.info("Wrote organizations.csv (1 row)")
    logging.info("Wrote memberships.csv (%d rows)", len(memberships))
    logging.info("Wrote votes.csv (%d rows)", len(votes_rows))
    logging.info("Wrote vote_events.json (%d records)", len(vote_events))
    logging.info("Wrote motions.json (%d records)", len(motions))
    logging.info("Unmapped options: %d", len(report["unmapped_options"]))
    logging.info(
        "G2 count-consistency: %d match, %d mismatch out of %d events",
        report["count_consistency"]["match"], report["count_consistency"]["mismatch"], report["total_events"],
    )

    report["persons_count"] = len(person_rows)
    report["memberships_count"] = len(memberships)
    report["votes_count"] = len(votes_rows)
    report["vote_events_count"] = len(vote_events)
    report["motions_count"] = len(motions)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
