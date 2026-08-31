"""Run the govity analysis for Zastupitelstvo města Pardubice.

Invokes the shared, unmodified `govity.py` from the separate `legislature-data-analyses`
repository. City-specific content — most importantly `government_groups` — is entirely in
`pardubice/analyses/govity/govity_definition.json`. PENDING PROJECT OWNER SIGN-OFF (D7) as of this
writing — see that file's own `approval_status` field before wiring this into anything
user-facing (nightly automation, the dashboard).

Usage:
  python pardubice/scripts/analyses/run_govity.py --script /path/to/legislature-data-analyses/govity/govity.py
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

_CITY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_CITY_ROOT / "scripts"))
import build_all_members  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402

_SLUG = "govity"
_DEFINITION = _CITY_ROOT / "analyses" / _SLUG / f"{_SLUG}_definition.json"
_VOTES = _CITY_ROOT / "data" / "votes.csv"
_VOTE_EVENTS = _CITY_ROOT / "data" / "vote_events.json"
_PERSONS = _CITY_ROOT / "work" / "analysis_inputs" / "all_members.json"
_OUTPUT = _CITY_ROOT / "analyses" / _SLUG / "outputs" / f"{_SLUG}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", required=True, help="Path to govity.py from legislature-data-analyses.")
    parser.add_argument("--definition", default=str(_DEFINITION))
    parser.add_argument("--votes", default=str(_VOTES))
    parser.add_argument("--vote-events", default=str(_VOTE_EVENTS), dest="vote_events")
    parser.add_argument("--persons", default=str(_PERSONS))
    parser.add_argument("--output", default=str(_OUTPUT))
    parser.add_argument("--skip-build-persons", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    script = Path(args.script).resolve()
    if not script.exists():
        raise FileNotFoundError(f"Analysis script not found: {script}")

    definition = Path(args.definition)
    votes = Path(args.votes)
    vote_events = Path(args.vote_events)
    persons = Path(args.persons)
    output = Path(args.output)

    if not args.skip_build_persons:
        records = build_all_members.build_all_members(_CITY_ROOT / "data")
        persons.parent.mkdir(parents=True, exist_ok=True)
        persons.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        logging.info("Rebuilt %s (%d persons)", persons, len(records))

    _common.ensure_all_schemas(script, [_common.definition_schema(_SLUG)])

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        "--definition",
        str(definition),
        "--votes",
        str(votes),
        "--vote_events",
        str(vote_events),
        "--persons",
        str(persons),
        "--output",
        str(output),
    ]
    logging.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    logging.info("Wrote %s", output)


if __name__ == "__main__":
    main()
