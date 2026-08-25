"""Non-blocking freshness check for roster_overlay.py's static table.

roster_overlay.py has no live source of its own — it can only re-apply gaps that were already
diagnosed against a past praha.eu fetch (VERIFIED_AS_OF). It cannot detect a *new* mid-term
personnel change (another substitute seated, another klub exclusion) that happened after that
date. This script is the tripwire for that blind spot: it doesn't re-check praha.eu itself (that
needs Playwright/a browser, not wired into the nightly runner — see fetch_praha_roster.py), it
just flags when the static table is old enough that a manual re-check is due.

Deliberately non-blocking (always exits 0): failing the nightly job over a stale-but-not-yet-wrong
overlay would stop Praha's data from updating at all until someone intervenes, which is worse than
the (possible, unconfirmed) gap this is warning about. Emits a GitHub Actions `::warning::`
annotation instead, which surfaces on the Actions run summary without red-X'ing the job.

Usage:
    python praha/scripts/check_roster_overlay_staleness.py
    python praha/scripts/check_roster_overlay_staleness.py --max-age-days 30
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roster_overlay  # noqa: E402

_DEFAULT_MAX_AGE_DAYS = 30


def check(max_age_days: int, today: date | None = None) -> int:
    """Returns the overlay's age in days; logs a GitHub warning annotation if stale."""
    verified = datetime.strptime(roster_overlay.VERIFIED_AS_OF, "%Y-%m-%d").date()
    today = today or date.today()
    age_days = (today - verified).days

    if age_days > max_age_days:
        message = (
            f"praha/scripts/roster_overlay.py hasn't been re-verified against praha.eu in "
            f"{age_days} days (last verified {roster_overlay.VERIFIED_AS_OF}, threshold "
            f"{max_age_days}). A new mid-term substitution or klub change since then would be "
            f"invisible to this pipeline until someone re-runs fetch_praha_roster.py, diagnoses "
            f"any new gaps, and updates roster_overlay.py + VERIFIED_AS_OF."
        )
        print(f"::warning::{message}")
        logging.warning(message)
    else:
        logging.info(
            "Roster overlay is %d day(s) old (threshold %d) — no action needed.",
            age_days,
            max_age_days,
        )
    return age_days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-age-days", type=int, default=_DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    check(args.max_age_days)


if __name__ == "__main__":
    main()
