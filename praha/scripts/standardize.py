"""Standardize Praha's wide-format roll-call CSV into dt-standard tables.

Source: the Golemio open-data CSV (praha/config/sources.yml, D8 / research/R1-praha.md). One row
per vote event (motion); 17 fixed metadata/count columns, one column per councilor (header = full
name with inline academic titles), and a trailing "neurčeno" (undetermined) column.

Two data-quality patterns are handled here, not papered over — see `_classify_row_quality`:

1. The "neurčeno" column (mostly overlooked in R1's research pass) turns out to carry a real vote
   option value on 740/2337 non-blank-aggregate rows. Including it in the recomputed tally
   resolves 616 of those rows to an exact match against the source's published pocetpro/
   pocetproti/pocetzdrzel aggregates — it is an anonymous "extra/substitute voter" bucket, not
   noise. We still do NOT attribute these votes to any named person (that would fabricate
   identity — G5), but we do record the option and count in the vote-event's `extras`.
2. After accounting for (1), exactly 124 rows remain off by exactly one vote in one option
   category (never more, never split across categories) — this is the substitute-vote pattern
   R1 documented (undercounted by a mid-term substitute with no dedicated column at all, not even
   the anonymous bucket). These are flagged (`unresolved_substitute_gap`) and logged, never
   silently dropped, never hard-failed — matching the plan's G2 instruction. Any mismatch that
   does NOT fit this exact shape (off by more than 1, or spread across more than one option) is
   treated as an unexplained anomaly and hard-fails the pipeline, since it would not match the
   documented, bounded pattern and could indicate an actual parsing bug.
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
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_RAW = _CITY_ROOT / "work" / "raw" / "Vysledky_hlasovani_ZHMP.csv"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "praha:org:zastupitelstvo-hmp"
ORG_NAME = "Zastupitelstvo hlavního města Prahy"

# Exact metadata column layout observed 2026-08-04 (see research/R1-praha.md). If the source
# changes this layout, fail loudly rather than silently misreading columns.
_EXPECTED_META_COLUMNS = [
    "cislotisku",
    "cislousneseni",
    "volobd",
    "datumjednani",
    "orgjednotka",
    "rokusneseni",
    "nazevtisku",
    "predkladatel",
    "poradi",
    "cislojednani",
    "datumcas",
    "kbodu",
    "pritomno",
    "nepritomno",
    "pocetpro",
    "pocetproti",
    "pocetzdrzel",
]

# Per-councilor vote vocabulary (research/R1-praha.md). Empty string = not a member at that date.
OPTION_MAP = {
    "Hlas pro": "yes",
    "Hlas proti": "no",
    "Zdržel se": "abstain",
    "Nehlasoval": "not voting",
    "Chyběl": "absent",
}

_COUNT_OPTION_TO_COLUMN = {
    "yes": "pocetpro",
    "no": "pocetproti",
    "abstain": "pocetzdrzel",
}
_COUNT_OPTION_TO_CSV_VOTE_VALUE = {
    "yes": "Hlas pro",
    "no": "Hlas proti",
    "abstain": "Zdržel se",
}


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _parse_person_header(raw_header: str) -> tuple[str, str, str]:
    """Split "Family  Given [titles]" into (family_name, given_name, title).

    Family/given are separated by 2+ spaces; given/title by 1+ spaces (occasionally 2). Titles
    may be absent (trailing spaces only) or contain internal punctuation/commas
    (e.g. "Ing. Ph.D., MSc."). See research/R1-praha.md — titles must NOT feed into the identity
    key (they can change over time, e.g. finishing a degree).
    """
    parts = [p.strip() for p in re.split(r"\s{2,}", raw_header) if p.strip() != ""]
    if not parts:
        raise ValueError(f"Cannot parse person column header: {raw_header!r}")
    family_name = parts[0]
    rest = " ".join(parts[1:]).strip()
    tokens = rest.split(" ", 1)
    given_name = tokens[0] if tokens and tokens[0] else ""
    title = tokens[1].strip() if len(tokens) > 1 else ""
    if not given_name:
        raise ValueError(f"Cannot parse given name from person column header: {raw_header!r}")
    return family_name, given_name, title


def _to_date(raw: str) -> str | None:
    """"03.11.2022 0:00:00" -> "2022-11-03"."""
    raw = (raw or "").strip()
    if not raw:
        return None
    date_part = raw.split(" ")[0]
    dd, mm, yyyy = date_part.split(".")
    return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"


def _to_datetime(raw: str) -> str | None:
    """datumcas is already ISO-ish, e.g. "2022-11-03T14:45:06"."""
    raw = (raw or "").strip()
    return raw or None


def _read_raw(raw_csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(raw_csv_path, dtype=str, keep_default_na=False, encoding="utf-8")

    actual_meta = list(df.columns[: len(_EXPECTED_META_COLUMNS)])
    if actual_meta != _EXPECTED_META_COLUMNS:
        raise ValueError(
            "Source CSV metadata column layout has changed (schema drift) — expected "
            f"{_EXPECTED_META_COLUMNS}, got {actual_meta}. Refusing to guess column meaning."
        )

    last_col = df.columns[-1]
    if last_col.strip().casefold() != "neurčeno":
        raise ValueError(
            "Expected the trailing column to be the 'neurčeno' (undetermined/anonymous-voter) "
            f"bucket, got {last_col!r}. This standardizer decodes that column explicitly "
            "(see module docstring) — refusing to run against an unexpected layout."
        )

    if df["cislotisku"].duplicated().any():
        dupes = df.loc[df["cislotisku"].duplicated(), "cislotisku"].tolist()
        raise ValueError(f"Duplicate cislotisku values (would break vote-event IDs): {dupes}")

    return df


def _build_organization(source_url: str) -> dict[str, Any]:
    return {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": json.dumps(
            [{"scheme": "praha:org_abbr", "identifier": "Zastupitelstvo hl. m. Prahy"}],
            ensure_ascii=False,
        ),
        "sources": json.dumps(
            [{"url": source_url, "note": "Praha open-data roll-call CSV, term 2022-2026"}],
            ensure_ascii=False,
        ),
    }


def _build_persons_and_memberships(
    df: pd.DataFrame,
    person_headers: list[str],
    dates: pd.Series,
    source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    global_min, global_max = dates.min(), dates.max()

    persons: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    header_to_person_id: dict[str, str] = {}
    slug_to_header: dict[str, str] = {}

    for header in person_headers:
        family_name, given_name, title = _parse_person_header(header)
        slug = _slugify(f"{given_name}-{family_name}")

        # G5 identity gate: two distinct roster entries must never silently collide.
        if slug in slug_to_header and slug_to_header[slug] != header:
            raise ValueError(
                f"G5 identity collision: columns {slug_to_header[slug]!r} and {header!r} both "
                f"slugify to {slug!r}. Refusing to silently merge — needs manual disambiguation "
                "(e.g. append a birth-year or sequence suffix to one of the IDs)."
            )
        slug_to_header[slug] = header

        person_id = f"praha:person:{slug}"
        header_to_person_id[header] = person_id

        active_mask = df[header] != ""
        active_dates = dates[active_mask]

        identifiers = [{"scheme": "praha:csv_column_header", "identifier": header.strip()}]
        if title:
            identifiers.append({"scheme": "praha:academic_title", "identifier": title})

        persons.append(
            {
                "id": person_id,
                "name": f"{given_name} {family_name}",
                "given_name": given_name,
                "family_name": family_name,
                "identifiers": json.dumps(identifiers, ensure_ascii=False),
                "sources": json.dumps(
                    [{"url": source_url, "note": f"CSV column header: {header.strip()!r}"}],
                    ensure_ascii=False,
                ),
            }
        )

        if active_dates.empty:
            logging.warning(
                "Person %s (%s) has a roster column but zero recorded votes — no membership "
                "row created for them; investigate before trusting attendance analyses for them.",
                person_id,
                header.strip(),
            )
            continue

        start_date = active_dates.min()
        end_date = active_dates.max()
        # Leave end_date open (still serving) if their last recorded vote is the dataset's last
        # observed session; otherwise they departed mid-term.
        end_date_str = None if end_date == global_max else end_date.strftime("%Y-%m-%d")

        memberships.append(
            {
                "id": f"praha:membership:{slug}:{ORG_ID}",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date_str or "",
                "sources": json.dumps(
                    [
                        {
                            "url": source_url,
                            "note": (
                                "start/end derived from first/last non-empty vote cell for this "
                                "councilor's column; open end_date = still active as of the last "
                                "observed session in this export."
                            ),
                        }
                    ],
                    ensure_ascii=False,
                ),
            }
        )

        if start_date != global_min:
            logging.info(
                "%s (%s) joined mid-term: first recorded vote %s (global start %s)",
                person_id,
                header.strip(),
                start_date.date(),
                global_min.date(),
            )
        if end_date_str:
            logging.info(
                "%s (%s) departed mid-term: last recorded vote %s (global end %s)",
                person_id,
                header.strip(),
                end_date.date(),
                global_max.date(),
            )

    return persons, memberships, header_to_person_id


def _classify_row_quality(
    row: pd.Series,
    person_headers: list[str],
    undet_header: str,
) -> dict[str, Any]:
    """Recompute per-option tallies (named councilors + the anonymous 'neurčeno' bucket) and
    compare against the source's published pocetpro/pocetproti/pocetzdrzel aggregates.

    Returns a dict describing the row's quality status; never raises for the documented,
    bounded 1-vote-gap pattern, but raises ValueError for anything larger/unexplained.
    """
    tally_named = {"yes": 0, "no": 0, "abstain": 0}
    for header in person_headers:
        val = row[header]
        if val == "Hlas pro":
            tally_named["yes"] += 1
        elif val == "Hlas proti":
            tally_named["no"] += 1
        elif val == "Zdržel se":
            tally_named["abstain"] += 1

    undet_val = row[undet_header]
    undet_option = OPTION_MAP.get(undet_val) if undet_val else None
    # Only yes/no/abstain are meaningful for the aggregate cross-check (Nehlasoval/Chyběl in the
    # bucket column, if it ever occurs, doesn't affect pocetpro/proti/zdrzel).
    tally_incl = dict(tally_named)
    if undet_option in ("yes", "no", "abstain"):
        tally_incl[undet_option] += 1

    raw_counts = {opt: row[col] for opt, col in _COUNT_OPTION_TO_COLUMN.items()}
    if any(v == "" for v in raw_counts.values()):
        return {
            "status": "aggregate_not_published",
            "tally_named": tally_named,
            "tally_incl": tally_incl,
            "undet_value": undet_val or None,
            "counts_used": tally_incl,  # fall back to our own recomputed tally
        }

    published = {opt: int(v) for opt, v in raw_counts.items()}
    diffs = {opt: published[opt] - tally_incl[opt] for opt in ("yes", "no", "abstain")}
    abs_sum = sum(abs(d) for d in diffs.values())

    if abs_sum == 0:
        status = "exact_match"
    elif abs_sum == 1:
        status = "unresolved_substitute_gap"
    else:
        raise ValueError(
            "G2 cross-check found an unexplained mismatch beyond the documented substitute-vote "
            f"pattern (expected at most a single unattributed vote): diffs={diffs}, "
            f"tally_named={tally_named}, tally_incl_neurceno={tally_incl}, published={published}. "
            "This does not match the R1-documented pattern (max 1-vote gap) — treating as a "
            "possible parsing bug rather than silently accepting it."
        )

    return {
        "status": status,
        "tally_named": tally_named,
        "tally_incl": tally_incl,
        "undet_value": undet_val or None,
        "diffs": diffs,
        "counts_used": published,
    }


def _build_votes_events_motions(
    df: pd.DataFrame,
    dates: pd.Series,
    person_headers: list[str],
    undet_header: str,
    header_to_person_id: dict[str, str],
    source_url: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    votes: list[dict[str, Any]] = []
    vote_events: list[dict[str, Any]] = []
    motions: list[dict[str, Any]] = []

    report = {
        "total_rows": int(len(df)),
        "exact_match": 0,
        "unresolved_substitute_gap": [],
        "aggregate_not_published": [],
        "neurceno_bucket_nonempty": 0,
    }

    for idx, row in df.iterrows():
        cislotisku = row["cislotisku"]
        vote_event_id = f"praha:vote-event:{cislotisku}"
        motion_id = f"praha:motion:{cislotisku}"

        for header in person_headers:
            val = row[header]
            if val == "":
                continue  # not a member of the assembly on this date
            option = OPTION_MAP.get(val)
            if option is None:
                raise ValueError(
                    f"Unmapped vote value {val!r} in column {header!r}, row cislotisku={cislotisku}. "
                    "Vocabulary is fixed/documented (research/R1-praha.md) — refusing to guess."
                )
            votes.append(
                {
                    "vote_event_id": vote_event_id,
                    "voter_id": header_to_person_id[header],
                    "voter_type": "person",
                    "option": option,
                }
            )

        quality = _classify_row_quality(row, person_headers, undet_header)

        if quality["undet_value"]:
            report["neurceno_bucket_nonempty"] += 1
        if quality["status"] == "exact_match":
            report["exact_match"] += 1
        elif quality["status"] == "unresolved_substitute_gap":
            report["unresolved_substitute_gap"].append(
                {
                    "cislotisku": cislotisku,
                    "datumjednani": row["datumjednani"],
                    "diffs": quality["diffs"],
                }
            )
        elif quality["status"] == "aggregate_not_published":
            report["aggregate_not_published"].append(
                {"cislotisku": cislotisku, "datumjednani": row["datumjednani"]}
            )

        counts_used = quality["counts_used"]
        counts = [{"option": opt, "value": int(counts_used[opt])} for opt in ("yes", "no", "abstain")]

        extras: dict[str, Any] = {
            "cislousneseni": row["cislousneseni"] or None,
            "orgjednotka": row["orgjednotka"] or None,
            "predkladatel": row["predkladatel"] or None,
            "poradi": row["poradi"] or None,
            "cislojednani": row["cislojednani"] or None,
            "kbodu": row["kbodu"].strip() or None,
            "pritomno": int(row["pritomno"]) if row["pritomno"] else None,
            "nepritomno": int(row["nepritomno"]) if row["nepritomno"] else None,
            "rokusneseni": row["rokusneseni"] or None,
            "volobd": row["volobd"] or None,
        }
        data_quality: dict[str, Any] = {}
        if quality["status"] == "unresolved_substitute_gap":
            data_quality["unresolved_substitute_gap"] = quality["diffs"]
            data_quality["note"] = (
                "Published aggregate is one vote higher than the sum of named-councilor + "
                "'neurčeno' bucket votes. Consistent with R1's documented mid-term substitute "
                "pattern (a councilor not present in this export's fixed column roster). Not "
                "attributable to any named person from this source alone."
            )
        elif quality["status"] == "aggregate_not_published":
            data_quality["aggregate_not_published"] = True
            data_quality["note"] = (
                "Source did not publish pocetpro/pocetproti/pocetzdrzel for this row; counts "
                "above are recomputed from named-councilor + 'neurčeno' bucket votes only."
            )
        if quality["undet_value"]:
            data_quality["unattributed_neurceno_vote"] = {
                "option": OPTION_MAP.get(quality["undet_value"], quality["undet_value"]),
                "note": (
                    "The source's anonymous 'neurčeno' bucket column had a vote on this row. The "
                    "option is known but the voter's identity is not — no votes.csv row was "
                    "created for it (never fabricate identity)."
                ),
            }
        if data_quality:
            extras["data_quality"] = data_quality

        start_datetime = _to_datetime(row["datumcas"]) or _to_date(row["datumjednani"])

        vote_events.append(
            {
                "id": vote_event_id,
                "identifier": cislotisku,
                "motion_id": motion_id,
                "organization_id": ORG_ID,
                "start_date": start_datetime,
                "result": None,
                "counts": counts,
                "sources": [{"url": source_url, "note": f"cislotisku={cislotisku}"}],
                "extras": extras,
            }
        )

        motions.append(
            {
                "id": motion_id,
                "identifier": cislotisku,
                "organization_id": ORG_ID,
                "date": _to_date(row["datumjednani"]),
                "text": (row["nazevtisku"] or "").strip() or None,
                "result": None,
                "sources": [{"url": source_url, "note": f"cislotisku={cislotisku}"}],
                "extras": {
                    "cislousneseni": row["cislousneseni"] or None,
                    "predkladatel": row["predkladatel"] or None,
                    "rokusneseni": row["rokusneseni"] or None,
                    "kbodu": row["kbodu"].strip() or None,
                },
            }
        )

    return votes, vote_events, motions, report


def standardize(
    raw_csv_path: Path = _DEFAULT_RAW,
    out_dir: Path = _DEFAULT_OUT,
    sources_path: Path = _DEFAULT_SOURCES,
) -> dict[str, Any]:
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    source_url = cfg["vysledky_hlasovani_zhmp"]["primary"]["url"]

    df = _read_raw(raw_csv_path)
    dates = pd.to_datetime(df["datumjednani"], format="%d.%m.%Y %H:%M:%S")

    person_headers = list(df.columns[len(_EXPECTED_META_COLUMNS) : -1])
    undet_header = df.columns[-1]
    logging.info(
        "Parsed %d vote-event rows, %d councilor columns, 1 anonymous bucket column (%r)",
        len(df),
        len(person_headers),
        undet_header.strip(),
    )

    organization = _build_organization(source_url)
    persons, memberships, header_to_person_id = _build_persons_and_memberships(
        df, person_headers, dates, source_url
    )
    votes, vote_events, motions, report = _build_votes_events_motions(
        df, dates, person_headers, undet_header, header_to_person_id, source_url
    )

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
    logging.info("Wrote organizations.csv (%d rows)", 1)
    logging.info("Wrote memberships.csv (%d rows)", len(memberships))
    logging.info("Wrote votes.csv (%d rows)", len(votes))
    logging.info("Wrote vote_events.json (%d records)", len(vote_events))
    logging.info("Wrote motions.json (%d records)", len(motions))

    logging.info(
        "G2 cross-check: %d/%d rows exact match; %d rows unresolved substitute-vote gap "
        "(documented R1 pattern, logged not failed); %d rows had no published aggregate; "
        "%d rows had a non-empty anonymous 'neurčeno' bucket vote",
        report["exact_match"],
        report["total_rows"],
        len(report["unresolved_substitute_gap"]),
        len(report["aggregate_not_published"]),
        report["neurceno_bucket_nonempty"],
    )
    if report["unresolved_substitute_gap"]:
        dates_involved = sorted({r["datumjednani"] for r in report["unresolved_substitute_gap"]})
        logging.warning(
            "Unresolved substitute-vote gap on %d rows, clustered on %d meeting date(s): %s",
            len(report["unresolved_substitute_gap"]),
            len(dates_involved),
            dates_involved,
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
