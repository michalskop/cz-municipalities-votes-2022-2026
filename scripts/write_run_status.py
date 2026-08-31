"""Write <city>/analyses/_run_status.json — a per-city pipeline heartbeat the dashboard footer
reads to show "data as of <X>" and "last checked <Y>".

Run at the end of a city's nightly job, AFTER the pipeline + analyses have produced their outputs
but BEFORE the "commit and push" step (and the file must be included in that step's `git add`, so
it is committed every night — the whole point is a heartbeat even when nothing else changed).

Fields:
  last_successful_run_utc  — set to "now" every time this script runs (i.e. every time the
                             pipeline + analyses completed without erroring). This is the "last
                             checked" timestamp.
  last_data_change_utc     — "now" if this run changed any tracked data/output file, otherwise the
                             value carried over from the previous _run_status.json (or "now" if
                             there is no previous file). This is the "data as of" timestamp.
  latest_vote_event_date   — the newest vote-event date in <city>/data/vote_events.json.
  vote_events_count        — number of vote events.

"Did this run change data?" is decided with `git status --porcelain` over the city's data/ dir and
its analyses/ *_definition.json + outputs/ — the same set the nightly job commits. _run_status.json
itself is excluded from that check (it always changes; that would make last_data_change_utc
useless).

Usage:
  python scripts/write_run_status.py --city ceske-budejovice
  python scripts/write_run_status.py --city praha --data-dir praha/data --analyses-dir praha/analyses
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tracked_paths_changed(data_dir: Path, analyses_dir: Path, status_rel: str) -> bool:
    """True if `git status --porcelain` reports any change under data_dir or analyses_dir other
    than the _run_status.json file itself. Untracked files count as a change too."""
    rel_data = data_dir.resolve().relative_to(_REPO_ROOT).as_posix()
    rel_analyses = analyses_dir.resolve().relative_to(_REPO_ROOT).as_posix()
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", rel_data, rel_analyses],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        # a rename shows as "old -> new"; take the new path
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path == status_rel:
            continue
        return True
    return False


def write_status(city: str, data_dir: Path, analyses_dir: Path) -> Path:
    status_path = analyses_dir / "_run_status.json"
    status_rel = status_path.resolve().relative_to(_REPO_ROOT).as_posix()

    prev: dict = {}
    if status_path.exists():
        try:
            prev = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logging.warning("Existing %s is not valid JSON — ignoring it", status_path)

    now = _now_iso()
    changed = _tracked_paths_changed(data_dir, analyses_dir, status_rel)
    last_data_change = now if changed else prev.get("last_data_change_utc", now)

    latest_date = None
    count = None
    ve_path = data_dir / "vote_events.json"
    if ve_path.exists():
        events = json.loads(ve_path.read_text(encoding="utf-8"))
        count = len(events)
        dates = [e.get("start_date") for e in events if e.get("start_date")]
        latest_date = max(dates) if dates else None

    status = {
        "city": city,
        "generated_at_utc": now,
        "last_successful_run_utc": now,
        "last_data_change_utc": last_data_change,
        "latest_vote_event_date": latest_date,
        "vote_events_count": count,
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Wrote %s (data %s this run; data as of %s; %s events, latest %s)",
                 status_path, "CHANGED" if changed else "unchanged", last_data_change, count, latest_date)
    return status_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", required=True, help="city slug, e.g. ceske-budejovice")
    parser.add_argument("--data-dir", default=None, help="defaults to <city>/data")
    parser.add_argument("--analyses-dir", default=None, help="defaults to <city>/analyses")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data_dir = Path(args.data_dir) if args.data_dir else _REPO_ROOT / args.city / "data"
    analyses_dir = Path(args.analyses_dir) if args.analyses_dir else _REPO_ROOT / args.city / "analyses"
    write_status(args.city, data_dir, analyses_dir)


if __name__ == "__main__":
    main()
