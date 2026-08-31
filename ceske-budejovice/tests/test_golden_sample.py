"""G3 golden-sample regression test for České Budějovice (C3).

Five real vote events, hand-verified against the raw cached VOATT portal pages and pinned so a
future change to `ceske-budejovice/scripts/standardize.py` that silently alters their output fails
loudly. Chosen for coverage (see standardize.py's module docstring and this repo's memory:
ceske-budejovice-source-research):

  - 2022001/1  — the term's first recorded vote (constitutive session, 2022-10-24).
  - 2022001/10 — all five option types present AND exactly 23 "Hlasoval pro" — the boundary of the
    DERIVED result rule (pass iff >= 23), so a regression in that threshold flips this event.
  - 2023005/25 — a DERIVED "fail" (15 pro / 16 proti / 5 abstain), all five option types present.
  - 2024012/31 — a "block vote": one Číslo hlasování (31) covering six agenda items (Bod 16.08 –
    16.13), where every ?bod= id returns the same 45×6 combined table. Exercises the de-tile +
    one-event-per-cislo collapse; its motion text is annotated "(blok 6 bodů)".
  - 2026031/7  — the newest event (2026-07-20).

Re-running standardize.py against the same raw snapshot (ceske-budejovice/work/raw/, fetched
2026-08-31) must reproduce these five events' counts, results and identifiers exactly. Requires
the raw pages to already be downloaded (`python ceske-budejovice/scripts/downloader.py`); this
test does not hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ceske_budejovice_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_DIR = _CITY_ROOT / "work" / "raw"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not (_RAW_DIR / "manifest.json").exists():
        pytest.skip(
            f"{_RAW_DIR / 'manifest.json'} not present — run `python ceske-budejovice/scripts/downloader.py` "
            "first. This golden-sample test intentionally does not hit the network itself."
        )
    out_dir = tmp_path_factory.mktemp("golden_sample_out")
    standardize.standardize(raw_dir=_RAW_DIR, out_dir=out_dir)
    votes = pd.read_csv(out_dir / "votes.csv", dtype=str)
    motions = {m["id"]: m for m in json.loads((out_dir / "motions.json").read_text(encoding="utf-8"))}
    vote_events = {ve["id"]: ve for ve in json.loads((out_dir / "vote_events.json").read_text(encoding="utf-8"))}
    return {"votes": votes, "vote_events": vote_events, "motions": motions}


def _counts(ve: dict) -> dict[str, int]:
    return {c["option"]: c["value"] for c in ve["counts"]}


def test_first_vote_of_term(standardized):
    ve = standardized["vote_events"]["ceske-budejovice:vote-event:2022001-1"]
    assert ve["start_date"] == "2022-10-24"
    assert ve["identifier"] == "2022001/1"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 34, "no": 7, "abstain": 2, "absent": 2, "not voting": 0}


def test_derived_pass_at_threshold(standardized):
    # exactly 23 "Hlasoval pro" — the boundary of `pass iff yes >= 23`
    ve = standardized["vote_events"]["ceske-budejovice:vote-event:2022001-10"]
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 23, "no": 16, "abstain": 3, "absent": 2, "not voting": 1}


def test_derived_fail(standardized):
    ve = standardized["vote_events"]["ceske-budejovice:vote-event:2023005-25"]
    assert ve["start_date"] == "2023-04-03"
    assert ve["result"] == "fail"
    assert _counts(ve) == {"yes": 15, "no": 16, "abstain": 5, "absent": 4, "not voting": 5}


def test_block_vote_collapsed(standardized):
    ve = standardized["vote_events"]["ceske-budejovice:vote-event:2024012-31"]
    assert ve["identifier"] == "2024012/31"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 38, "no": 0, "abstain": 0, "absent": 3, "not voting": 4}
    # de-tiled to one row per councillor, not 6x
    n_rows = (standardized["votes"]["vote_event_id"] == "ceske-budejovice:vote-event:2024012-31").sum()
    assert n_rows == 45
    assert standardized["motions"]["ceske-budejovice:motion:2024012-31"]["text"].endswith("(blok 6 bodů)")


def test_newest_event(standardized):
    ve = standardized["vote_events"]["ceske-budejovice:vote-event:2026031-7"]
    assert ve["start_date"] == "2026-07-20"
    assert ve["identifier"] == "2026031/7"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 35, "no": 0, "abstain": 0, "absent": 10, "not voting": 0}
