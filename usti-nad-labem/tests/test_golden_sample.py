"""G3 golden-sample regression test for Ústí nad Labem (C3).

Four real vote events, hand-verified against the raw source and pinned here so a future change to
`usti-nad-labem/scripts/standardize.py` that silently changes their output fails this test loudly.
Chosen to cover real format variance discovered ACROSS THE FULL 24-meeting corpus during the build
(the original research pass sampled only 5 meetings and missed several of these -- see
standardize.py's module docstring and this repo's memory: usti-nad-labem-source-research):
  - meeting 24, vote 1: the newest meeting, standard "Bod N: Title" format.
  - meeting 1, vote 13: the term's constitutive session, a "Nepřijato usnesení" (rejected) result
    -- exercises the fail-result path with a real abstain-heavy vote (14 yes, 20 abstain).
  - meeting 24, vote 27: exercises the "Bod N. Title" period-separator variant (vs. the usual
    colon) -- "Bod 35a. protinávrh Ing.arch. Osleje ...".
  - meeting 15, vote 1: exercises the "Zastupitelstva STATUTÁRNÍHO města Ústí nad Labem" header
    wording variant (vs. the usual "Zastupitelstva města Ústí nad Labem") -- confirms the header
    regex's date/meeting-number extraction is tolerant of this, not brittle to exact wording.

Re-running standardize.py against the same raw PDF snapshot (usti-nad-labem/work/raw/pdfs/,
fetched 2026-08-29) must reproduce these four events' vote rows and vote-event counts/result
exactly.

Requires the raw PDFs to already be downloaded (run `python usti-nad-labem/scripts/downloader.py`
first if missing) -- this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("usti_nad_labem_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_DIR = _CITY_ROOT / "work" / "raw"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not (_RAW_DIR / "manifest.json").exists():
        pytest.skip(
            f"{_RAW_DIR / 'manifest.json'} not present -- run `python usti-nad-labem/scripts/downloader.py` "
            "first. This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_dir=_RAW_DIR, out_dir=out_dir)

    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    motions = {m["id"]: m for m in json.loads((out_dir / "motions.json").read_text(encoding="utf-8"))}
    vote_events = {ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))}
    return {"votes": votes, "vote_events": vote_events, "motions": motions}


def _counts_dict(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


# ── Event 1: meeting 24 (2026-06-15), vote 1 -- newest meeting, standard "Bod N:" format ───────
def test_newest_meeting_first_vote(standardized):
    ve = standardized["vote_events"]["usti-nad-labem:vote-event:24-1"]

    assert ve["start_date"] == "2026-06-15"
    assert ve["identifier"] == "24/1"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 34, "no": 0, "abstain": 0, "absent": 3, "not voting": 0}


# ── Event 2: meeting 1 (2022-10-24), vote 13 -- constitutive session, a real "Nepřijato" result ─
def test_constitutive_session_rejected_vote(standardized):
    ve = standardized["vote_events"]["usti-nad-labem:vote-event:1-13"]

    assert ve["start_date"] == "2022-10-24"
    assert ve["identifier"] == "1/13"
    assert ve["result"] == "fail"
    assert _counts_dict(ve) == {"yes": 14, "no": 0, "abstain": 20, "absent": 3, "not voting": 0}


# ── Event 3: meeting 24, vote 27 -- "Bod N. Title" period-separator format variant ─────────────
def test_bod_period_separator_variant(standardized):
    ve = standardized["vote_events"]["usti-nad-labem:vote-event:24-27"]
    motion = standardized["motions"]["usti-nad-labem:motion:24-27"]

    assert ve["identifier"] == "24/27"
    assert ve["result"] == "fail"
    assert _counts_dict(ve) == {"yes": 10, "no": 0, "abstain": 22, "absent": 5, "not voting": 0}
    assert motion["text"].startswith("35a. protinávrh Ing.arch. Osleje")


# ── Event 4: meeting 15 (2024-10-02), vote 1 -- "Zastupitelstva STATUTÁRNÍHO města" header ──────
# variant (vs. the usual "Zastupitelstva města Ústí nad Labem") -- confirms date/meeting-number
# extraction tolerates the wording difference rather than requiring an exact city-name match.
def test_statutarniho_header_variant(standardized):
    ve = standardized["vote_events"]["usti-nad-labem:vote-event:15-1"]

    assert ve["start_date"] == "2024-10-02"
    assert ve["identifier"] == "15/1"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 32, "no": 0, "abstain": 0, "absent": 5, "not voting": 0}
