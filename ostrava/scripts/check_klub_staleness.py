"""Non-blocking freshness check for Ostrava's klub (assembly group) membership data.

Mirrors praha/scripts/check_roster_overlay_staleness.py's pattern. party_affiliation.py's klub
history stops at the last meeting that published klub grouping (2025-06-18, meeting 202505) — the
site has published NO klub data on any vote page since. This script doesn't re-check the source
itself (that's party_affiliation.py's job, re-run nightly against whatever's newly cached); it
just flags when the gap since the last confirmed klub snapshot is old enough that a manual check
(has the site resumed publishing klub grouping? has ostrava.cz's live composition page moved
further from the last known state?) is due.

Deliberately non-blocking (always exits 0), same reasoning as Praha's version: failing the
nightly job over a stale-but-not-necessarily-wrong klub snapshot would stop Ostrava's vote data
from updating at all, which is worse than the gap this warns about.

Usage:
    python ostrava/scripts/check_klub_staleness.py
    python ostrava/scripts/check_klub_staleness.py --max-age-days 60
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import party_affiliation  # noqa: E402

_DEFAULT_MAX_AGE_DAYS = 60
_DEFAULT_RAW_DIR = Path(__file__).resolve().parents[1] / "work" / "raw"


def check(max_age_days: int, raw_dir: Path, today: date | None = None) -> int | None:
    history = party_affiliation.build_klub_history(raw_dir)
    if not history:
        print("::warning::No klub data found at all in ostrava/work/raw/ — party_affiliation.py has nothing to work with.")
        return None

    last_confirmed = max(h["date"] for h in history.values())
    last_date = datetime.strptime(last_confirmed, "%Y-%m-%d").date()
    today = today or date.today()
    age_days = (today - last_date).days

    if age_days > max_age_days:
        message = (
            f"Ostrava's klub (assembly group) data hasn't been confirmed in {age_days} days "
            f"(last confirmed {last_confirmed}, threshold {max_age_days}). The site has published "
            f"no per-vote klub grouping since then; check whether it has resumed, and whether "
            f"ostrava.cz's live composition page (slozeni-zastupitelstva-1) shows any change worth "
            f"investigating before assuming continuity."
        )
        print(f"::warning::{message}")
        logging.warning(message)
    else:
        logging.info("Klub data is %d day(s) old (threshold %d) — no action needed.", age_days, max_age_days)
    return age_days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check(args.max_age_days, Path(args.raw_dir))


if __name__ == "__main__":
    main()
