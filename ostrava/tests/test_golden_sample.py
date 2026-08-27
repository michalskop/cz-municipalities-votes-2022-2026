"""G3 golden-sample regression test for Ostrava (C9).

Five real vote events, manually inspected against the cached raw HTML and pinned here so that any
future change to `ostrava/scripts/standardize.py` that silently changes their output fails this
test loudly instead of drifting unnoticed. Chosen to cover: a procedural test vote with no
resolution text, the first real resolution of the term, a vote with all five vote-options
non-zero (full vocabulary coverage), the newest available session (spanning the whole ~4-year
corpus from the oldest), and Miroslav Otisk's identity held stable across the whole term despite
his displayed academic title changing between vote pages (see standardize.py's module docstring
finding on title-variant merging — this is the concrete regression that finding guards against:
without it, "Bc. Miroslav Otisk" and "Bc. Miroslav Otisk, MSc., MBA" would silently become two
different people).

Re-running standardize.py against the same cached raw/ snapshot (fetched 2026-08-27, all 2116
votes across all 31 meetings, zero failed fetches) must reproduce these five events' vote rows,
vote-event counts/result and data-quality flags exactly.

Requires the raw pages to already be downloaded (run `python ostrava/scripts/downloader.py` first
if missing) — this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

# Loaded under a city-unique module name (not the bare "standardize" that praha/brno's own
# standardize.py also use) so running all three cities' test suites in one pytest process can't
# have this import silently resolve to another city's cached sys.modules entry — see
# pytest.ini's docstring for the collision this avoids (first hit when Brno's test file was
# added alongside Praha's).
_spec = importlib.util.spec_from_file_location("ostrava_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_DIR = _CITY_ROOT / "work" / "raw"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not (_RAW_DIR / "meetings.json").exists():
        pytest.skip(
            f"{_RAW_DIR / 'meetings.json'} not present — run `python ostrava/scripts/downloader.py` "
            "first. This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_dir=_RAW_DIR, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    vote_events = {
        ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))
    }
    motions = {
        m["id"]: m for m in json.loads((out_dir / "motions.json").read_text(encoding="utf-8"))
    }
    memberships = pd.read_csv(out_dir / "memberships.csv", dtype=str, keep_default_na=False)
    return {"votes": votes, "vote_events": vote_events, "motions": motions, "memberships": memberships}


def _vote_option(votes: pd.DataFrame, vote_event_id: str, voter_id: str) -> str | None:
    row = votes[(votes.vote_event_id == vote_event_id) & (votes.voter_id == voter_id)]
    if row.empty:
        return None
    assert len(row) == 1, f"expected exactly one vote row for {voter_id} on {vote_event_id}, got {len(row)}"
    return row.iloc[0]["option"]


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: 2022-10-19, meeting 202201 vote 0001 — procedural test vote, no resolution ────────
def test_procedural_test_vote_no_resolution(standardized):
    ve = standardized["vote_events"]["ostrava:vote-event:202201-0001"]
    motion = standardized["motions"]["ostrava:motion:202201-0001"]

    assert ve["start_date"] == "2022-10-19T09:24:53"
    assert ve["identifier"] == "202201/0001"
    assert _counts_dict(ve) == {"yes": 34, "no": 12, "abstain": 8, "absent": 1, "not voting": 0}
    assert ve["extras"]["resolution_number"] is None
    assert "text" not in motion, "procedural test vote has no usnesení block — text must be omitted, not fabricated"
    assert "identifier" not in motion


# ── Event 2: 2022-10-19, meeting 202201 vote 0002 — first real resolution of the term ───────────
def test_first_real_resolution(standardized):
    ve = standardized["vote_events"]["ostrava:vote-event:202201-0002"]
    motion = standardized["motions"]["ostrava:motion:202201-0002"]

    assert ve["identifier"] == "202201/0002"
    assert ve["extras"]["resolution_number"] == "0001/ZM2226/1"
    assert _counts_dict(ve) == {"yes": 54, "no": 0, "abstain": 0, "absent": 1, "not voting": 0}
    assert motion["identifier"] == "0001/ZM2226/1"
    assert "schvaluje" in motion["text"]


# ── Event 3: 2022-10-19, meeting 202201 vote 0020 — all five vote-options non-zero ──────────────
def test_full_vocabulary_coverage(standardized):
    ve = standardized["vote_events"]["ostrava:vote-event:202201-0020"]

    assert ve["extras"]["resolution_number"] == "0006/ZM2226/1"
    assert _counts_dict(ve) == {"yes": 33, "no": 4, "abstain": 16, "absent": 1, "not voting": 1}
    # published_totals.pritomno includes not-voting (present but chose not to press a button),
    # excludes only the genuinely absent — the specific bug this test pins (see
    # standardize.py's pritomno-formula comment): 33+4+16+1 = 54, matching the page's own
    # "Přítomno: 54", NOT 33+4+16 = 53.
    assert ve["extras"]["published_totals"]["pritomno"] == 54


# ── Event 4: 2026-06-24, meeting 202604 vote 0101 — newest available session ────────────────────
def test_newest_session(standardized):
    ve = standardized["vote_events"]["ostrava:vote-event:202604-0101"]

    assert ve["start_date"] == "2026-06-24T15:36:46"
    assert ve["identifier"] == "202604/0101"
    assert _counts_dict(ve) == {"yes": 41, "no": 0, "abstain": 0, "absent": 11, "not voting": 3}
    assert ve["extras"]["resolution_number"] == "2057/ZM2226/31"


# ── Event 5: Miroslav Otisk's identity held stable across ~4 years despite title drift ─────────
# Real observed variants on the source pages: "Bc. Miroslav Otisk" and "Bc. Miroslav Otisk, MSc.,
# MBA" — same real person (only one "Miroslav Otisk" exists in the corpus; 59 persons total
# matches Ostrava's real ~55-58-member assembly size with no sign of a name-based split). Without
# stripping titles/suffixes for the identity key, these would be two different person rows and his
# 2022 and 2026 votes would attribute to different (non-existent, half-populated) people.
def test_otisk_identity_stable_across_the_term(standardized):
    votes = standardized["votes"]
    memberships = standardized["memberships"]

    assert _vote_option(votes, "ostrava:vote-event:202201-0001", "ostrava:person:miroslav-otisk") == "yes"
    assert _vote_option(votes, "ostrava:vote-event:202604-0101", "ostrava:person:miroslav-otisk") == "yes"

    m = memberships[memberships.person_id == "ostrava:person:miroslav-otisk"]
    assert len(m) == 1
    assert m.iloc[0]["organization_id"] == "ostrava:org:zastupitelstvo-mesta-ostravy"
    assert m.iloc[0]["start_date"] == "2022-10-19"
    assert m.iloc[0]["end_date"] == "", "still active as of the last observed vote — end_date must be open"
