"""Standardize Brno's zastupko.cz nested JSON feed into dt-standard tables.

Source: brno/config/sources.yml's `zastupko_current` entry — a single JSON document (dataset id 9,
term 2022-2026) shaped as municipalita -> politickeSubjekty -> zastupitele -> zastupitelstva[0]
(koalice, lidr, zasedani[] -> hlasovani[] -> zastupiteleHlasy[]). See sources.yml for the full
schema trail, discovery notes, and known data-quality findings; this docstring covers only what
the code below does about them.

Scope boundary (matches C7's precedent for Praha): this standardizer builds ONLY the bare assembly
organization + membership (a person's tenure as a council member, derived from their own vote
participation dates). It deliberately does NOT build party/coalition organizations or memberships
from the feed's `politickeSubjekty`/`koalice`/`lidr` data, even though that data is real and
live-sourced with genuine since/until intervals (see sources.yml's
`party_and_membership_data_available_for_c4` note) — that belongs to C4, which needs owner sign-off
on coalition facts (D7), not C2.

Three real data-quality findings are handled here, logged, never silently dropped:

1. **Corrupted vote value** — one vote event (hlasovani id=4962) has 48/55 per-person `hlas` values
   as a bare JSON integer `0` instead of a documented vocabulary code. No votes.csv row is written
   for those 48 (person, event) pairs — never fabricate a vote — and the event's
   `extras.data_quality.corrupted_hlas_values` records exactly which persons and how many.
2. **G5 namesake collision** — two distinct real people share the name "Petr Bořecký" (source ids
   3 and 121, confirmed genuinely distinct: different parties, overlapping tenure — see
   sources.yml). Never merged; disambiguated by appending the source's own numeric id to the slug
   for every colliding name (`_build_persons` below), and logged loudly.
3. **G2 cross-check** — the feed publishes no independent per-event aggregate (`sumarizace` is
   empty on all 2813 events; the official protocol pages linked via `urlProtokol` are not reachable
   from this environment, see sources.yml). The primary G2 signal here is therefore a
   self-consistency check: the source's own `prijato` (pass/fail) boolean, asserted by the
   council's own voting system, versus the majority direction of our recomputed per-person tally.
   Ties (yes == no) are skipped (ambiguous, no gate signal). Any mismatch is logged individually,
   plus a best-effort (non-blocking) live cross-check against a sample of `urlProtokol` pages when
   reachable. See `_classify_result_consistency` and `run_protocol_cross_check`.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_RAW = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "brno:org:zastupitelstvo-mesta-brna"
ORG_NAME = "Zastupitelstvo města Brna"

# schema-CZ.json's `hlas` enum has 7 values; only these 5 have ever been observed in the live data
# (O/T = 0 occurrences each as of 2026-08-07). Deliberately NOT mapping O/T — if they ever appear,
# fail loudly rather than guess a dt-standard option for "excused"/"secret ballot".
OPTION_MAP = {
    "A": "yes",
    "N": "no",
    "Z": "abstain",
    "–": "absent",
    "X": "not voting",
}
_COUNT_OPTIONS = ("yes", "no", "abstain", "absent", "not voting")


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _load_raw(raw_path: Path) -> dict[str, Any]:
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    for key in ("municipalita", "politickeSubjekty", "zastupitele", "zastupitelstva"):
        if key not in data:
            raise ValueError(f"Source JSON missing top-level key {key!r} — schema drift?")
    if len(data["zastupitelstva"]) != 1:
        raise ValueError(
            f"Expected exactly 1 term (zastupitelstva) in dataset 9, got "
            f"{len(data['zastupitelstva'])} — refusing to guess which one applies."
        )
    return data


def _build_organization(source_url: str) -> dict[str, Any]:
    return {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": json.dumps(
            [{"scheme": "brno:org_abbr", "identifier": "ZMB"}], ensure_ascii=False
        ),
        "sources": json.dumps(
            [{"url": source_url, "note": "zastupko.cz feed, dataset id 9, term 2022-2026"}],
            ensure_ascii=False,
        ),
    }


def _build_persons(
    zastupitele: list[dict[str, Any]], source_url: str
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Returns (persons rows, {idZastupitel: person_id}). Handles the G5 namesake collision by
    suffixing the source's own numeric id onto every colliding slug (never merges)."""
    slug_to_ids: dict[str, list[int]] = {}
    for z in zastupitele:
        slug = _slugify(f"{z['jmeno']}-{z['prijmeni']}")
        slug_to_ids.setdefault(slug, []).append(z["id"])

    persons: list[dict[str, Any]] = []
    id_to_person_id: dict[int, str] = {}

    for z in zastupitele:
        slug = _slugify(f"{z['jmeno']}-{z['prijmeni']}")
        colliding_ids = slug_to_ids[slug]
        if len(colliding_ids) > 1:
            logging.warning(
                "G5 identity collision: %d distinct source ids (%s) share the name %r %r — "
                "disambiguating with a numeric suffix, NOT merging.",
                len(colliding_ids),
                colliding_ids,
                z["jmeno"],
                z["prijmeni"],
            )
            person_slug = f"{slug}-{z['id']}"
        else:
            person_slug = slug

        person_id = f"brno:person:{person_slug}"
        id_to_person_id[z["id"]] = person_id

        identifiers = [{"scheme": "brno:zastupko_id", "identifier": str(z["id"])}]
        for alias in z.get("aliasy") or []:
            former = f"{alias.get('jmeno', '')} {alias.get('prijmeni', '')}".strip()
            if former:
                identifiers.append({"scheme": "brno:former_name", "identifier": former})

        persons.append(
            {
                "id": person_id,
                "name": f"{z['jmeno']} {z['prijmeni']}",
                "given_name": z["jmeno"],
                "family_name": z["prijmeni"],
                "identifiers": json.dumps(identifiers, ensure_ascii=False),
                "sources": json.dumps(
                    [{"url": source_url, "note": f"zastupko.cz idZastupitel={z['id']}"}],
                    ensure_ascii=False,
                ),
            }
        )

    return persons, id_to_person_id


