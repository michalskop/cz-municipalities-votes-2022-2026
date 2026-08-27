"""Download Ostrava's roll-call vote HTML pages to ostrava/work/raw/ (C9).

No JSON/CSV API exists — see ostrava/config/sources.yml. Three-level crawl:
  1. The term landing page lists every meeting as a link to
     `.../vysledky_hlasovani/vo2226/z<code>/` (confirmed 2026-08-27: 31 meetings, z202201
     through z202604, two gaps — z202202 and z202504 — presumably cancelled/renumbered
     sessions, not a scrape bug).
  2. Each meeting's `index.html` lists every vote as a link to `<NNNN>.html` (4-digit,
     zero-padded), plus the meeting's date and title.
  3. Each vote page (`<NNNN>.html`) has the motion text, the aggregate tally (Přítomno/Pro/
     Proti/Zdržel se/Nehlasovalo), and a per-person breakdown.

Caching is the whole point of a separate downloader step: a full-term backfill is ~2,000-3,000
individual page requests (confirmed 2026-08-27 via a real vote-count sum across all 31 meetings)
— crawled with a politeness delay between requests, and every page cached to disk so a normal
nightly re-run only fetches whatever's genuinely new (new meetings, or new votes appended to the
term's current/latest meeting) instead of re-walking the whole term every night.

Layout under ostrava/work/raw/ (gitignored):
  meetings.json                 — manifest: meeting code -> {date, title, vote_count}
  <meeting_code>/index.html     — cached meeting index page
  <meeting_code>/<NNNN>.html    — cached vote pages

Usage:
  python ostrava/scripts/downloader.py
  python ostrava/scripts/downloader.py --delay 0.3   # politeness delay between requests (seconds)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT_DIR = _CITY_ROOT / "work" / "raw"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cz-cities-research-bot/1.0)"}

_MEETING_CODE_RE = re.compile(r"vysledky_hlasovani/vo2226/z(\d{6})/?\"")
_MEETING_DATE_RE = re.compile(r"Konaného dne\s*([\d.]+)")
_MEETING_TITLE_RE = re.compile(r"zasedání č\. \d+ - (.+?)</td>")
_VOTE_HREF_RE = re.compile(r'href="(\d{4})\.html\s*"')


def _fetch(url: str, timeout: int = 30, retries: int = 4) -> str:
    """A full-term backfill is thousands of requests to a single old municipal server — transient
    timeouts/connection resets are expected, not exceptional, and must not abort the whole run.
    Retries with linear backoff (5s, 10s, 15s, ...); re-raises only after exhausting retries."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 5 * attempt
                logging.warning("Fetch %s failed (attempt %d/%d): %s — retrying in %ds", url, attempt, retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts") from last_exc


def discover_meeting_codes(landing_url: str) -> list[str]:
    html = _fetch(landing_url)
    codes = sorted(set(_MEETING_CODE_RE.findall(html)))
    logging.info("Discovered %d meeting(s) on the term landing page", len(codes))
    return codes


def fetch_meeting_index(
    meeting_code: str, index_url_pattern: str, out_dir: Path, delay: float
) -> dict[str, Any]:
    meeting_dir = out_dir / meeting_code
    meeting_dir.mkdir(parents=True, exist_ok=True)
    index_path = meeting_dir / "index.html"

    url = index_url_pattern.replace("<YYYYMM>", meeting_code)
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        logging.debug("Meeting %s: using cached index.html", meeting_code)
    else:
        html = _fetch(url)
        index_path.write_text(html, encoding="utf-8")
        time.sleep(delay)
        logging.info("Meeting %s: fetched index.html", meeting_code)

    date_match = _MEETING_DATE_RE.search(html)
    title_match = _MEETING_TITLE_RE.search(html)
    vote_numbers = sorted(set(_VOTE_HREF_RE.findall(html)))

    return {
        "meeting_code": meeting_code,
        "date": date_match.group(1) if date_match else None,
        "title": title_match.group(1).strip() if title_match else None,
        "vote_numbers": vote_numbers,
    }


def fetch_vote_pages(
    meeting_code: str, vote_numbers: list[str], vote_url_pattern: str, out_dir: Path, delay: float
) -> tuple[int, list[str]]:
    """Returns (fetched_count, failed_vote_numbers). A page that still fails after _fetch's own
    retries is logged and skipped — not raised — so one bad page can't abort a
    multi-thousand-request backfill; failures are surfaced in the manifest for follow-up instead
    of silently dropped."""
    meeting_dir = out_dir / meeting_code
    fetched = 0
    failed: list[str] = []
    for n in vote_numbers:
        vote_path = meeting_dir / f"{n}.html"
        if vote_path.exists():
            continue
        url = vote_url_pattern.replace("<YYYYMM>", meeting_code).replace("<NNNN>", n)
        try:
            html = _fetch(url)
        except RuntimeError as exc:
            logging.error("Meeting %s vote %s: giving up after retries — %s", meeting_code, n, exc)
            failed.append(n)
            continue
        vote_path.write_text(html, encoding="utf-8")
        fetched += 1
        time.sleep(delay)
    if fetched:
        logging.info("Meeting %s: fetched %d new vote page(s) (of %d total)", meeting_code, fetched, len(vote_numbers))
    else:
        logging.debug("Meeting %s: all %d vote page(s) already cached", meeting_code, len(vote_numbers))
    return fetched, failed


def download(sources_path: Path = _DEFAULT_SOURCES, out_dir: Path = _DEFAULT_OUT_DIR, delay: float = 0.3) -> Path:
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    primary = cfg["vysledky_hlasovani"]["primary"]
    landing_url = primary["term_landing_page"]
    index_url_pattern = primary["meeting_index_pattern"]
    vote_url_pattern = primary["vote_page_pattern"]

    out_dir.mkdir(parents=True, exist_ok=True)
    meeting_codes = discover_meeting_codes(landing_url)

    manifest: dict[str, Any] = {}
    total_fetched = 0
    for code in meeting_codes:
        try:
            meeting = fetch_meeting_index(code, index_url_pattern, out_dir, delay)
        except RuntimeError as exc:
            logging.error("Meeting %s: could not fetch index.html, skipping this meeting — %s", code, exc)
            manifest[code] = {"date": None, "title": None, "vote_count": 0, "index_fetch_failed": True}
            continue
        fetched, failed = fetch_vote_pages(code, meeting["vote_numbers"], vote_url_pattern, out_dir, delay)
        manifest[code] = {
            "date": meeting["date"],
            "title": meeting["title"],
            "vote_count": len(meeting["vote_numbers"]),
        }
        if failed:
            manifest[code]["failed_vote_numbers"] = failed
        total_fetched += fetched

    manifest_path = out_dir / "meetings.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total_votes = sum(m["vote_count"] for m in manifest.values())
    logging.info(
        "Done: %d meeting(s), %d vote(s) total, %d new page(s) fetched this run. Wrote %s",
        len(manifest),
        total_votes,
        total_fetched,
        manifest_path,
    )
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR))
    parser.add_argument("--delay", type=float, default=0.3, help="seconds to sleep between requests")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.sources), Path(args.out_dir), args.delay)


if __name__ == "__main__":
    main()
