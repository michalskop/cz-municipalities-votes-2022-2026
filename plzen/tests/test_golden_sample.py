"""G3 golden-sample regression test for Plzeň (C3).

Four real vote events, hand-verified against the raw source and pinned here so a future change to
`plzen/scripts/standardize.py` that silently changes their output fails this test loudly. Chosen
to cover all THREE protocol format eras (see standardize.py's module docstring for the full
era1/era2/era3 background):
  - point 98555: era1 (cp1250 HTML), the term's first roll-call vote.
  - point 98557: era1, a documented G2 result-consistency MISMATCH (recomputed yes>no majority
    disagrees with the source's own stated "Návrh nebyl přijat" -- a real supermajority/quorum
    exception, ~1% rate across the whole era1 corpus, same class of exception Brno/Ostrava also
    see and don't hard-fail on).
  - point 113728: era2 (UTF-16LE HTML), the 29th ZMP session (2026-02-05).
  - point 113730: era3 (PDF, current/ongoing format), also cross-checks that "Jiří Lodr" resolves
    to ONE merged identity across eras despite one era2 meeting's source HTML containing a real
    typo ("JIří" instead of "Jiří" -- see standardize.py's family-name comma-suffix-stripping
    comment for the related, deliberately-NOT-"fixed" MBA/Ph.D. suffix-merging case this same
    identity-stability logic also handles).

Re-running standardize.py against the same raw snapshot (plzen/work/raw/, fetched 2026-08-28) must
reproduce these four events' vote rows and vote-event counts/result exactly.

Requires the raw corpus to already be downloaded (run `python plzen/scripts/downloader.py` first
if missing) -- this test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("plzen_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_DIR = _CITY_ROOT / "work" / "raw"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not (_RAW_DIR / "manifest.json").exists():
        pytest.skip(
            f"{_RAW_DIR / 'manifest.json'} not present -- run `python plzen/scripts/downloader.py` "
            "first. This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_dir=_RAW_DIR, out_dir=out_dir)

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


# ── Event 1: 2022-10-18, point 98555 -- era1, first roll-call of the term ──────────────────────
def test_era1_first_event_of_term(standardized):
    ve = standardized["vote_events"]["plzen:vote-event:98555"]

    assert ve["start_date"] == "2022-10-18T14:53:43"
    assert ve["identifier"] == "3683/5"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 31, "no": 4, "abstain": 5, "absent": 2, "not voting": 5}
    assert ve["extras"]["era"] == "era1"


# ── Event 2: 2022-10-18, point 98557 -- era1, a real G2 supermajority/quorum mismatch ───────────
# Recomputed yes(14) > no(13) says "pass", but the source's own text says "Návrh nebyl přijat"
# (not accepted) -- a real exception (~1% of era1's 790 events), not a parsing bug. This test
# pins OUR OWN (majority-rule) computed result, matching every prior city's G2 precedent of
# computing result independently rather than trusting a not-always-present source label.
def test_era1_quorum_mismatch_event(standardized):
    ve = standardized["vote_events"]["plzen:vote-event:98557"]

    assert ve["start_date"] == "2022-10-18T17:46:01"
    assert ve["identifier"] == "3683/7"
    assert _counts_dict(ve) == {"yes": 14, "no": 13, "abstain": 17, "absent": 3, "not voting": 0}
    assert ve["result"] == "pass"  # our majority-rule computation; source itself says "nebyl přijat"


# ── Event 3: 2026-02-05, point 113728 -- era2, UTF-16LE HTML table format ──────────────────────
def test_era2_event(standardized):
    ve = standardized["vote_events"]["plzen:vote-event:113728"]

    assert ve["start_date"] == "2026-02-05T09:42:59"
    assert ve["identifier"] == "4201/2"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 35, "no": 0, "abstain": 0, "absent": 9, "not voting": 3}
    assert ve["extras"]["era"] == "era2"


# ── Event 4: 2026-05-14, point 113730 -- era3, current/ongoing PDF format ──────────────────────
# Also confirms "Jiří Lodr" resolves to ONE merged identity (plzen:person:jiri-lodr) despite a
# real source typo ("Ing. JIří <b>Lodr</b>") in one era2 meeting's own HTML -- slugify() lowercases
# before comparison, so this was never actually at risk of a G5 split, but pinning his vote here
# still exercises the era3 positional-roster-matching path end-to-end for a real person.
def test_era3_event_and_merged_identity(standardized):
    votes = standardized["votes"]
    ve = standardized["vote_events"]["plzen:vote-event:113730"]

    assert ve["start_date"] == "2026-05-14T10:24:00"
    assert ve["identifier"] == "4203/2"
    assert ve["result"] == "pass"
    assert _counts_dict(ve) == {"yes": 40, "no": 0, "abstain": 0, "absent": 6, "not voting": 1}
    assert ve["extras"]["era"] == "era3"

    assert _vote_option(votes, "plzen:vote-event:113730", "plzen:person:jiri-lodr") == "yes"
