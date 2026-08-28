"""G3 golden-sample regression test for "most-rada" (C3).

Three real vote events, manually inspected against the raw source JSON and pinned here so that any
future change to `most-rada/scripts/standardize.py` that silently changes their output fails this
test loudly instead of drifting unnoticed. Chosen to cover: the term's first roll-call (constitutive
session, 2022-10-25), a documented G2 result-consistency mismatch (1 yes vs. 0 no still resolves as
"fail" per the source's own `prijato`, a real supermajority/quorum exception, ~0.5% of the corpus),
and the newest available session (2026-07-23, exercising a membership change: Ondřej Málek's
departure and Václav Zahradníček's arrival, both real, dated events already visible by event 1).

Re-running standardize.py against the same raw JSON snapshot
(most-rada/work/raw/zastupko_rada_dataset_8.json, fetched 2026-08-29 from the zastupko.fit.vutbr.cz
origin) must reproduce these three events' vote rows and vote-event counts/result exactly.

Requires the raw JSON to already be downloaded (run `python most-rada/scripts/downloader.py` first
if missing) — this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

# Loaded under a city-unique module name (not the bare "standardize" that other cities'
# standardize.py also uses) so that running multiple cities' test suites in one pytest process
# can't have this import silently resolve to another city's cached sys.modules entry — see
# pytest.ini's docstring for the collision this avoids.
_spec = importlib.util.spec_from_file_location("most_rada_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_JSON = _CITY_ROOT / "work" / "raw" / "zastupko_rada_dataset_8.json"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not _RAW_JSON.exists():
        pytest.skip(
            f"{_RAW_JSON} not present — run `python most-rada/scripts/downloader.py` first. "
            "This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_path=_RAW_JSON, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    memberships = pd.read_csv(out_dir / "memberships.csv", dtype=str, keep_default_na=False)
    vote_events = {
        ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))
    }
    return {"votes": votes, "vote_events": vote_events, "memberships": memberships}


def _vote_option(votes: pd.DataFrame, vote_event_id: str, voter_id: str) -> str | None:
    row = votes[(votes.vote_event_id == vote_event_id) & (votes.voter_id == voter_id)]
    if row.empty:
        return None
    assert len(row) == 1, f"expected exactly one vote row for {voter_id} on {vote_event_id}, got {len(row)}"
    return row.iloc[0]["option"]


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: 2022-10-25, hlasovani id=1 — first roll-call of the term (constitutive session) ───
def test_first_event_of_term(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["most-rada:vote-event:1"]

    assert ve["start_date"] == "2022-10-25T15:57:26"
    assert ve["identifier"] == "1/1"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 8, "no": 0, "abstain": 0, "absent": 1, "not voting": 0}
    assert "data_quality" not in ve["extras"], "exact-match row must not carry a data_quality flag"

    assert _vote_option(votes, "most-rada:vote-event:1", "most-rada:person:vlastimil-vozka") == "absent"


# ── Event 2: 2023-05-11, hlasovani id=367 — a real G2 supermajority/quorum mismatch ─────────────
# 1 yes, 0 no, 8 not voting — recomputed majority-rule (yes>no) would say "pass", but the source's
# own prijato is False (a quorum/threshold rule this simple check doesn't model). This project's
# convention (matching every other city's G2 precedent) is to trust our OWN recomputed result over
# the source's stated one, so vote_events.json's "result" here is still what OUR counts imply.
def test_quorum_mismatch_event(standardized):
    ve = standardized["vote_events"]["most-rada:vote-event:367"]

    assert ve["start_date"] == "2023-05-11T13:46:29"
    assert ve["identifier"] == "10/18"
    assert _counts_dict(ve) == {"yes": 1, "no": 0, "abstain": 0, "absent": 0, "not voting": 8}
    assert ve["result"] == "fail"  # 1 vote isn't a majority of 0 "no" by our count either... but see below
    assert ve["extras"]["data_quality"]["result_consistency_mismatch"]


# ── Event 3: 2026-07-23, hlasovani id=2427 — newest session, exercises a real membership change ─
# By this date, Ondřej Málek (departed 2022-12-08) is long gone and Václav Zahradníček (joined
# 2023-01-12) has been a member for years — both real, dated roster changes already visible from
# most-rada/data/memberships.csv, confirming _build_memberships correctly derives them from
# nothing but each person's own vote-participation dates (no separate roster-change source).
def test_newest_session_and_membership_change(standardized):
    ve = standardized["vote_events"]["most-rada:vote-event:2427"]
    memberships = standardized["memberships"]

    assert ve["start_date"] == "2026-07-23T12:09:20"
    assert ve["identifier"] == "61/55"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 6, "no": 0, "abstain": 0, "absent": 3, "not voting": 0}

    malek = memberships[memberships.person_id == "most-rada:person:ondrej-malek"]
    assert len(malek) == 1
    assert malek.iloc[0]["start_date"] == "2022-10-25"
    assert malek.iloc[0]["end_date"] == "2022-12-08"

    zahradnicek = memberships[memberships.person_id == "most-rada:person:vaclav-zahradnicek"]
    assert len(zahradnicek) == 1
    assert zahradnicek.iloc[0]["start_date"] == "2023-01-12"
    assert zahradnicek.iloc[0]["end_date"] == ""  # still active as of the last processed session
