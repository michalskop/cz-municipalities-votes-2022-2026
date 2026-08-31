"""Run the wpca analysis for Zastupitelstvo města Pardubice.

Invokes the shared, unmodified `wpca.py` from the separate `legislature-data-analyses` repository.
City-specific content is entirely in `pardubice/analyses/wpca/wpca_definition.json`. Its
government-axis detection depends on govity's `government_groups`, which is PENDING PROJECT OWNER
SIGN-OFF (D7) as of this writing — see that file's own `approval_status` field.

Note: unlike the other three analyses, wpca.py's own CLI flag is `--vote-events` (hyphen), not
`--vote_events` (underscore) — same as Praha's runner, verified against the actual argparse
definitions in legislature-data-analyses/{attendance,govity,rebelity,wpca}/*.py.

Usage:
  python pardubice/scripts/analyses/run_wpca.py --script /path/to/legislature-data-analyses/wpca/wpca.py
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

_SLUG = "wpca"
_DEFINITION = _CITY_ROOT / "analyses" / _SLUG / f"{_SLUG}_definition.json"
_VOTES = _CITY_ROOT / "data" / "votes.csv"
_VOTE_EVENTS = _CITY_ROOT / "data" / "vote_events.json"
_PERSONS = _CITY_ROOT / "work" / "analysis_inputs" / "all_members.json"
_OUTPUT = _CITY_ROOT / "analyses" / _SLUG / "outputs" / f"{_SLUG}.json"
_OUTPUT_TIME = _CITY_ROOT / "analyses" / _SLUG / "outputs" / f"{_SLUG}_time.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--script", required=True, help="Path to wpca.py from legislature-data-analyses.")
    parser.add_argument("--definition", default=str(_DEFINITION))
    parser.add_argument("--votes", default=str(_VOTES))
    parser.add_argument("--vote-events", default=str(_VOTE_EVENTS), dest="vote_events")
    parser.add_argument("--persons", default=str(_PERSONS))
    parser.add_argument("--output", default=str(_OUTPUT))
    parser.add_argument("--output-time", default=str(_OUTPUT_TIME), dest="output_time")
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
    output_time = Path(args.output_time) if args.output_time else None

    if not args.skip_build_persons:
        records = build_all_members.build_all_members(_CITY_ROOT / "data")
        persons.parent.mkdir(parents=True, exist_ok=True)
        persons.write_text(json.dumps(records, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        logging.info("Rebuilt %s (%d persons)", persons, len(records))

    extra_schemas = [_common.definition_schema(_SLUG), _common.output_schema(_SLUG)]
    extra_schemas.append(
        (
            "dt.analyses/wpca-time/latest/schemas/wpca-time.dt.analyses.json",
            "https://michalskop.github.io/legislature-data-standard/dt.analyses/wpca-time/latest/schemas/wpca-time.dt.analyses.json",
        )
    )
    _common.ensure_all_schemas(script, extra_schemas)

    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        "--definition",
        str(definition),
        "--votes",
        str(votes),
        "--vote-events",
        str(vote_events),
        "--persons",
        str(persons),
        "--output",
        str(output),
    ]
    if output_time is not None:
        cmd += ["--output-time", str(output_time)]

    logging.info("Running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    logging.info("Wrote %s", output)
    if output_time is not None:
        logging.info("Wrote %s", output_time)


if __name__ == "__main__":
    main()