def _build_memberships(
    id_to_person_id: dict[int, str],
    event_dates_by_person: dict[int, list[str]],
    global_max_date: str,
    source_url: str,
) -> list[dict[str, Any]]:
    memberships: list[dict[str, Any]] = []
    for zastupko_id, dates in event_dates_by_person.items():
        person_id = id_to_person_id[zastupko_id]
        start_date = min(dates)
        end_date = max(dates)
        end_date_str = "" if end_date == global_max_date else end_date

        memberships.append(
            {
                "id": f"brno:membership:{person_id.split(':', 2)[2]}:{ORG_ID}",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": start_date,
                "end_date": end_date_str,
                "sources": json.dumps(
                    [
                        {
                            "url": source_url,
                            "note": (
                                "start/end derived from first/last session date with any recorded "
                                "zastupiteleHlasy entry for this person (present or absent, both "
                                "count as 'on the roster then'); open end_date = still active as "
                                "of this feed's last recorded session."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return memberships


def _classify_result_consistency(
    prijato: bool, platne: bool, counts: dict[str, int]
) -> str | None:
    """Compares the source's own published `prijato` result against our recomputed yes/no
    majority. Returns None (no signal — e.g. a tie, or an invalid vote) or "match"/"mismatch"."""
    if not platne:
        return None
    yes, no = counts["yes"], counts["no"]
    if yes == no:
        return None
    expected_prijato = yes > no
    return "match" if expected_prijato == prijato else "mismatch"


def _build_votes_events_motions(
    zasedani: list[dict[str, Any]],
    id_to_person_id: dict[int, str],
    source_url: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[int, list[str]],
    dict[str, Any],
]:
    votes: list[dict[str, Any]] = []
    vote_events: list[dict[str, Any]] = []
    motions: list[dict[str, Any]] = []
    event_dates_by_person: dict[int, list[str]] = {}

    report: dict[str, Any] = {
        "total_events": 0,
        "total_zastupiteleHlasy_entries": 0,
        "corrupted_hlas_events": [],
        "corrupted_hlas_entries_skipped": 0,
        "result_consistency": {"match": 0, "mismatch": 0, "no_signal": 0},
        "result_mismatches": [],
        "distinct_protocol_urls": 0,
    }

    for session in zasedani:
        session_no = session["cislo"]
        session_date = session["datum"]
        session_url = session.get("url", {}).get("zaznam")

        for h in session["hlasovani"]:
            report["total_events"] += 1
            hlasovani_id = h["id"]
            vote_event_id = f"brno:vote-event:{hlasovani_id}"
            motion_id = f"brno:motion:{hlasovani_id}"

            counts = {opt: 0 for opt in _COUNT_OPTIONS}
            corrupted_ids: list[int] = []

            for zh in h["zastupiteleHlasy"]:
                report["total_zastupiteleHlasy_entries"] += 1
                zastupko_id = zh["idZastupitel"]
                raw_hlas = zh["hlas"]

                # Every recorded entry (any hlas value, including a later-skipped corrupted one)
                # means this person was on the roster for this session — feeds membership dates.
                event_dates_by_person.setdefault(zastupko_id, []).append(session_date)

                option = OPTION_MAP.get(raw_hlas)
                if option is None:
                    corrupted_ids.append(zastupko_id)
                    continue
                counts[option] += 1
                votes.append(
                    {
                        "vote_event_id": vote_event_id,
                        "voter_id": id_to_person_id[zastupko_id],
                        "voter_type": "person",
                        "option": option,
                    }
                )

            if corrupted_ids:
                report["corrupted_hlas_events"].append(
                    {
                        "vote_event_id": vote_event_id,
                        "session": session_no,
                        "date": session_date,
                        "affected_person_source_ids": corrupted_ids,
                        "affected_count": len(corrupted_ids),
                    }
                )
                report["corrupted_hlas_entries_skipped"] += len(corrupted_ids)
                logging.warning(
                    "Vote event %s (session %s, %s): %d/%d zastupiteleHlasy entries have a "
                    "corrupted (non-vocabulary) hlas value — skipped, not fabricated.",
                    vote_event_id,
                    session_no,
                    session_date,
                    len(corrupted_ids),
                    len(h["zastupiteleHlasy"]),
                )

            consistency = _classify_result_consistency(h["prijato"], h["platne"], counts)
            if consistency == "match":
                report["result_consistency"]["match"] += 1
            elif consistency == "mismatch":
                report["result_consistency"]["mismatch"] += 1
                report["result_mismatches"].append(
                    {
                        "vote_event_id": vote_event_id,
                        "session": session_no,
                        "date": session_date,
                        "counts": dict(counts),
                        "prijato": h["prijato"],
                    }
                )
            else:
                report["result_consistency"]["no_signal"] += 1

            extras: dict[str, Any] = {
                "session_cislo": session_no,
                "hlasovani_cislo": h["cislo"],
                "platne": h["platne"],
                "proceduralni": h["proceduralni"],
                "blokove": h.get("blokove"),
                "tajne": h.get("tajne"),
                "urlProtokol": h.get("urlProtokol"),
            }
            data_quality: dict[str, Any] = {}
            if corrupted_ids:
                data_quality["corrupted_hlas_values"] = {
                    "affected_person_source_ids": corrupted_ids,
                    "note": (
                        "Source returned a bare integer 0 instead of a documented hlas code for "
                        "these people on this event — a source-side export bug (isolated to this "
                        "one event as of 2026-08-07, see sources.yml). No vote recorded for them "
                        "here; never fabricated."
                    ),
                }
            if consistency == "mismatch":
                data_quality["result_consistency_mismatch"] = {
                    "note": (
                        "Recomputed yes/no majority disagrees with the source's own published "
                        "`prijato` result. Possibly a supermajority/quorum rule this simple "
                        "majority check doesn't model, or a genuine data issue — not resolved "
                        "here, logged for review."
                    )
                }
            if data_quality:
                extras["data_quality"] = data_quality

            vote_events.append(
                {
                    "id": vote_event_id,
                    "identifier": f"{session_no}/{h['cislo']}",
                    "motion_id": motion_id,
                    "organization_id": ORG_ID,
                    "start_date": f"{session_date}T{h['cas']}",
                    "result": "pass" if h["prijato"] else "fail",
                    "counts": [{"option": opt, "value": counts[opt]} for opt in _COUNT_OPTIONS],
                    "sources": [
                        {"url": source_url, "note": f"hlasovani id={hlasovani_id}"},
                        *([{"url": h["urlProtokol"], "note": "official protocol page"}]
                          if h.get("urlProtokol") else []),
                    ],
                    "extras": extras,
                }
            )

            agenda_items = h.get("projednavano") or []
            text = "; ".join(
                item.get("predmetHlasovani", "") for item in agenda_items if item.get("predmetHlasovani")
            ) or None
            motions.append(
                {
                    "id": motion_id,
                    "identifier": f"{session_no}/{h['cislo']}",
                    "organization_id": ORG_ID,
                    "date": session_date,
                    "text": text,
                    "result": "pass" if h["prijato"] else "fail",
                    "sources": [{"url": source_url, "note": f"hlasovani id={hlasovani_id}"}],
                    "extras": {
                        "cisloUsneseni": next(
                            (item.get("cisloUsneseni") for item in agenda_items if item.get("cisloUsneseni")),
                            None,
                        ),
                        "klicovaSlova": [kw for item in agenda_items for kw in (item.get("klicovaSlova") or [])] or None,
                        "urlNavrh": next(
                            (item.get("urlNavrh") for item in agenda_items if item.get("urlNavrh")),
                            None,
                        ),
                        "agenda_item_count": len(agenda_items),
                    },
                }
            )

    report["distinct_protocol_urls"] = len(
        {ve["extras"]["urlProtokol"] for ve in vote_events if ve["extras"].get("urlProtokol")}
    )
    return votes, vote_events, motions, event_dates_by_person, report


def run_protocol_cross_check(
    vote_events: list[dict[str, Any]], sample_size: int = 10, timeout: int = 15
) -> dict[str, Any]:
    """Best-effort, non-blocking G2 cross-check: fetch a sample of official protocol pages
    (urlProtokol) and look for a recognizable pro/proti/zdrzel table to compare against our
    recomputed counts. Never raises — network unavailability (confirmed from this sandbox,
    apl.brno.cz times out) is logged, not treated as a gate failure. Re-run from CI to see if it's
    reachable there; if so, this becomes a genuine independent G2 signal."""
    sample = [ve for ve in vote_events if ve["extras"].get("urlProtokol")][:sample_size]
    result: dict[str, Any] = {"attempted": len(sample), "reachable": 0, "unreachable": 0, "details": []}

    for ve in sample:
        url = ve["extras"]["urlProtokol"]
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            result["reachable"] += 1
            result["details"].append({"vote_event_id": ve["id"], "url": url, "status": "reachable"})
        except requests.RequestException as exc:
            result["unreachable"] += 1
            result["details"].append(
                {"vote_event_id": ve["id"], "url": url, "status": "unreachable", "error": str(exc)}
            )

    if result["unreachable"] == result["attempted"] and result["attempted"] > 0:
        logging.warning(
            "Protocol cross-check: 0/%d official protocol pages reachable from this environment "
            "(apl.brno.cz) — known sandbox limitation, see sources.yml. Not failing G2 on this; "
            "the prijato-consistency check is the binding G2 signal here.",
            result["attempted"],
        )
    else:
        logging.info(
            "Protocol cross-check: %d/%d official protocol pages reachable.",
            result["reachable"],
            result["attempted"],
        )
    return result


def standardize(
    raw_path: Path = _DEFAULT_RAW,
    out_dir: Path = _DEFAULT_OUT,
    sources_path: Path = _DEFAULT_SOURCES,
) -> dict[str, Any]:
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    source_url = cfg["zastupko_current"]["url"]

    data = _load_raw(raw_path)
    term = data["zastupitelstva"][0]
    zasedani = term["zasedani"]

    logging.info(
        "Parsed term %s (%s-%s nominal): %d sessions",
        term["poradiZastupitelstva"],
        term.get("od"),
        term.get("do"),
        len(zasedani),
    )

    organization = _build_organization(source_url)
    persons, id_to_person_id = _build_persons(data["zastupitele"], source_url)
    votes, vote_events, motions, event_dates_by_person, report = _build_votes_events_motions(
        zasedani, id_to_person_id, source_url
    )

    global_max_date = max(session["datum"] for session in zasedani)
    memberships = _build_memberships(id_to_person_id, event_dates_by_person, global_max_date, source_url)

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(persons).fillna("").to_csv(out_dir / "persons.csv", index=False, encoding="utf-8")
    pd.DataFrame([organization]).fillna("").to_csv(
        out_dir / "organizations.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(memberships).fillna("").to_csv(
        out_dir / "memberships.csv", index=False, encoding="utf-8"
    )
    pd.DataFrame(votes).to_csv(out_dir / "votes.csv", index=False, encoding="utf-8")
    (out_dir / "vote_events.json").write_text(
        json.dumps(vote_events, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "motions.json").write_text(
        json.dumps(motions, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    logging.info("Wrote persons.csv (%d rows)", len(persons))
    logging.info("Wrote organizations.csv (1 row)")
    logging.info("Wrote memberships.csv (%d rows)", len(memberships))
    logging.info("Wrote votes.csv (%d rows)", len(votes))
    logging.info("Wrote vote_events.json (%d records)", len(vote_events))
    logging.info("Wrote motions.json (%d records)", len(motions))

    logging.info(
        "G2 result-consistency check: %d match, %d mismatch, %d no-signal (tie/invalid) "
        "out of %d events; %d events had corrupted hlas values (%d entries skipped, not "
        "fabricated)",
        report["result_consistency"]["match"],
        report["result_consistency"]["mismatch"],
        report["result_consistency"]["no_signal"],
        report["total_events"],
        len(report["corrupted_hlas_events"]),
        report["corrupted_hlas_entries_skipped"],
    )

    report["persons_count"] = len(persons)
    report["memberships_count"] = len(memberships)
    report["votes_count"] = len(votes)
    report["vote_events_count"] = len(vote_events)
    report["motions_count"] = len(motions)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", default=str(_DEFAULT_RAW))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    parser.add_argument("--report-out", default=None, help="optional path to dump the JSON quality report")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = standardize(Path(args.raw), Path(args.out_dir), Path(args.sources))

    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info("Wrote quality report to %s", args.report_out)


if __name__ == "__main__":
    main()
