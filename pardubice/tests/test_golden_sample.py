"""G3 golden-sample regression test for Pardubice (C3).

Five real vote events, hand-verified against the raw source PDFs/TXT and pinned so a future change
to `pardubice/scripts/standardize.py` that silently alters their output fails loudly. Chosen to
cover the real format variance across the term (see standardize.py's module docstring and this
repo's memory: pardubice-source-research):

  - 2/1   — the term's first recorded vote (2022-11-21), "classic" format, a clean near-unanimous
            pass with one "Nehlasoval".
  - 2/11  — same meeting, but this PDF was printed from a .txt so every page carries "Stránka N"
            + "Hlasování bez os. údajů.txt" running furniture interleaved in the roster; a real
            contested NESCHVÁLENO (6 pro / 27 proti / 6 zdržel se). Exercises _FURNITURE_RE.
  - 4/10  — "classic", the only golden event with all five option types non-zero
            (yes/no/abstain/absent/not-voting) — full-vocabulary coverage, a NESCHVÁLENO.
  - 20/1  — meeting 20's voting file is a UTF-16LE .txt (not a PDF); exercises _extract_text's
            BOM path and CRLF normalization. Also the meeting where seat 13 is "Luticová Mária"
            (the same person earlier recorded as "Ministrová Mária" — the name-change case C4
            merges) and seat 4 is "Janda Leoš" (a seat reused from "Dvořáčková Helena").
  - 39/59 — the newest meeting (2026-06-29), "verbose_2026" format: `str. X z N` page breaks
            interleaved mid-roster, a leading `Prezence` block, and a trailing
            `HLASOVÁNÍ č. N - SCHVÁLENO` result line after the totals.

Re-running standardize.py against the same cached raw snapshot (pardubice/work/raw/, fetched
2026-08-31) must reproduce these five events' counts, results and identifiers exactly. Requires the
raw files to already be downloaded (`python pardubice/scripts/downloader.py`); this test does not
hit the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

_CITY_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("pardubice_standardize", _CITY_ROOT / "scripts" / "standardize.py")
standardize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standardize)

_RAW_DIR = _CITY_ROOT / "work" / "raw"


@pytest.fixture(scope="module")
def standardized(tmp_path_factory):
    if not (_RAW_DIR / "manifest.json").exists():
        pytest.skip(
            f"{_RAW_DIR / 'manifest.json'} not present — run `python pardubice/scripts/downloader.py` "
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


# ── 2/1 — first recorded vote of the term, "classic" format ───────────────────────────────────
def test_first_vote_of_term(standardized):
    ve = standardized["vote_events"]["pardubice:vote-event:2-1"]
    assert ve["start_date"] == "2022-11-21"
    assert ve["identifier"] == "2/1"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 38, "no": 0, "abstain": 0, "absent": 0, "not voting": 1}


# ── 2/11 — running-furniture PDF, a real contested NESCHVÁLENO ─────────────────────────────────
def test_furniture_pdf_rejected_vote(standardized):
    ve = standardized["vote_events"]["pardubice:vote-event:2-11"]
    assert ve["identifier"] == "2/11"
    assert ve["result"] == "fail"
    assert _counts(ve) == {"yes": 6, "no": 27, "abstain": 6, "absent": 0, "not voting": 0}
    assert standardized["motions"]["pardubice:motion:2-11"]["text"].startswith("06. XI. změna rozpočtu")


# ── 4/10 — all five option types present ──────────────────────────────────────────────────────
def test_full_vocabulary_event(standardized):
    ve = standardized["vote_events"]["pardubice:vote-event:4-10"]
    assert ve["start_date"] == "2023-01-30"
    assert ve["result"] == "fail"
    assert _counts(ve) == {"yes": 3, "no": 14, "abstain": 15, "absent": 1, "not voting": 6}


# ── 20/1 — UTF-16LE .txt source; seat-reuse + name-change identities ──────────────────────────
def test_utf16_txt_meeting(standardized):
    ve = standardized["vote_events"]["pardubice:vote-event:20-1"]
    assert ve["start_date"] == "2024-09-23"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 31, "no": 0, "abstain": 0, "absent": 7, "not voting": 1}

    voters = set(standardized["votes"].loc[
        standardized["votes"]["vote_event_id"] == "pardubice:vote-event:20-1", "voter_id"
    ])
    # seat 13 in meeting 20 is "Luticová Mária" (not "Ministrová Mária", not "Charvát Martin")
    assert "pardubice:person:maria-luticova" in voters
    # seat 4 is "Janda Leoš" (a seat earlier held by "Dvořáčková Helena")
    assert "pardubice:person:leos-janda" in voters


# ── 39/59 — newest event, "verbose_2026" format ──────────────────────────────────────────────
def test_newest_event_verbose_format(standardized):
    ve = standardized["vote_events"]["pardubice:vote-event:39-59"]
    assert ve["start_date"] == "2026-06-29"
    assert ve["identifier"] == "39/59"
    assert ve["result"] == "pass"
    assert _counts(ve) == {"yes": 25, "no": 1, "abstain": 1, "absent": 11, "not voting": 1}
