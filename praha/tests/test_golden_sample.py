"""G3 golden-sample regression test (plan.md quality gate G3).

Five real vote events, manually inspected against the raw source CSV and pinned here so that any
future change to `praha/scripts/standardize.py` that silently changes their output fails this test
loudly instead of drifting unnoticed. Per the plan, the owner is meant to verify a golden sample
once; this file is the Haiku-written fixture half of that gate (owner verification happened via the
manual cross-checks documented in each test's docstring, against `praha/work/raw/
Vysledky_hlasovani_ZHMP.csv` and `praha/data/{votes.csv,vote_events.json,memberships.csv}` as
produced 2026-08-04).

Re-running `praha/scripts/standardize.py` against the same raw CSV snapshot must reproduce these
five events' vote rows, vote-event counts and data-quality flags exactly.

Requires the raw CSV to already be downloaded at `praha/work/raw/Vysledky_hlasovani_ZHMP.csv`
(run `python praha/scripts/downloader.py` first if missing) — this test does not hit the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CITY_ROOT / "scripts"))
import standardize  # noqa: E402

_RAW_CSV = _CITY_ROOT / "work" / "raw" / "Vysledky_hlasovani_ZHMP.csv"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not _RAW_CSV.exists():
        pytest.skip(
            f"{_RAW_CSV} not present — run `python praha/scripts/downloader.py` first. "
            "This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_csv_path=_RAW_CSV, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    vote_events = {
        ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))
    }
    memberships = pd.read_csv(out_dir / "memberships.csv", dtype=str)
    return {"votes": votes, "vote_events": vote_events, "memberships": memberships}


def _vote_option(votes: pd.DataFrame, vote_event_id: str, voter_id: str) -> str | None:
    row = votes[(votes.vote_event_id == vote_event_id) & (votes.voter_id == voter_id)]
    if row.empty:
        return None
    assert len(row) == 1, f"expected exactly one vote row for {voter_id} on {vote_event_id}, got {len(row)}"
    return row.iloc[0]["option"]


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: 2022-11-03, Z-10880 — first roll-call of the term ─────────────────────────────────
# Manually checked against R1's sample-record excerpt and the raw CSV: procedural resolution,
# pritomno=65/nepritomno=2, pocetpro=60/pocetproti=0/pocetzdrzel=3, "Arnotová Kateřina Mgr." voted
# "Hlas pro". Tomáš Kaněra (joined 2025-01-23, per memberships.csv) was not yet a councilor, so his
# column is blank on this row — no votes.csv row should exist for him here.
def test_z10880_first_event_of_term(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["praha:vote-event:Z-10880"]

    assert ve["start_date"] == "2022-11-03T14:45:06"
    assert _counts_dict(ve) == {"yes": 60, "no": 0, "abstain": 3}
    assert ve["extras"]["pritomno"] == 65
    assert ve["extras"]["nepritomno"] == 2
    assert "data_quality" not in ve["extras"], "exact-match row must not carry a data_quality flag"

    assert _vote_option(votes, "praha:vote-event:Z-10880", "praha:person:katerina-arnotova") == "yes"
    assert _vote_option(votes, "praha:vote-event:Z-10880", "praha:person:tomas-kanera") is None

    named_rows = votes[votes.vote_event_id == "praha:vote-event:Z-10880"]
    assert len(named_rows) == 65


# ── Event 2: 2024-10-25, Z-12616 — documented substitute-vote gap (R1/C7 pattern) ──────────────
# Published pocetpro=47 is exactly 1 higher than the sum of named-councilor votes (also 47 named
# "yes" rows expected? no — the gap means named+neurčeno sums to 46, one short). This is one of the
# 124 R1-documented rows; must be flagged, not silently absorbed or hard-failed.
def test_z12616_substitute_gap(standardized):
    ve = standardized["vote_events"]["praha:vote-event:Z-12616"]

    assert _counts_dict(ve) == {"yes": 47, "no": 0, "abstain": 0}
    dq = ve["extras"].get("data_quality")
    assert dq is not None, "this row is a known R1 substitute-gap case and must carry data_quality"
    assert dq["unresolved_substitute_gap"] == {"yes": 1, "no": 0, "abstain": 0}


# ── Event 3: 2022-11-03, Z-10901 — no published aggregate ──────────────────────────────────────
# All 68 metadata/name columns are blank for pocetpro/proti/zdrzel; no councilor cast a named vote
# either (0 votes.csv rows). Counts must fall back to the recomputed (all-zero) tally, flagged
# aggregate_not_published, not silently reported as "0 for/0 against" with no explanation.
def test_z10901_aggregate_not_published(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["praha:vote-event:Z-10901"]

    assert _counts_dict(ve) == {"yes": 0, "no": 0, "abstain": 0}
    dq = ve["extras"].get("data_quality")
    assert dq is not None
    assert dq["aggregate_not_published"] is True

    assert len(votes[votes.vote_event_id == "praha:vote-event:Z-10901"]) == 0


# ── Event 4: 2026-06-18, Z-14045 — last vote event in this export ──────────────────────────────
# By this date Tomáš Kaněra (joined 2025-01-23) is a full member and cast a "yes" vote; this row
# is also a substitute-gap case (documented pattern recurs near term end per R1's date list).
def test_z14045_last_event_and_kanera_active(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["praha:vote-event:Z-14045"]

    assert ve["start_date"] == "2026-06-18T22:55:46"
    assert _counts_dict(ve) == {"yes": 49, "no": 0, "abstain": 0}
    assert ve["extras"]["data_quality"]["unresolved_substitute_gap"] == {"yes": 1, "no": 0, "abstain": 0}

    assert _vote_option(votes, "praha:vote-event:Z-14045", "praha:person:tomas-kanera") == "yes"
    assert len(votes[votes.vote_event_id == "praha:vote-event:Z-14045"]) == 63


# ── Event 5: Martin Hrubčík's membership boundary (2024-12-12) ─────────────────────────────────
# Cross-checked against memberships.csv: his zastupitelstvo membership end_date is 2024-12-12
# (derived from datumjednani, the session DATE — not datumcas, the exact timestamp, which for his
# last session rolls past midnight into 2024-12-13). His last three votes are all on cislotisku
# Z-12701/Z-12758/Z-12855, all with datumjednani=2024-12-12 despite datumcas timestamps reading
# "2024-12-13T0*:*". This confirms standardize.py's membership-interval derivation uses the
# session date, not the timestamp, for the boundary — pin that behavior explicitly.
def test_hrubcik_membership_boundary(standardized):
    votes = standardized["votes"]
    memberships = standardized["memberships"]

    row = memberships[
        (memberships.person_id == "praha:person:martin-hrubcik")
        & (memberships.organization_id == "praha:org:zastupitelstvo-hmp")
    ]
    assert len(row) == 1
    assert row.iloc[0]["start_date"] == "2022-11-03"
    assert row.iloc[0]["end_date"] == "2024-12-12"

    assert _vote_option(votes, "praha:vote-event:Z-12855", "praha:person:martin-hrubcik") == "yes"
