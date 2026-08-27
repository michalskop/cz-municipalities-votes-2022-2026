"""Standardize Ostrava's cached roll-call HTML pages into dt-standard tables (C9).

Source: ostrava/work/raw/ (built by downloader.py) — one directory per meeting
(ostrava/work/raw/<meeting_code>/), each containing a cached index.html plus one cached
<NNNN>.html per vote. See downloader.py's module docstring for the crawl shape and
ostrava/config/sources.yml for the vote-option vocabulary/schema notes.

Scope boundary (matches Praha's C7 / Brno's C2 precedent): this standardizer builds ONLY the bare
assembly organization + membership (a person's tenure as a council member, derived from their own
vote participation). It does NOT build party/klub organizations or memberships — Ostrava's
per-vote party grouping is only sometimes populated (confirmed empty on every sampled vote from
meeting z202604, populated on meeting z202201's early votes) and cannot be relied on as a complete
per-vote source; a real party/klub build needs the live composition page
(ostrava.cz/.../slozeni-zastupitelstva-1) as its primary source instead, which is C4's job, not
C9's.

Person-identity finding (2026-08-27, confirmed on the real corpus before writing this): the same
real person's displayed academic-title prefix AND the comma-suffix credential after their surname
BOTH vary across different vote pages — e.g. "Bc. Miroslav Otisk" on one page, later "Ing. Miroslav
Otisk, MSc., MBA" on another (same real person, evidently earning further
degrees over the term — titles observed to only ever grow, never shrink, across this corpus).
Person identity is therefore built from normalized (given_name, family_name) ONLY, with all title
variants stripped for the identity key; the canonical *displayed* name/title uses whichever
variant was observed LAST in strict chronological (meeting, vote-number) order — documented as a
modeling choice (titles are assumed non-decreasing over time; not independently verified to be
strictly monotonic for every person), not a guess. See `_MergedPerson`.

Three real data-quality patterns handled here, logged, never silently dropped:
1. **Missing resolution text** — procedural/test votes (e.g. z202201's vote 1, an explicit
   "zkušební hlasování" test vote) have no `<pre>` usnesení block. motion text is left empty, not
   fabricated.
2. **Per-vote result self-consistency (G2)** — every vote page publishes its own aggregate tally
   (Přítomno/Pro/Proti/Zdržel se/Nehlasovalo). Recomputing the same tally from the per-person rows
   this script parses and comparing against that published aggregate is the G2 cross-check — not a
   truly independent source (it's the same page), but it does catch parsing bugs, which is its real
   purpose here (Ostrava has no separate published dataset to cross-check against at all, unlike
   Praha/Brno).
3. **Failed page fetches** — downloader.py records `failed_vote_numbers` per meeting in
   meetings.json when a page could not be fetched even after retries. This standardizer skips
   those (never fabricates a vote-event for a page it never actually saw) and logs the count.
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

ORG_ID = "ostrava:org:zastupitelstvo-mesta-ostravy"
ORG_NAME = "Zastupitelstvo města Ostravy"

# NOTE the literal "z" before {meeting}: meeting_code itself is the bare 6-digit string (e.g.
# "202201") — downloader.py's URL patterns have their own literal "z" baked in (see
# sources.yml's meeting_index_pattern/vote_page_pattern), but this citation template is separate
# code and needs the same "z" or the resulting source URL 404s. Caught 2026-08-27 while building
# the golden sample test, before this ever got committed.
_SOURCE_URL_TMPL = "https://www.ostrava.cz/uloziste/zastupitelstvo/vysledky_hlasovani/vo2226/z{meeting}/{n}.html"

# Per-person cast values observed on real vote pages (2026-08-27, sampled across meetings
# z202201 and z202604). "Omluven" (excused) and other schema-CZ-style codes some other cities'
# feeds document are NOT in this map on purpose — never observed here; a genuinely new value
# raises loudly (see _parse_vote_page) rather than being guessed into an option.
OPTION_MAP = {
    "Pro": "yes",
    "Proti": "no",
    "Zdržel se": "abstain",
    "Nepřítomen": "absent",
    "Nehlasoval": "not voting",
}

_DEPUTY_RE = re.compile(
    r'class="deputy"[^>]*>([^<]*)<b>([^<]*)</b>\s*:</td>\s*'
    r'<td[^>]*class="cast"[^>]*>([^<]*)</td>',
)
_DATE_RE = re.compile(r"Dne\s*([\d.]+)\s*([\d:]+)")
_AGENDA_RE = re.compile(r'class="title">(.*?)</td>', re.S)
_RESOLUTION_RE = re.compile(r"\n(\d+/ZM2226/\d+)\s")
_USNESENI_RE = re.compile(
    r'<pre style="font: 10pt Courier New, Arial; text-align: left;">(.*?)</pre>', re.S
)
_TOTALS_RE = re.compile(
    r"Přítomno:\s*(\d+).*?<b>Pro:\s*(\d+)</b>.*?Proti:\s*(\d+).*?Zdržel se:\s*(\d+).*?"
    r"Nehlasovalo:\s*(\d+)",
    re.S,
)


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _parse_cz_date(date_str: str) -> str:
    """"19.10.2022" -> "2022-10-19"."""
    d, m, y = date_str.strip(".").split(".")
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _split_deputy(given_block: str, family_block: str) -> dict[str, str]:
    """given_block: "Ing. Lucie" (title prefix + given name). family_block: "Baránková, Ph.D."
    (surname, optional comma-suffix credential). Returns raw parts — normalization/identity
    handling happens in the caller, which needs both the raw (for display) and normalized
    (given_name, family_name) forms."""
    given_block = html_module.unescape(given_block).strip()
    family_block = html_module.unescape(family_block).strip()
    tokens = given_block.split()
    given_name = tokens[-1] if tokens else given_block
    title_prefix = " ".join(tokens[:-1])
    if "," in family_block:
        family_name, suffix = family_block.split(",", 1)
        family_name = family_name.strip()
        suffix = suffix.strip()
    else:
        family_name, suffix = family_block, ""
    return {
        "title_prefix": title_prefix,
        "given_name": given_name,
        "family_name": family_name,
        "suffix": suffix,
    }


def _parse_vote_page(raw_html: str, meeting_code: str, vote_number: str) -> dict[str, Any]:
    agenda_match = _AGENDA_RE.search(raw_html)
    agenda = re.sub(r"\s+", " ", html_module.unescape(agenda_match.group(1))).strip() if agenda_match else None

    usneseni_match = _USNESENI_RE.search(raw_html)
    usneseni_raw = usneseni_match.group(1) if usneseni_match else ""
    usneseni_text = html_module.unescape(usneseni_raw).strip()
    resolution_match = _RESOLUTION_RE.search(usneseni_raw)
    resolution_number = resolution_match.group(1) if resolution_match else None

    date_match = _DATE_RE.search(raw_html)
    if not date_match:
        raise ValueError(f"{meeting_code}/{vote_number}: no 'Dne DD.MM.YYYY HH:MM:SS' timestamp found")
    date_iso = _parse_cz_date(date_match.group(1))
    time_str = date_match.group(2)

    totals_match = _TOTALS_RE.search(raw_html)
    if not totals_match:
        raise ValueError(f"{meeting_code}/{vote_number}: aggregate totals line not found")
    totals = {
        "pritomno": int(totals_match.group(1)),
        "pro": int(totals_match.group(2)),
        "proti": int(totals_match.group(3)),
        "zdrzel_se": int(totals_match.group(4)),
        "nehlasovalo": int(totals_match.group(5)),
    }

    deputies = []
    for m in _DEPUTY_RE.finditer(raw_html):
        parts = _split_deputy(m.group(1), m.group(2))
        cast_raw = html_module.unescape(m.group(3)).strip()
        deputies.append({**parts, "cast_raw": cast_raw})

    return {
        "meeting_code": meeting_code,
        "vote_number": vote_number,
        "date_iso": date_iso,
        "time": time_str,
        "agenda": agenda,
        "resolution_number": resolution_number,
        "usneseni_text": usneseni_text,
        "totals": totals,
        "deputies": deputies,
    }


class _MergedPerson:
    """Accumulates every observed title/name variant for one normalized identity, keeping the
    chronologically LAST-observed variant as canonical (see module docstring's identity-finding
    note)."""

    def __init__(self, given_name: str, family_name: str) -> None:
        self.given_name = given_name
        self.family_name = family_name
        self.title_prefix = ""
        self.suffix = ""
        self.observed_variants: set[str] = set()

    def observe(self, parts: dict[str, str]) -> None:
        self.title_prefix = parts["title_prefix"]
        self.given_name = parts["given_name"]
        self.family_name = parts["family_name"]
        self.suffix = parts["suffix"]
        variant = f"{parts['title_prefix']} {parts['given_name']} {parts['family_name']}".strip()
        if parts["suffix"]:
            variant += f", {parts['suffix']}"
        self.observed_variants.add(variant)

    @property
    def full_name(self) -> str:
        base = f"{self.given_name} {self.family_name}"
        return f"{base}, {self.suffix}" if self.suffix else base

    @property
    def display_title(self) -> str:
        return self.title_prefix


def standardize(raw_dir: Path = _DEFAULT_RAW_DIR, out_dir: Path = _DEFAULT_OUT) -> dict[str, Any]:
    manifest_path = raw_dir / "meetings.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    meeting_codes = sorted(manifest.keys())  # "YYYYNN" strings sort chronologically
    logging.info("Standardizing %d meeting(s) from %s", len(meeting_codes), manifest_path)

    people: dict[tuple[str, str], _MergedPerson] = {}  # normalized (given, family) -> merged
    event_dates_by_person: dict[tuple[str, str], list[str]] = {}
    votes: list[dict[str, Any]] = []
    vote_events: list[dict[str, Any]] = []
    motions: list[dict[str, Any]] = []

    report = {
        "total_events": 0,
        "skipped_failed_fetch": 0,
        "result_consistency": {"match": 0, "mismatch": 0},
        "result_mismatches": [],
        "unmapped_cast_values": [],
    }

    for meeting_code in meeting_codes:
        meeting_info = manifest[meeting_code]
        if meeting_info.get("index_fetch_failed"):
            logging.warning("Meeting %s: index.html fetch failed, skipping entire meeting", meeting_code)
            continue
        failed_votes = set(meeting_info.get("failed_vote_numbers", []))
        meeting_dir = raw_dir / meeting_code
        vote_files = sorted(p.stem for p in meeting_dir.glob("[0-9][0-9][0-9][0-9].html"))

        for n in vote_files:
            if n in failed_votes:
                continue
            raw_html = (meeting_dir / f"{n}.html").read_text(encoding="utf-8")
            try:
                parsed = _parse_vote_page(raw_html, meeting_code, n)
            except ValueError as exc:
                logging.error("%s — skipping this vote event", exc)
                report["skipped_failed_fetch"] += 1
                continue

            report["total_events"] += 1
            vote_event_id = f"ostrava:vote-event:{meeting_code}-{n}"
            motion_id = f"ostrava:motion:{meeting_code}-{n}"
            start_date = f"{parsed['date_iso']}T{parsed['time']}"

            counts = {opt: 0 for opt in ("yes", "no", "abstain", "absent", "not voting")}
            for dep in parsed["deputies"]:
                key = (_slugify(dep["given_name"]), _slugify(dep["family_name"]))
                person = people.setdefault(key, _MergedPerson(dep["given_name"], dep["family_name"]))
                person.observe(dep)
                event_dates_by_person.setdefault(key, []).append(parsed["date_iso"])

                option = OPTION_MAP.get(dep["cast_raw"])
                if option is None:
                    report["unmapped_cast_values"].append(
                        {"vote_event_id": vote_event_id, "person_key": key, "raw_value": dep["cast_raw"]}
                    )
                    logging.warning(
                        "%s: unmapped cast value %r for %s %s — no vote row written, not fabricated",
                        vote_event_id, dep["cast_raw"], dep["given_name"], dep["family_name"],
                    )
                    continue
                counts[option] += 1
                votes.append(
                    {
                        "vote_event_id": vote_event_id,
                        "voter_id": None,  # filled in below once every person's slug is finalized
                        "voter_type": "person",
                        "option": option,
                        "_person_key": key,  # internal, stripped before writing votes.csv
                    }
                )

            totals = parsed["totals"]
            recomputed = {
                # "Přítomno" (present) counts everyone physically present, including those who
                # chose not to press a button ("Nehlasoval") — only "Nepřítomen" (absent) is
                # excluded. Confirmed empirically 2026-08-27 against 130 real vote pages: a
                # pritomno formula that excluded not-voting mismatched on 59/130 events; this one
                # matches exactly on all 130.
                "pritomno": counts["yes"] + counts["no"] + counts["abstain"] + counts["not voting"],
                "pro": counts["yes"],
                "proti": counts["no"],
                "zdrzel_se": counts["abstain"],
                "nehlasovalo": counts["not voting"],
            }
            if recomputed == totals:
                report["result_consistency"]["match"] += 1
            else:
                report["result_consistency"]["mismatch"] += 1
                report["result_mismatches"].append(
                    {"vote_event_id": vote_event_id, "published": totals, "recomputed": recomputed}
                )

            vote_events.append(
                {
                    "id": vote_event_id,
                    "identifier": f"{meeting_code}/{n}",
                    "motion_id": motion_id,
                    "organization_id": ORG_ID,
                    "start_date": start_date,
                    "result": "pass" if totals["pro"] > totals["proti"] else "fail",
                    "counts": [{"option": k, "value": v} for k, v in counts.items()],
                    "sources": [
                        {
                            "url": _SOURCE_URL_TMPL.format(meeting=meeting_code, n=n),
                            "note": f"ostrava.cz vysledky_hlasovani, meeting {meeting_code}, vote {n}",
                        }
                    ],
                    "extras": {
                        "meeting_code": meeting_code,
                        "vote_number": n,
                        "agenda": parsed["agenda"],
                        "resolution_number": parsed["resolution_number"],
                        "published_totals": totals,
                    },
                }
            )
            motion_record: dict[str, Any] = {
                "id": motion_id,
                "organization_id": ORG_ID,
                "date": parsed["date_iso"],
            }
            # Only "id" is required by the published motions.dt.json schema; "text" is typed as
            # a plain string (no null variant), so procedural/test votes with no usnesení block
            # (see module docstring finding #1) omit the key entirely rather than writing null.
            if parsed["usneseni_text"]:
                motion_record["text"] = parsed["usneseni_text"]
            if parsed["resolution_number"]:
                motion_record["identifier"] = parsed["resolution_number"]
            motions.append(motion_record)

    # Now that every person's normalized key is known, assign stable slugs and fix up votes.csv.
    slug_counts: dict[str, int] = {}
    key_to_person_id: dict[tuple[str, str], str] = {}
    for key in sorted(people):
        base_slug = f"{key[0]}-{key[1]}"
        slug_counts[base_slug] = slug_counts.get(base_slug, 0) + 1
    seen_slug: dict[str, int] = {}
    for key in sorted(people):
        base_slug = f"{key[0]}-{key[1]}"
        if slug_counts[base_slug] > 1:
            seen_slug[base_slug] = seen_slug.get(base_slug, 0) + 1
            logging.warning(
                "G5 identity collision: multiple distinct normalized keys share slug %r — "
                "this should not happen (keys are already unique tuples); investigate.",
                base_slug,
            )
        key_to_person_id[key] = f"ostrava:person:{base_slug}"

    for v in votes:
        v["voter_id"] = key_to_person_id[v.pop("_person_key")]

    persons_rows = []
    for key, person in sorted(people.items()):
        person_id = key_to_person_id[key]
        persons_rows.append(
            {
                "id": person_id,
                "name": person.full_name,
                "given_name": person.given_name,
                "family_name": person.family_name,
                "identifiers": json.dumps(
                    [{"scheme": "ostrava:academic_title", "identifier": person.display_title}]
                    if person.display_title
                    else [],
                    ensure_ascii=False,
                ),
                "sources": json.dumps(
                    [
                        {
                            "url": "https://www.ostrava.cz/uloziste/zastupitelstvo/vysledky_hlasovani/vo2226/",
                            "note": (
                                f"Name/title as last observed across {len(person.observed_variants)} "
                                f"distinct variant(s) seen on vote pages: {sorted(person.observed_variants)}"
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )

    global_max_date = max(
        max(dates) for dates in event_dates_by_person.values()
    ) if event_dates_by_person else None

    memberships_rows = []
    for key, dates in sorted(event_dates_by_person.items()):
        person_id = key_to_person_id[key]
        start_date = min(dates)
        end_date = max(dates)
        end_date_str = "" if end_date == global_max_date else end_date
        memberships_rows.append(
            {
                "id": f"ostrava:membership:{person_id.split(':', 2)[2]}:{ORG_ID.split(':', 2)[2]}",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": start_date,
                "end_date": end_date_str,
                "sources": json.dumps(
                    [
                        {
                            "url": "https://www.ostrava.cz/uloziste/zastupitelstvo/vysledky_hlasovani/vo2226/",
                            "note": (
                                "start/end derived from first/last vote-page appearance for this "
                                "person (present or absent, both count as 'on the roster then'); "
                                "open end_date = still active as of the last observed vote."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )

    organization_row = {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": json.dumps([], ensure_ascii=False),
        "sources": json.dumps(
            [{"url": "https://ostrava.cz/cs/urad/mesto-a-jeho-organy/zastupitelstvo-mesta", "note": "official council page"}],
            ensure_ascii=False,
        ),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(persons_rows).fillna("").to_csv(out_dir / "persons.csv", index=False, encoding="utf-8")
    pd.DataFrame([organization_row]).fillna("").to_csv(out_dir / "organizations.csv", index=False, encoding="utf-8")
    pd.DataFrame(memberships_rows).fillna("").to_csv(out_dir / "memberships.csv", index=False, encoding="utf-8")
    votes_out = [{k: v for k, v in row.items() if k != "_person_key"} for row in votes]
    pd.DataFrame(votes_out).to_csv(out_dir / "votes.csv", index=False, encoding="utf-8")
    (out_dir / "vote_events.json").write_text(
        json.dumps(vote_events, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (out_dir / "motions.json").write_text(
        json.dumps(motions, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )

    logging.info("Wrote persons.csv (%d rows)", len(persons_rows))
    logging.info("Wrote organizations.csv (1 row)")
    logging.info("Wrote memberships.csv (%d rows)", len(memberships_rows))
    logging.info("Wrote votes.csv (%d rows)", len(votes_out))
    logging.info("Wrote vote_events.json (%d records)", len(vote_events))
    logging.info("Wrote motions.json (%d records)", len(motions))
    logging.info(
        "G2 result-consistency: %d/%d events match their own published aggregate; %d skipped "
        "(failed fetch/parse); %d unmapped cast value(s)",
        report["result_consistency"]["match"],
        report["total_events"],
        report["skipped_failed_fetch"],
        len(report["unmapped_cast_values"]),
    )
    if report["result_consistency"]["mismatch"]:
        logging.warning(
            "%d event(s) have a recomputed tally that doesn't match the page's own published "
            "aggregate — likely a parsing edge case, see report.",
            report["result_consistency"]["mismatch"],
        )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    parser.add_argument("--report", default=str(_CITY_ROOT / "work" / "reports" / "g2_report.json"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = standardize(Path(args.raw_dir), Path(args.out_dir))

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote full quality report to %s (gitignored, work/)", report_path)


if __name__ == "__main__":
    main()
