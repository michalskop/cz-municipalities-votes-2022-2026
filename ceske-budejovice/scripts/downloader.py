"""Download České Budějovice's roll-call vote HTML pages to ceske-budejovice/work/raw/ (C9).

No JSON/CSV API — see ceske-budejovice/config/sources.yml. The city's VOATT "Jak se hlasovalo"
portal is a three-level plain-GET crawl:
  1. /statistiky/obdobi/1484  — term-9 index: a table of all 31 meetings, each
     `Zasedání číslo <YYYYNNN> | <D. month YYYY> | link=/statistiky/zasedani/<meetingId>`.
  2. /statistiky/zasedani/<meetingId>  — a table of that meeting's vote events, each
     `Bod č. NN. | <cislo>. | <title> | link=/statistiky/vysledky-hlasovani-dle-bodu/<meetingId>?bod=<voteId>`.
  3. /statistiky/vysledky-hlasovani-dle-bodu/<meetingId>?bod=<voteId>  — the per-vote page with
     the `Zastupitel | Klub | Hlasoval | Hlasování klubu` table (45 rows).

Caching is the whole point of a separate downloader: a full-term backfill is ~1,000 page requests
to one site. Every page is cached to disk (ceske-budejovice/work/raw/, gitignored) so a normal
nightly re-run only fetches what's genuinely new. A politeness delay sits between requests, and a
page that still fails after retries is logged + skipped (surfaced in the manifest), never raised —
one bad page can't abort the crawl.

`_force_ipv4` + retry/backoff included proactively: www.c-budejovice.cz publishes a AAAA record
(`dig www.c-budejovice.cz AAAA` is non-empty), so GitHub Actions' IPv6 routing issue could bite.

Layout under ceske-budejovice/work/raw/:
  manifest.json                      — meetings + their vote trees + any fetch failures
  index.html                         — cached term-9 index page
  meeting_<meetingId>.html           — cached meeting page
  votes/<meetingId>_<voteId>.html    — cached vote pages

Usage:
  python ceske-budejovice/scripts/downloader.py
  python ceske-budejovice/scripts/downloader.py --delay 0.5
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import socket
import time
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as urllib3_connection
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT_DIR = _CITY_ROOT / "work" / "raw"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cz-cities-research-bot/1.0)"}

_MONTHS_CS = {
    "ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
    "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12,
}

# term-9 index: one <tr> per meeting, cells in order
#   <td>Zasedání číslo 2022001</td><td>24. října 2022</td><td><a href="/statistiky/zasedani/287849">…</a></td>
_INDEX_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_ROW_NUMBER_RE = re.compile(r"Zasedání číslo\s*(\d{7})")
_ROW_DATE_RE = re.compile(r"<td[^>]*>\s*(\d{1,2})\.\s*([A-Za-zÁ-Žá-žěščřžýáíéúůňťďóĚŠČŘŽÝÁÍÉÚŮŇŤĎÓ]+)\s*(\d{4})\s*</td>")
_ROW_MEETING_ID_RE = re.compile(r"/statistiky/zasedani/(\d+)")
_VOTE_HREF_RE = re.compile(r'/statistiky/vysledky-hlasovani-dle-bodu/(\d+)\?bod=(\d+)')


@contextlib.contextmanager
def _force_ipv4():
    original = urllib3_connection.allowed_gai_family
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        urllib3_connection.allowed_gai_family = original


def _fetch(url: str, timeout: int = 30, retries: int = 4) -> str:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with _force_ipv4():
                resp = requests.get(url, headers=_HEADERS, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 5 * attempt
                logging.warning("Fetch %s failed (attempt %d/%d): %s — retrying in %ds", url, attempt, retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts") from last_exc


def _period_url(sources_path: Path) -> str:
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    return cfg["portal"]["term_index"]["url"]


def discover_meetings(index_html: str) -> list[dict[str, Any]]:
    """[{"number": "2022001", "meeting_id": "287849", "date": "2022-10-24"}, ...] chronological.
    Parsed per <tr> — the index table's cells are (number, date, link) in that order."""
    meetings = []
    for row in _INDEX_ROW_RE.findall(index_html):
        n = _ROW_NUMBER_RE.search(row)
        mid = _ROW_MEETING_ID_RE.search(row)
        if not (n and mid):
            continue
        d = _ROW_DATE_RE.search(row)
        date = None
        if d:
            month = _MONTHS_CS.get(d.group(2).lower())
            if month:
                date = f"{d.group(3)}-{month:02d}-{int(d.group(1)):02d}"
            else:
                logging.warning("Meeting %s: unrecognised month %r — date left null", n.group(1), d.group(2))
        else:
            logging.warning("Meeting %s: no date cell matched — date left null", n.group(1))
        meetings.append({"number": n.group(1), "meeting_id": mid.group(1), "date": date})
    meetings.sort(key=lambda m: m["number"])
    logging.info("Discovered %d meetings (%s .. %s)", len(meetings),
                 meetings[0]["date"] if meetings else None, meetings[-1]["date"] if meetings else None)
    return meetings


