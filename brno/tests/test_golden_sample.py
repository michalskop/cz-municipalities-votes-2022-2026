"""G3 golden-sample regression test for Brno (C3).

Five real vote events, manually inspected against the raw source JSON and pinned here so that any
future change to `brno/scripts/standardize.py` that silently changes their output fails this test
loudly instead of drifting unnoticed. Chosen to cover: the first roll-call of the term, an invalid
(platne=false) vote, a G2 prijato-mismatch event (also covering an early Petr Bořecký vote under
the merged brno:person:petr-borecky-3 identity, see standardize.py's _KNOWN_ID_RENUMBERINGS), the
one documented unmapped-hlas-value ("T"/secret ballot) event, and the newest available session
(exercising the origin server's datum_od/datum_do session-date schema, see _session_date()).

Re-running standardize.py against the same raw JSON snapshot (brno/work/raw/zastupko_dataset_9.json,
fetched 2026-08-26 from the zastupko.fit.vutbr.cz origin) must reproduce these five events' vote
rows, vote-event counts/result and data-quality flags exactly.

Requires the raw JSON to already be downloaded (run `python brno/scripts/downloader.py` first if
missing) — this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

# Loaded under a city-unique module name (not the bare "standardize" that
# praha/scripts/standardize.py also uses) so that running both cities' test suites in one pytest
# process can't have this import silently resolve to the other city's cached sys.modules entry —
# see pytest.ini's docstring for the collision this avoids.
_spec = importlib.util.spec_from_file_location("brno_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_JSON = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not _RAW_JSON.exists():
        pytest.skip(
            f"{_RAW_JSON} not present — run `python brno/scripts/downloader.py` first. "
            "This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_path=_RAW_JSON, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    vote_events = {
        ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))
    }
    return {"votes": votes, "vote_events": vote_events}


def _vote_option(votes: pd.DataFrame, vote_event_id: str, voter_id: str) -> str | None:
    row = votes[(votes.vote_event_id == vote_event_id) & (votes.voter_id == voter_id)]
    if row.empty:
        return None
    assert len(row) == 1, f"expected exactly one vote row for {voter_id} on {vote_event_id}, got {len(row)}"
    return row.iloc[0]["option"]


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: 2022-10-20, hlasovani id=3923 — first roll-call of the term ───────────────────────
# Manually checked against the raw origin JSON: procedural technical-point vote, platne=true,
# 53 yes / 1 absent / 1 not-voting, prijato=true.
def test_first_event_of_term(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["brno:vote-event:3923"]

    assert ve["start_date"] == "2022-10-20T08:36:15"
    assert ve["identifier"] == "Z9/01/1"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 53, "no": 0, "abstain": 0, "absent": 1, "not voting": 1}
    assert ve["extras"]["platne"] is True
    assert "data_quality" not in ve["extras"], "exact-match row must not carry a data_quality flag"


# ── Event 2: 2022-10-20, hlasovani id=3930 — mayoral election, INVALID vote ────────────────────
# platne=false (the council's own vote-validity flag) — this must produce a "no_signal"
# result-consistency classification (no result_mismatch entry), regardless of prijato/tally.
def test_invalid_vote_no_consistency_signal(standardized):
    ve = standardized["vote_events"]["brno:vote-event:3930"]

    assert ve["identifier"] == "Z9/01/8"
    assert ve["extras"]["platne"] is False
    assert _counts_dict(ve) == {"yes": 41, "no": 5, "abstain": 7, "absent": 1, "not voting": 1}
    assert ve["result"] == "pass"


# ── Event 3: 2022-11-15, hlasovani id=3969 — G2 prijato-mismatch + early Bořecký merge check ───
# yes=6 > no=3, but prijato=false (absolute-majority-with-abstention pattern documented in
# sources.yml's g2_cross_check_approach — 36 abstain + 9 not-voting means yes=6 is not an absolute
# majority of the 55-member body). Also: source id 125 (re-mapped to brno:person:petr-borecky-3,
# see _KNOWN_ID_RENUMBERINGS) voted "abstain" here — this is BEFORE his Dec 2025 ANO expulsion,
# confirming the merged identity's vote history extends back to the term's start, not just from
# his renumbering date forward.
def test_prijato_mismatch_and_early_borecky_vote(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["brno:vote-event:3969"]

    assert ve["start_date"] == "2022-11-15T08:58:27"
    assert ve["identifier"] == "Z9/02/7"
    assert _counts_dict(ve) == {"yes": 6, "no": 3, "abstain": 36, "absent": 1, "not voting": 9}
    assert ve["result"] == "fail"
    assert ve["extras"]["platne"] is True

    assert _vote_option(votes, "brno:vote-event:3969", "brno:person:petr-borecky-3") == "abstain"


# ── Event 4: 2023-10-18, hlasovani id=4962 — the one documented unmapped-hlas-value event ──────
# 48/55 entries are "T" (Tajná/secret ballot, schema-documented but intentionally unmapped) on the
# origin server — no votes.csv row for those 48, never fabricated, and extras.data_quality records
# exactly which source ids were affected.
def test_unmapped_hlas_value_event(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["brno:vote-event:4962"]

    assert ve["identifier"] == "Z9/11/12"
    assert _counts_dict(ve) == {"yes": 1, "no": 0, "abstain": 0, "absent": 6, "not voting": 0}
    dq = ve["extras"]["data_quality"]["corrupted_hlas_values"]
    assert dq["affected_person_source_ids"] == [
        1, 4, 8, 9, 14, 16, 21, 24, 28, 29, 30, 32, 34, 35, 36, 39, 40, 41, 43, 45, 53, 56, 91, 92,
        93, 94, 96, 97, 99, 100, 102, 103, 104, 105, 106, 107, 108, 109, 110, 112, 113, 114, 115,
        116, 117, 118, 119, 125,
    ]

    # None of the 48 affected people (incl. petr-borecky-3, via source id 125) get a votes.csv row.
    assert _vote_option(votes, "brno:vote-event:4962", "brno:person:petr-borecky-3") is None


# ── Event 5: 2026-06-23, hlasovani id=7173 — newest session, origin-only schema ────────────────
# Z9/36 exists only on the zastupko.fit.vutbr.cz origin (absent from the old brno.zastupko.cz
# mirror, see sources.yml's mirror_vs_origin note) and uses the origin's datum_od/datum_do session
# fields rather than the documented `datum` field — exercises _session_date()'s fallback. Also:
# Bořecký/petr-borecky-3's later (post-expulsion, "Brno klidem") vote, confirming the merged
# identity's history extends all the way to the latest session too, not just the earliest.
def test_newest_session_and_later_borecky_vote(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["brno:vote-event:7173"]

    assert ve["start_date"] == "2026-06-23T15:26:19"
    assert ve["identifier"] == "Z9/36/24"
    assert _counts_dict(ve) == {"yes": 45, "no": 0, "abstain": 2, "absent": 8, "not voting": 0}
    assert ve["result"] == "pass"

    assert _vote_option(votes, "brno:vote-event:7173", "brno:person:petr-borecky-3") == "yes"
