"""Fetch Praha's live assembly-member roster from praha.eu for cross-checking against the
Golemio-derived standard tables.

Why this exists: `praha/data/{persons,organizations,memberships}.csv` are derived entirely from
the Golemio roll-call CSV (standardize.py) plus each councilor's 2022 election candidate-list
(party_affiliation.py). Both sources have a structural blind spot — a personnel change (a member
leaving, a substitute taking a seat, someone losing/changing their klub) is only visible to the
Golemio pipeline once that person has cast at least one recorded vote under a dedicated CSV
column. A brand-new substitute who hasn't voted yet, or a departure with no later roll call before
the export's cutoff, is invisible to it. praha.eu/seznam-zastupitelu is the live, authoritative
membership roster and does not have this lag; this script cross-checks against it. See
praha/config/sources.yml's `group_affiliation_source` note for the earlier (2026-08-04) attempt at
this, which concluded praha.eu was unscrapable — that conclusion was specific to *not having
browser automation available at the time*, not to the site itself; see the addendum note added
there.

praha.eu/seznam-zastupitelu is an Angular SPA (Liferay portal + an Angular "representatives"
portlet) — the HTML returned to a plain HTTP client is just the page shell, no member data. A real
browser has to execute the client-side JS. Playwright is used here, but pointed at the system
Chromium (`/snap/bin/chromium`) rather than Playwright's own browser-download mechanism, because
the latter's download path does not work on this container's OS. `wait_until="networkidle"` was
tried and times out on this specific site (it appears to keep some long-poll/analytics connection
open); `wait_until="domcontentloaded"` plus a fixed settle delay is what actually works.

Two views are fetched:
  - the default view: current members (name, title, email, klub/party)
  - after clicking "ZOBRAZIT BÝVALÉ ČLENY ZHMP" ("show former members"): current + former members,
    with former ones distinguishable by an extra `RepresentativeList__box--cancelled` CSS class
    on their card.

Both raw HTML snapshots are saved under `praha/work/praha_eu/` (gitignored via the repo's
`*/work/` rule) for inspection/debugging, alongside a parsed JSON roster.

This is written as a small, reusable pipeline capability (clear function boundaries, idempotent —
re-running just re-fetches and overwrites today's snapshot) rather than a one-off script, since
praha.eu is the kind of ground truth this project will plausibly want to re-check again. It is
deliberately scoped to Praha only: generalizing this into a multi-city roster-fetch abstraction
(e.g. for Brno/Ostrava, if/when they get client-rendered member-roster sites too) is a future
concern, not solved here.

Usage:
  python praha/scripts/fetch_praha_roster.py
  python praha/scripts/fetch_praha_roster.py --output-dir praha/work/praha_eu --headless=False
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_DIR = _CITY_ROOT / "work" / "praha_eu"

ROSTER_URL = "https://praha.eu/seznam-zastupitelu"
FORMER_MEMBERS_BUTTON_TEXT = "ZOBRAZIT BÝVALÉ ČLENY ZHMP"

# The system-wide Chromium build this container actually has working; Playwright's own
# `playwright install` browser-download path does not work in this environment.
_SYSTEM_CHROMIUM = "/snap/bin/chromium"

_CANCELLED_CSS_CLASS = "RepresentativeList__box--cancelled"


@dataclass
class RosterEntry:
    name: str  # "Given Family", titles stripped
    given_name: str
    family_name: str
    title: str  # e.g. "Mgr.", "Ing. MBA" — academic/professional titles only
    email: str | None
    party: str | None  # klub label as shown on praha.eu (e.g. "ANO 2011", "Nezařazení")
    status: str  # "current" | "former"
    detail_url: str | None


def _slugify(text: str) -> str:
    """Mirror standardize.py's `_slugify` exactly, so person IDs derived here match the
    convention already used for everyone in persons.csv."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _parse_name(raw_name: str) -> tuple[str, str, str]:
    """Split a praha.eu display name into (given_name, family_name, title).

    praha.eu renders names as "[Prefix titles] Family [Family2] Given[, Suffix titles]" — e.g.
    "Mgr. Ševčíková Mária", "MUDr.  Cingrošová  Klára" (note: sometimes double-spaced), "Mgr.
    Hrubčík Martin, MBA", or with no title at all: "Procházka David". Unlike the Golemio CSV
    header format (family/given separated by 2+ spaces, parsed in standardize.py's
    `_parse_person_header`), praha.eu puts the given name LAST and titles can appear as a prefix,
    a comma-suffix, or both.

    Heuristic: comma-separated suffix (if any) is a title; then, of the remaining space-separated
    tokens, leading tokens containing "." are prefix titles; the last remaining token is the given
    name; everything else remaining is the (possibly multi-word, e.g. "Kordová Marvanová") family
    name.
    """
    text = re.sub(r"\s+", " ", raw_name.strip())
    suffix_title = ""
    if "," in text:
        text, suffix_title = (part.strip() for part in text.split(",", 1))

    tokens = text.split(" ")
    prefix_titles: list[str] = []
    while tokens and "." in tokens[0]:
        prefix_titles.append(tokens.pop(0))

    if len(tokens) < 2:
        raise ValueError(f"Cannot parse praha.eu display name into family+given: {raw_name!r}")

    given_name = tokens[-1]
    family_name = " ".join(tokens[:-1])
    title = " ".join(prefix_titles + ([suffix_title] if suffix_title else []))
    return given_name, family_name, title


