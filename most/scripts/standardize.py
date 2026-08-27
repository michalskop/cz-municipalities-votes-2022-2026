"""Standardize Most's zastupko.cz nested JSON feed into dt-standard tables.

Source: most/config/sources.yml's `zastupko_current` entry — a single JSON document (dataset id 8,
term 2022-2026), same shape and same shared FIT VUT backend as brno/scripts/standardize.py already
handles: municipalita -> politickeSubjekty -> zastupitele -> zastupitelstva[0] (koalice, lidr,
zasedani[] -> hlasovani[] -> zastupiteleHlasy[]). This file is Brno's standardize.py ported with
city-specific constants changed (ORG_ID/NAME, dataset id) — the underlying schema, vocabulary, and
session-date handling (`_session_date`'s `datum`/`datum_od` tolerance) are identical, confirmed
directly against Most's own data before writing this, not assumed from Brno's precedent alone.

Scope boundary (matches every other city's precedent): this standardizer builds ONLY the bare
assembly organization + membership (a person's tenure as a council member, derived from their own
vote participation dates). It deliberately does NOT build party/coalition organizations or
memberships — that's a later C4-equivalent phase needing owner sign-off (D7).

Real data-quality findings confirmed here (2026-08-27, a fresh scan of Most's own data — NOT
copied from Brno's findings, which don't necessarily apply):
1. **Vote vocabulary**: same 5-value single-letter codes as Brno's ("A"/"N"/"Z"/"–"/"X"), no
   unmapped/corrupted values found in a full scan of the 2022-2026 dataset — cleaner than Brno's
   data (which had one isolated event with an unmapped "T" value).
2. **G5 namesake collisions**: none found in a full scan of the 49-member zastupitele list.
   `_build_persons` still guards for this the same way Brno's does (numeric-suffix
   disambiguation, never merge) in case a mid-term substitute introduces one later.
3. **`_KNOWN_ID_RENUMBERINGS`**: kept as an empty table (see below) — the MECHANISM Brno's
   Bořecký case needed is ported in case Most's data ever exhibits the same source-side id-reissue
   pattern, but no case has been found here as of this writing. Do not copy Brno's actual entries
   into this table; they're specific to a real, cited Brno person.
4. **G2 cross-check**: same self-consistency signal as Brno (no independent aggregate published;
   compare recomputed yes/no majority against the source's own `prijato` boolean).
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
_DEFAULT_RAW = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_8.json"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "most:org:zastupitelstvo-mesta-mostu"
ORG_NAME = "Zastupitelstvo města Mostu"

# schema-CZ.json's `hlas` enum has 7 values; only these 5 have ever been observed in Most's live
# data (checked 2026-08-27, full scan). Deliberately NOT mapping O/T — if they ever appear, fail
# loudly rather than guess a dt-standard option for "excused"/"secret ballot".
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
            f"Expected exactly 1 term (zastupitelstva) in dataset 8, got "
            f"{len(data['zastupitelstva'])} — refusing to guess which one applies."
        )
    return data


def _build_organization(source_url: str) -> dict[str, Any]:
    return {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": json.dumps(
            [{"scheme": "most:org_abbr", "identifier": "ZMM"}], ensure_ascii=False
        ),
        "sources": json.dumps(
            [{"url": source_url, "note": "zastupko.cz feed, dataset id 8, term 2022-2026"}],
            ensure_ascii=False,
        ),
    }


# See module docstring finding #3: kept as an EMPTY table, mirroring the mechanism
# brno/scripts/standardize.py needed for a real, cited source-id reissue (Petr Bořecký, id 3->125)
# — not found in Most's data as of this writing. Never add an entry here without an independent
# citation matching Brno's own bar (see that file's table for the citation shape required); an id
# change with no citation must mint a new person instead (the default behavior with this empty).
_KNOWN_ID_RENUMBERINGS: dict[int, dict[str, Any]] = {}


def _build_persons(
    zastupitele: list[dict[str, Any]], source_url: str
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Returns (persons rows, {idZastupitel: person_id}). Handles the G5 namesake collision by
    suffixing the source's own numeric id onto every colliding slug (never merges two distinct
    people). Separately handles _KNOWN_ID_RENUMBERINGS (never merges two distinct people either —
    it re-maps a single real person's OLD source id to their NEW one, both citing independent
    confirmation; see that table's docstring)."""
    slug_to_ids: dict[str, list[int]] = {}
    for z in zastupitele:
        slug = _slugify(f"{z['jmeno']}-{z['prijmeni']}")
        slug_to_ids.setdefault(slug, []).append(z["id"])

    persons: list[dict[str, Any]] = []
    id_to_person_id: dict[int, str] = {}

    for z in zastupitele:
        renumbering = _KNOWN_ID_RENUMBERINGS.get(z["id"])
        if renumbering is not None:
            person_id = renumbering["stable_person_id"]
            logging.info(
                "Source id %d re-mapped to the already-established %s (former source id %d) "
                "per _KNOWN_ID_RENUMBERINGS — see that table's citations.",
                z["id"],
                person_id,
                renumbering["former_source_id"],
            )
        else:
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
            person_id = f"most:person:{person_slug}"

        id_to_person_id[z["id"]] = person_id

        identifiers = [{"scheme": "most:zastupko_id", "identifier": str(z["id"])}]
        if renumbering is not None:
            identifiers.append(
                {
                    "scheme": "most:zastupko_id_former",
                    "identifier": str(renumbering["former_source_id"]),
                }
            )
        for alias in z.get("aliasy") or []:
            former = f"{alias.get('jmeno', '')} {alias.get('prijmeni', '')}".strip()
            if former:
                identifiers.append({"scheme": "most:former_name", "identifier": former})

        sources_list = [{"url": source_url, "note": f"zastupko.cz idZastupitel={z['id']}"}]
        if renumbering is not None:
            sources_list.extend(renumbering["citations"])

        persons.append(
            {
                "id": person_id,
                "name": f"{z['jmeno']} {z['prijmeni']}",
                "given_name": z["jmeno"],
                "family_name": z["prijmeni"],
                "identifiers": json.dumps(identifiers, ensure_ascii=False),
                "sources": json.dumps(sources_list, ensure_ascii=False),
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
                "id": f"most:membership:{person_id.split(':', 2)[2]}:{ORG_ID}",
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


def _session_date(session: dict[str, Any]) -> str:
    """A session's date, tolerant of the schema-CZ.json-documented `datum` field AND the
    undocumented `datum_od`/`datum_do` shape (same tolerance as Brno's standardize.py — Most's
    data already uses the `datum_od`/`datum_do` variant exclusively, confirmed 2026-08-27, but
    kept tolerant of both in case that ever changes). Neither present is a real schema break, not
    something to guess past — fail loudly.
    """
    if "datum" in session:
        return session["datum"]
    if "datum_od" in session:
        return session["datum_od"]
    raise KeyError(
        f"session {session.get('cislo')!r} (id={session.get('id')!r}) has neither 'datum' nor "
        "'datum_od' — unrecognized session-date schema, see _session_date's docstring"
    )


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
        session_date = _session_date(session)

        for h in session["hlasovani"]:
            report["total_events"] += 1
            hlasovani_id = h["id"]
            vote_event_id = f"most:vote-event:{hlasovani_id}"
            motion_id = f"most:motion:{hlasovani_id}"

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
                        "Source has an unmapped hlas value for these people on this event — no "
                        "vote recorded for them here; never fabricated."
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
    recomputed counts. Never raises — network unavailability is logged, not treated as a gate
    failure."""
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
            "Protocol cross-check: 0/%d official protocol pages reachable from this environment. "
            "Not failing G2 on this; the prijato-consistency check is the binding G2 signal here.",
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

    global_max_date = max(_session_date(session) for session in zasedani)
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
