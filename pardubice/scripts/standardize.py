"""Standardize Pardubice's ZmP roll-call voting PDFs into dt-standard tables.

Source shape: pardubice/config/sources.yml (research pass 2026-08-31 — see this repo's memory:
pardubice-source-research). One clean text-layer PDF per MEETING (occasionally a .txt), bundling
every "HLASOVÁNÍ č.N" block for that meeting. Extracted via `pdftotext -layout` (poppler-utils, a
required system dependency); .txt members are read directly.

Two format eras, handled by one parser:
  - "classic" (term start 2022 through session 34): event header `HLASOVÁNÍ č. N - SCHVÁLENO`
    (result IS in the header), timestamp, agenda line, a rule of underscores, one row per
    councillor `Příjmení Jméno   <klub>   <seat#>   <vote>`, another rule, then a totals block.
  - "verbose_2026" (session ~35+): adds `str. X z N` page-break lines interleaved (stripped
    first), a leading `Prezence č. 1` attendance block (skipped — not a vote), a bare event
    header `HLASOVÁNÍ č. N` with a SEPARATE `HLASOVÁNÍ č. N - SCHVÁLENO` result line after the
    totals block.

Event detection is era-agnostic: an event starts at a `HLASOVÁNÍ č. N` line immediately followed
by a `DD.MM.YYYY HH:MM:SS` timestamp line — true of the real header in both eras, never of the
verbose trailing result line (which is followed by a meeting header, not a timestamp). Results are
taken from ALL `HLASOVÁNÍ č. N - (SCHVÁLENO|NESCHVÁLENO)` matches in the meeting text.

G2 (built in here, checked as a hard gate by run_pipeline.py): each event's recomputed per-row
Pro/Proti/abstain/not-voting counts must exactly match the totals line's four figures. The
roster-row-count vs "Celkem zastupitelů" is logged as a warning, not gated (the Omluven vs
Nepřítomen split against the single "Omluveno" subcount is not fully pinned down).

Person name split: no structural marker (same as Ústí) — standard Czech "last word = given name"
heuristic; a genuine two-word surname could mis-split. Flagged, not silently assumed perfect.
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

ORG_ID = "pardubice:org:zastupitelstvo-mesta-pardubic"
ORG_NAME = "Zastupitelstvo města Pardubic"
_SOURCE_URL = "https://pardubice.eu/zmp-2026"

# Per-row vote words. Gendered variants are real (see sources.yml). pdftotext also sometimes
# injects a stray space inside a word ("Zdržel s e"), so matching is done on the whitespace-
# stripped form via _map_option(), not a literal lookup.
_OPTION_MAP = {
    "Pro": "yes",
    "Proti": "no",
    "Zdržel se": "abstain",
    "Zdržela se": "abstain",
    "Nehlasoval": "not voting",
    "Nehlasovala": "not voting",
    "Omluven": "absent",
    "Omluvena": "absent",
    "Nepřítomen": "absent",
    "Nepřítomna": "absent",
}
_OPTION_MAP_NOSPACE = {re.sub(r"\s+", "", k): v for k, v in _OPTION_MAP.items()}


def _map_option(raw: str) -> str | None:
    collapsed = re.sub(r"\s+", " ", raw).strip()
    if collapsed in _OPTION_MAP:
        return _OPTION_MAP[collapsed]
    return _OPTION_MAP_NOSPACE.get(re.sub(r"\s+", "", collapsed))


# Page furniture interleaved in the roster, all eras — must be stripped before parsing or it
# splits a roster row / defeats the event-header regex:
#   "str. 4 z 129 Záznam ze zasedání konaného dne : ..."  (verbose 2026)
#   "Stránka 15"  +  "Hlasování bez os. údajů.txt"        (PDFs printed from a .txt, e.g. meeting 2)
_FURNITURE_RE = re.compile(
    r"^[ \t]*(?:str\. \d+ z \d+.*|Stránka \d+|Záznam ze zasedání konaného dne.*|Hlasován[ií][^\n]*\.txt)[ \t]*$",
    re.M,
)
_PAGEBREAK_RE = _FURNITURE_RE  # back-compat alias for any external caller
_MEETING_HEADER_RE = re.compile(
    r"(\d+)\.\s*zasedání [Zz]astupitelstva města Pardubic dne (\d{1,2})\.(\d{1,2})\.(\d{4})"
)
_RESULT_RE = re.compile(r"HLASOVÁNÍ č\.\s*(\d+)\s*-\s*(SCHVÁLENO|NESCHVÁLENO)")
# An event start: "HLASOVÁNÍ č. N" (optionally "- RESULT"), then a timestamp line.
_EVENT_HEAD_RE = re.compile(
    r"HLASOVÁNÍ č\.\s*(\d+)(?:\s*-\s*(?:SCHVÁLENO|NESCHVÁLENO))?\s*\n\s*"
    r"(\d{1,2})\.(\d{1,2})\.(\d{4}) \d{1,2}:\d{2}:\d{2}",
    re.M,
)
# The totals line is "Pro: N (x%) Proti: N (x%) Zdrželo se: N (x%)  Nehlasovalo: N (x%)" — but
# trailing zero-valued fields are sometimes dropped entirely (meeting 10 vote 21 omits
# "Nehlasovalo: 0"). Anchor on the distinctive "Pro: N (x%)" token, then read each following field
# independently, defaulting a missing one to 0.
_TOTALS_ANCHOR_RE = re.compile(r"Pro:\s*(\d+)\s*\(\d+\s*%\)")
_PROTI_RE = re.compile(r"Proti:\s*(\d+)\s*\(\d+\s*%\)")
_ZDRZELO_RE = re.compile(r"Zdrželo se:\s*(\d+)\s*\(\d+\s*%\)")
_NEHLASOVALO_RE = re.compile(r"Nehlasovalo:\s*(\d+)\s*\(\d+\s*%\)")
_CELKEM_RE = re.compile(r"Celkem zastupitelů:\s*(\d+)")
_OMLUVENO_RE = re.compile(r"Omluveno:\s*(\d+)")
# Leading indent varies by meeting/event: usually 1-2 spaces, but some blocks (meeting 10 vote 31+)
# have the roster flush at column 0. The name->klub column gap is the anchor. CRITICAL: every
# inter-field separator is [ \t] not \s — \s matches \n, which let an "____" rule line merge with
# the next roster row into one bogus match (dropping that councillor + injecting a garbage klub).
# The name must start with a Unicode letter, which also rejects the pure-underscore rule lines.
# The vote is whatever letters follow the seat number to end of line — captured loosely and
# normalized by _map_option (handles "Zdržel s e" and gendered forms). Requires >=1 letter so a
# bare "  Name   Klub   12  " with no vote never matches.
_ROW_RE = re.compile(
    r"^ {0,8}(?P<name>[^\W\d_][^\n]*?\S)[ \t]{2,}(?P<klub>\S[^\n]*?\S)[ \t]+(?P<seat>\d{1,3})[ \t]+"
    r"(?P<vote>[^\W\d_][^\n]*?)[ \t]*$",
    re.M,
)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _pdftotext_layout(raw: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(raw)
        tmp.flush()
        result = subprocess.run(["pdftotext", "-layout", tmp.name, "-"], capture_output=True, check=True)
    return result.stdout.decode("utf-8")


def _extract_text(path: Path) -> str:
    return _norm_newlines(_extract_text_raw(path))


def _norm_newlines(text: str) -> str:
    # \f (form feed) is pdftotext's page-boundary marker — it lands immediately before the first
    # roster row on a new page, defeating the `^ {0,8}<letter>` row anchor and silently dropping
    # that councillor (a systematic off-by-one against every event's totals). Treat it as a newline.
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")


def _extract_text_raw(path: Path) -> str:
    raw = path.read_bytes()
    if path.suffix.lower() == ".txt":
        # Meeting 20's .txt is UTF-16LE (BOM ff fe) with the same layout as the classic PDF text;
        # try BOM-aware utf-16 first, then the usual Czech-Windows fallbacks.
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        for enc in ("utf-8", "cp1250", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    return _pdftotext_layout(raw)


def _split_name(full_name: str) -> tuple[str, str]:
    """'FamilyName GivenName' -> (given, family). Last-word-is-given heuristic. Trailing '.' on a
    given name (a real export artifact, e.g. 'Mazuch Jan.') is stripped."""
    parts = re.sub(r"\s+", " ", full_name).strip().rstrip(".").split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def parse_meeting(text: str, meeting_no: int, date: str) -> list[dict[str, Any]]:
    text = _PAGEBREAK_RE.sub("", text)

    hdr = _MEETING_HEADER_RE.search(text)
    if hdr:
        hdr_date = f"{hdr.group(4)}-{int(hdr.group(3)):02d}-{int(hdr.group(2)):02d}"
        if hdr_date != date:
            logging.warning("Meeting %d: header date %s != manifest date %s", meeting_no, hdr_date, date)

    results = {int(m.group(1)): ("pass" if m.group(2) == "SCHVÁLENO" else "fail") for m in _RESULT_RE.finditer(text)}

    starts = list(_EVENT_HEAD_RE.finditer(text))
    # dedupe by vote_no keeping the first occurrence (classic has one header; verbose has one too)
    seen_nos: set[int] = set()
    spans: list[tuple[int, int, int]] = []
    for i, sm in enumerate(starts):
        vote_no = int(sm.group(1))
        if vote_no in seen_nos:
            continue
        seen_nos.add(vote_no)
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        spans.append((vote_no, sm.end(), end))

    events: list[dict[str, Any]] = []
    for vote_no, start, end in spans:
        block = text[start:end]

        # No reliable underscore-rule separators (meeting 10 has none at all) — the roster rows
        # themselves are the anchor. Agenda = block text before the first row; totals = the
        # Pro/Proti/Zdrželo/Nehlasovalo line anywhere in the block.
        row_matches = list(_ROW_RE.finditer(block))
        if not row_matches:
            raise ValueError(f"Meeting {meeting_no} vote {vote_no}: no roster rows matched in block")

        agenda = re.sub(r"[_\s]+", " ", block[:row_matches[0].start()]).strip()

        anchor = _TOTALS_ANCHOR_RE.search(block)
        if not anchor:
            raise ValueError(f"Meeting {meeting_no} vote {vote_no}: totals line ('Pro: N (x%)') not found")
        tail = block[anchor.start():anchor.start() + 300]  # the 4 figures always fit in one short span
        proti_m, zdrz_m, neh_m = _PROTI_RE.search(tail), _ZDRZELO_RE.search(tail), _NEHLASOVALO_RE.search(tail)
        totals = {
            "pro": int(anchor.group(1)),
            "proti": int(proti_m.group(1)) if proti_m else 0,
            "zdrzelo": int(zdrz_m.group(1)) if zdrz_m else 0,
            "nehlasovalo": int(neh_m.group(1)) if neh_m else 0,
        }
        celkem_m = _CELKEM_RE.search(block)
        celkem = int(celkem_m.group(1)) if celkem_m else None

        rows = []
        for rm in row_matches:
            vote_raw = re.sub(r"\s+", " ", rm.group("vote")).strip()
            # A real vote token is short; a long capture means a line of resolution prose slipped
            # through the row shape (klub column containing "a č. 3 tohoto usnesení." etc.) — drop
            # it silently rather than reporting it as an unmapped vocabulary value.
            if _map_option(vote_raw) is None and len(vote_raw) > 15:
                continue
            rows.append(
                {
                    "full_name": re.sub(r"\s+", " ", rm.group("name")).strip(),
                    "klub": re.sub(r"\s+", " ", rm.group("klub")).strip(),
                    "seat": int(rm.group("seat")),
                    "option_raw": vote_raw,
                }
            )

        events.append(
            {
                "meeting_no": meeting_no,
                "vote_no": vote_no,
                "date": date,
                "title": agenda,
                "result": results.get(vote_no),
                "totals": totals,
                "celkem": celkem,
                "rows": rows,
            }
        )
    return events


# ── Orchestration ────────────────────────────────────────────────────────────────────────────
def _person_key(given: str, family: str) -> str:
    return _slugify(f"{given}-{family}")


def resolve_all_events(raw_dir: Path = _DEFAULT_RAW_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    """Parses every cached meeting file and resolves each vote into a unified per-event structure.
    Returns (events, persons, report). Each event's "options" carries "klub" per person — reused
    by party_affiliation.py (C4), the plzen/usti mechanism."""
    manifest = json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "total_meetings": 0,
        "total_events": 0,
        "unmapped_options": [],
        "missing_result": [],
        "count_consistency": {"match": 0, "mismatch": 0},
        "count_mismatches": [],
        "roster_vs_celkem": {"match": 0, "mismatch": 0},
        "roster_mismatches": [],
    }

    persons: dict[str, dict[str, str]] = {}
    events: list[dict[str, Any]] = []

    for m in manifest["meetings"]:
        path = raw_dir / "pdfs" / m["vote_file"]
        text = _extract_text(path)
        if "HLASOVÁNÍ č." not in text:
            raise ValueError(f"Meeting {m['meeting_no']} ({m['vote_file']}): no 'HLASOVÁNÍ č.' structure in extracted text")
        parsed = parse_meeting(text, m["meeting_no"], m["date"])
        report["total_meetings"] += 1

        for e in parsed:
            report["total_events"] += 1
            if e["result"] is None:
                report["missing_result"].append({"meeting_no": e["meeting_no"], "vote_no": e["vote_no"]})
                logging.warning("Meeting %d vote %d: no SCHVÁLENO/NESCHVÁLENO result line", e["meeting_no"], e["vote_no"])

            options: list[dict[str, Any]] = []
            counts = {"yes": 0, "no": 0, "abstain": 0, "absent": 0, "not voting": 0}
            for r in e["rows"]:
                option = _map_option(r["option_raw"])
                if option is None:
                    report["unmapped_options"].append(
                        {"meeting_no": e["meeting_no"], "vote_no": e["vote_no"], "option_raw": r["option_raw"], "full_name": r["full_name"]}
                    )
                    logging.warning("Meeting %d vote %d: unmapped vote option %r for %s — skipped",
                                    e["meeting_no"], e["vote_no"], r["option_raw"], r["full_name"])
                    continue
                given, family = _split_name(r["full_name"])
                key = _person_key(given, family)
                persons.setdefault(key, {"given_name": given, "family_name": family})
                counts[option] += 1
                options.append({"person_key": key, "option": option, "klub": r["klub"], "seat": r["seat"]})

            t = e["totals"]
            if (counts["yes"] != t["pro"] or counts["no"] != t["proti"]
                    or counts["abstain"] != t["zdrzelo"] or counts["not voting"] != t["nehlasovalo"]):
                report["count_consistency"]["mismatch"] += 1
                report["count_mismatches"].append(
                    {"meeting_no": e["meeting_no"], "vote_no": e["vote_no"], "computed": dict(counts), "totals": t}
                )
            else:
                report["count_consistency"]["match"] += 1

            if e["celkem"] is not None:
                if len(e["rows"]) == e["celkem"]:
                    report["roster_vs_celkem"]["match"] += 1
                else:
                    report["roster_vs_celkem"]["mismatch"] += 1
                    report["roster_mismatches"].append(
                        {"meeting_no": e["meeting_no"], "vote_no": e["vote_no"], "rows": len(e["rows"]), "celkem": e["celkem"]}
                    )

            events.append(
                {
                    "meeting_no": e["meeting_no"],
                    "vote_no": e["vote_no"],
                    "date": e["date"],
                    "title": e["title"],
                    "result": e["result"] or "pass",  # default; missing_result is reported above
                    "counts": counts,
                    "options": options,
                }
            )

    events.sort(key=lambda e: (e["date"], e["meeting_no"], e["vote_no"]))
    return events, persons, report


def standardize(raw_dir: Path = _DEFAULT_RAW_DIR, out_dir: Path = _DEFAULT_OUT) -> dict[str, Any]:
    events, persons, report = resolve_all_events(raw_dir)

    global_max_date = max((e["date"] for e in events), default=None)
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        for o in e["options"]:
            k = o["person_key"]
            first_seen[k] = min(first_seen.get(k, e["date"]), e["date"])
            last_seen[k] = max(last_seen.get(k, e["date"]), e["date"])

    person_rows, memberships = [], []
    for key in sorted(persons):
        if key not in first_seen:
            continue
        p = persons[key]
        person_id = f"pardubice:person:{key}"
        person_rows.append(
            {
                "id": person_id,
                "name": f"{p['given_name']} {p['family_name']}".strip(),
                "given_name": p["given_name"],
                "family_name": p["family_name"],
                "identifiers": "[]",
                "sources": json.dumps([{"url": _SOURCE_URL, "note": "pardubice.eu ZmP voting PDFs"}], ensure_ascii=False),
            }
        )
        end_date = "" if last_seen[key] == global_max_date else last_seen[key]
        memberships.append(
            {
                "id": f"pardubice:membership:{key}:zastupitelstvo-mesta-pardubic",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": first_seen[key],
                "end_date": end_date,
                "sources": json.dumps([{"url": _SOURCE_URL, "note": "start/end derived from first/last recorded vote"}], ensure_ascii=False),
            }
        )

    organization = {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": "[]",
        "sources": json.dumps([{"url": _SOURCE_URL, "note": "pardubice.eu"}], ensure_ascii=False),
    }

    votes_rows, vote_events, motions = [], [], []
    for e in events:
        event_key = f"{e['meeting_no']}-{e['vote_no']}"
        vote_event_id = f"pardubice:vote-event:{event_key}"
        motion_id = f"pardubice:motion:{event_key}"
        for o in e["options"]:
            votes_rows.append(
                {"vote_event_id": vote_event_id, "voter_id": f"pardubice:person:{o['person_key']}", "voter_type": "person", "option": o["option"]}
            )
        identifier = f"{e['meeting_no']}/{e['vote_no']}"
        sources = [{"url": _SOURCE_URL, "note": f"meeting {e['meeting_no']}, hlasování {e['vote_no']}"}]
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

    logging.info("persons=%d memberships=%d votes=%d vote_events=%d motions=%d",
                 len(person_rows), len(memberships), len(votes_rows), len(vote_events), len(motions))
    logging.info("unmapped_options=%d missing_result=%d", len(report["unmapped_options"]), len(report["missing_result"]))
    logging.info("G2 count-consistency: %d match / %d mismatch of %d events",
                 report["count_consistency"]["match"], report["count_consistency"]["mismatch"], report["total_events"])
    logging.info("roster vs 'Celkem zastupitelů': %d match / %d mismatch",
                 report["roster_vs_celkem"]["match"], report["roster_vs_celkem"]["mismatch"])

    report.update(
        persons_count=len(person_rows), memberships_count=len(memberships), votes_count=len(votes_rows),
        vote_events_count=len(vote_events), motions_count=len(motions),
    )
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
