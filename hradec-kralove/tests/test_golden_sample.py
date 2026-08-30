"""G3 golden-sample regression test for Hradec Králové (C9).

Four real vote events, manually inspected against the raw source JSON and pinned here so that any
future change to `hradec-kralove/scripts/standardize.py` that silently changes their output fails
this test loudly instead of drifting unnoticed. Chosen to cover: the first roll-call of the term, a
tied vote (the only source of a "no_signal" result-consistency classification in this feed, since
`platne` is always True — unlike Brno, which had a real invalid-vote event), a G2 prijato-mismatch
event, and the newest available session (2025-08-26 — see sources.yml's known_staleness_gap for why
this is the newest, not a more recent 2026 date).

Re-running standardize.py against the same raw JSON snapshot
(hradec-kralove/work/raw/zastupko_dataset_9.json, fetched 2026-08-30) must reproduce these four
events' vote rows and vote-event counts/result exactly.

Requires the raw JSON to already be downloaded (run `python hradec-kralove/scripts/downloader.py`
first if missing) — this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "hradec_kralove_standardize", _CITY_ROOT / "scripts" / "standardize.py"
)
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_JSON = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not _RAW_JSON.exists():
        pytest.skip(
            f"{_RAW_JSON} not present — run `python hradec-kralove/scripts/downloader.py` first. "
            "This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_path=_RAW_JSON, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    vote_events = {
        ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))
    }
    return {"votes": votes, "vote_events": vote_events}


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: 2022-10-24, hlasovani id=1 — first roll-call of the term ──────────────────────────
# Manually checked against the raw origin JSON: the constitutive session's organ election,
# platne=true, 35 yes / 2 absent, prijato=true.
def test_first_event_of_term(standardized):
    ve = standardized["vote_events"]["hradec-kralove:vote-event:1"]

    assert ve["start_date"] == "2022-10-24T16:30:34"
    assert ve["identifier"] == "202202/6"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 35, "no": 0, "abstain": 0, "absent": 2, "not voting": 0}
    assert "data_quality" not in ve["extras"], "exact-match row must not carry a data_quality flag"


# ── Event 2: 2022-12-13, hlasovani id=52 — a TIED vote (the only no_signal source in this feed) ─
# yes=15 == no=15 — must produce a "no_signal" result-consistency classification (no
# result_mismatches entry), regardless of the source's own prijato value.
def test_tied_vote_no_consistency_signal(standardized):
    ve = standardized["vote_events"]["hradec-kralove:vote-event:52"]

    assert ve["identifier"] == "202204/6"
    assert _counts_dict(ve) == {"yes": 15, "no": 15, "abstain": 3, "absent": 4, "not voting": 0}
    assert ve["result"] == "fail"


# ── Event 3: 2023-08-29, hlasovani id=409 — G2 prijato-mismatch ────────────────────────────────
# yes=11 > no=9, but prijato=false (an absolute-majority-with-abstention pattern, same class as
# Brno's/Most's documented G2 exceptions — 12 abstain means yes=11 is not an absolute majority of
# the body).
def test_prijato_mismatch(standardized):
    ve = standardized["vote_events"]["hradec-kralove:vote-event:409"]

    assert ve["identifier"] == "202307/12"
    assert _counts_dict(ve) == {"yes": 11, "no": 9, "abstain": 12, "absent": 4, "not voting": 1}
    assert ve["result"] == "fail"


# ── Event 4: 2025-08-26, hlasovani id=1627 — newest available session ──────────────────────────
# The 30th and last session in this feed (see sources.yml's known_staleness_gap — real meetings
# continued into 2026, this feed just hasn't been updated past this date).
def test_newest_session(standardized):
    ve = standardized["vote_events"]["hradec-kralove:vote-event:1627"]

    assert ve["start_date"].startswith("2025-08-26")
    assert ve["identifier"] == "202506/83"
    assert _counts_dict(ve) == {"yes": 16, "no": 0, "abstain": 2, "absent": 15, "not voting": 4}
    assert ve["result"] == "pass"