def _fetch_html(headless: bool = True) -> tuple[str, str]:
    """Return (current_only_html, current_plus_former_html) for ROSTER_URL."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=_SYSTEM_CHROMIUM, headless=headless, args=["--no-sandbox"]
        )
        try:
            page = browser.new_page()
            logging.info("Loading %s", ROSTER_URL)
            # networkidle times out on this site (a long-lived connection never quiesces);
            # domcontentloaded + a fixed settle delay is what reliably lets the Angular portlet
            # finish rendering the member cards.
            page.goto(ROSTER_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            current_html = page.content()

            logging.info("Revealing former members")
            page.get_by_text(FORMER_MEMBERS_BUTTON_TEXT, exact=False).click()
            page.wait_for_timeout(5000)
            full_html = page.content()
        finally:
            browser.close()
    return current_html, full_html


def _parse_roster(full_html: str) -> list[RosterEntry]:
    """Parse the current+former HTML snapshot into RosterEntry rows."""
    soup = BeautifulSoup(full_html, "html.parser")
    boxes = soup.select("div.contact-box")
    if not boxes:
        raise ValueError(
            "No 'div.contact-box' member cards found — praha.eu's markup may have changed, or "
            "the page didn't finish rendering. Refusing to silently produce an empty roster."
        )

    entries: list[RosterEntry] = []
    for box in boxes:
        classes = box.get("class", [])
        status = "former" if _CANCELLED_CSS_CLASS in classes else "current"

        name_link = box.select_one("h5 a")
        if name_link is None:
            raise ValueError(f"Member card with no name link: {box}")
        raw_name = name_link.get_text(strip=True)
        given_name, family_name, title = _parse_name(raw_name)

        detail_href = name_link.get("href")
        detail_url = (
            f"https://praha.eu/web/praha/seznam-zastupitelu{detail_href}"
            if detail_href and detail_href.startswith("#")
            else detail_href
        )

        email_link = box.select_one("a[href^=mailto]")
        email = email_link.get_text(strip=True) if email_link else None

        label_link = box.select_one(".label-container a.label")
        party = label_link.get_text(strip=True) if label_link else None

        entries.append(
            RosterEntry(
                name=f"{given_name} {family_name}",
                given_name=given_name,
                family_name=family_name,
                title=title,
                email=email,
                party=party,
                status=status,
                detail_url=detail_url,
            )
        )
    return entries


def fetch_roster(
    output_dir: Path = _DEFAULT_OUTPUT_DIR, headless: bool = True
) -> list[RosterEntry]:
    """Fetch, parse, and persist today's praha.eu roster snapshot. Returns the parsed entries."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc)
    stamp = fetched_at.strftime("%Y-%m-%d")

    current_html, full_html = _fetch_html(headless=headless)

    (output_dir / f"seznam-zastupitelu_current_{stamp}.html").write_text(
        current_html, encoding="utf-8"
    )
    (output_dir / f"seznam-zastupitelu_current-plus-former_{stamp}.html").write_text(
        full_html, encoding="utf-8"
    )

    entries = _parse_roster(full_html)

    n_current = sum(1 for e in entries if e.status == "current")
    n_former = sum(1 for e in entries if e.status == "former")
    logging.info("Parsed %d roster entries (%d current, %d former)", len(entries), n_current, n_former)

    roster_json_path = output_dir / f"roster_{stamp}.json"
    roster_json_path.write_text(
        json.dumps(
            {
                "source_url": ROSTER_URL,
                "fetched_at": fetched_at.isoformat(),
                "entries": [asdict(e) for e in entries],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logging.info("Wrote %s", roster_json_path)

    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--headless",
        type=lambda v: v.lower() not in ("false", "0", "no"),
        default=True,
        help="Set to False to watch the browser locally while debugging.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fetch_roster(output_dir=args.output_dir, headless=args.headless)


if __name__ == "__main__":
    main()