def _parse_vote_links(meeting_html: str) -> list[str]:
    """Ordered, de-duplicated list of voteIds referenced on a meeting page."""
    seen: list[str] = []
    for _mid, vid in _VOTE_HREF_RE.findall(meeting_html):
        if vid not in seen:
            seen.append(vid)
    return seen


def download(sources_path: Path = _DEFAULT_SOURCES, out_dir: Path = _DEFAULT_OUT_DIR, delay: float = 0.3) -> Path:
    period_url = _period_url(sources_path)
    base = period_url.split("/statistiky/")[0]
    out_dir.mkdir(parents=True, exist_ok=True)
    votes_dir = out_dir / "votes"
    votes_dir.mkdir(parents=True, exist_ok=True)

    # 1) term index — always re-fetched (cheap; how new meetings/votes are discovered)
    index_html = _fetch(period_url)
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")
    meetings = discover_meetings(index_html)

    manifest: dict[str, Any] = {"period_url": period_url, "meetings": []}
    for m in meetings:
        mid = m["meeting_id"]
        meeting_path = out_dir / f"meeting_{mid}.html"
        # A meeting is immutable once it's over, but the newest one may still be getting votes
        # appended — always re-fetch the last two meetings, cache the rest.
        is_recent = m["number"] >= meetings[-2]["number"] if len(meetings) >= 2 else True
        if meeting_path.exists() and not is_recent:
            meeting_html = meeting_path.read_text(encoding="utf-8")
        else:
            time.sleep(delay)
            try:
                meeting_html = _fetch(f"{base}/statistiky/zasedani/{mid}")
                meeting_path.write_text(meeting_html, encoding="utf-8")
            except RuntimeError as exc:
                logging.error("Meeting %s (%s): page fetch failed — skipping meeting: %s", m["number"], mid, exc)
                manifest["meetings"].append({**m, "index_fetch_failed": True, "votes": []})
                continue

        vote_ids = _parse_vote_links(meeting_html)
        got, failed = [], []
        for vid in vote_ids:
            vpath = votes_dir / f"{mid}_{vid}.html"
            if vpath.exists() and not is_recent:
                got.append(vid)
                continue
            time.sleep(delay)
            try:
                vhtml = _fetch(f"{base}/statistiky/vysledky-hlasovani-dle-bodu/{mid}?bod={vid}")
                vpath.write_text(vhtml, encoding="utf-8")
                got.append(vid)
            except RuntimeError as exc:
                logging.error("Meeting %s vote %s: giving up after retries — %s", m["number"], vid, exc)
                failed.append(vid)

        manifest["meetings"].append({**m, "votes": got, "failed_votes": failed})
        logging.info("Meeting %s (%s): %d vote page(s)%s", m["number"], m["date"], len(got),
                     f", {len(failed)} FAILED" if failed else "")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(x["votes"]) for x in manifest["meetings"])
    logging.info("Wrote manifest to %s (%d meetings, %d vote pages)", manifest_path, len(manifest["meetings"]), total)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR))
    parser.add_argument("--delay", type=float, default=0.3, help="politeness delay between requests (seconds)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.sources), Path(args.out_dir), args.delay)


if __name__ == "__main__":
    main()
