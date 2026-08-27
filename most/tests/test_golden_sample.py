"""G3 golden-sample regression test for Most (C3).

Four real vote events, manually inspected against the raw source JSON and pinned here so that any
future change to `most/scripts/standardize.py` that silently changes their output fails this test
loudly instead of drifting unnoticed. Chosen to cover: the first roll-call of the term, a "fail"
result event (Most's 2022-2026 term has ZERO platne=false events — unlike Brno — so this is the
best available diversity signal for the result-consistency path), the departing council member
Pavel Lisický's last recorded vote (his membership row's end_date, 2023-02-16, is derived exactly
from this event — see standardize.py's `_build_memberships`), and the newest available session.

Re-running standardize.py against the same raw JSON snapshot (most/work/raw/zastupko_dataset_8.json,
fetched 2026-08-27 from the zastupko.fit.vutbr.cz origin) must reproduce these four events' vote
rows, vote-event counts/result and data-quality flags exactly.

Requires the raw JSON to already be downloaded (run `python most/scripts/downloader.py` first if
missing) — this test does not hit the network.
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
_spec = importlib.util.spec_from_file_location("most_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_JSON = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_8.json"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not _RAW_JSON.exists():
        pytest.skip(
            f"{_RAW_JSON} not present — run `python most/scripts/downloader.py` first. "
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


# ── Event 1: 2022-10-24, hlasovani id=127 — first roll-call of the term ────────────────────────
# Manually checked against the raw origin JSON: procedural vote, platne=true, 42 yes / 2 abstain /
# 1 absent (out of 49 members — no "not voting" entries on this event).
def test_first_event_of_term(standardized):
    ve = standardized["vote_events"]["most:vote-event:127"]

    assert ve["start_date"] == "2022-10-24T13:20:09"
    assert ve["identifier"] == "1/3"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 42, "no": 0, "abstain": 2, "absent": 1, "not voting": 0}
    assert ve["extras"]["platne"] is True
    assert "data_quality" not in ve["extras"], "exact-match row must not carry a data_quality flag"


# ── Event 2: 2022-10-24, hlasovani id=129 — a "fail" result event ──────────────────────────────
# Most's full 2022-2026 term has ZERO platne=false events (confirmed by a full scan while building
# this test) — unlike Brno, which has an invalid-mayoral-vote example. A prijato=false event is the
# best available diversity signal here instead: 31 no vs 5 yes, correctly resolves to "fail".
def test_fail_result_event(standardized):
    ve = standardized["vote_events"]["most:vote-event:129"]

    assert ve["identifier"] == "1/5"
    assert ve["extras"]["platne"] is True
    assert _counts_dict(ve) == {"yes": 5, "no": 31, "abstain": 7, "absent": 1, "not voting": 1}
    assert ve["result"] == "fail"


# ── Event 3: 2023-02-16, hlasovani id=215 — Pavel Lisický's last recorded vote ─────────────────
# most:person:pavel-lisicky's membership row ends exactly on this session's date (2023-02-16),
# derived from this being the last session with any recorded zastupiteleHlasy entry for him (an
# "absent" mark, not a "yes"/"no" — presence in the roll call, not the vote choice, is what feeds
# _build_memberships). Confirms the end_date wasn't accidentally sourced from a later session.
def test_departing_member_last_vote_matches_membership_end(standardized):
    votes = standardized["votes"]
    memberships = standardized["memberships"]
    ve = standardized["vote_events"]["most:vote-event:215"]

    assert ve["start_date"] == "2023-02-16T14:57:20"
    assert ve["identifier"] == "4/14"
    assert _counts_dict(ve) == {"yes": 41, "no": 0, "abstain": 0, "absent": 4, "not voting": 0}
    assert ve["result"] == "pass"

    assert _vote_option(votes, "most:vote-event:215", "most:person:pavel-lisicky") == "absent"

    row = memberships[memberships.person_id == "most:person:pavel-lisicky"]
    assert len(row) == 1
    assert row.iloc[0]["start_date"] == "2022-10-24"
    assert row.iloc[0]["end_date"] == "2023-02-16"


# ── Event 4: 2026-06-18, hlasovani id=615 — newest available session ───────────────────────────
# Exercises the origin's datum_od/datum_do session-date schema at the far end of the term (same
# schema as session 1 — Most never had a datum-only legacy form, unlike Brno's mid-term drift).
def test_newest_session(standardized):
    ve = standardized["vote_events"]["most:vote-event:615"]

    assert ve["start_date"] == "2026-06-18T15:44:57"
    assert ve["identifier"] == "24/20"
    assert _counts_dict(ve) == {"yes": 32, "no": 0, "abstain": 6, "absent": 7, "not voting": 0}
    assert ve["result"] == "pass"
