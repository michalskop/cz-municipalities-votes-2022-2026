"""Download Plzeň's ZMP (city council) roll-call vote protocols from usneseni.plzen.eu.

Source shape fully documented in plzen/config/sources.yml (from the 2026-08-28 research pass —
see this repo's memory: plzen-source-research). Summary: no JSON API; a 3-stage scrape
(meeting list -> per-meeting agenda points -> per-point vote protocol), all unauthenticated but
session-cookie-gated (a `requests.Session()` obtained by visiting pages in order — NOT a login),
and the actual vote-protocol files are UTF-16LE-encoded HTML fragments, saved here verbatim (raw
bytes, not re-encoded) so standardize.py owns the one place that decodes them.

Incremental by design (same reasoning as ostrava/scripts/downloader.py — this is a ~2000+ request
scrape across the whole term): a point's protocol file, once downloaded, is never re-fetched on a
later run (protocols don't change after a meeting is finalized). Only the meeting list and each
meeting's point list are always re-fetched (cheap, and the only way to discover newly-held
meetings/points).

Known failure mode to guard against (see sources.yml's protocol_download note): a protocol
download without the right session cookie silently returns HTTP 200 with the WRAPPER PAGE HTML
instead of the real file. _fetch_protocol checks the Content-Disposition header, not just status.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as urllib3_connection

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT_DIR = _CITY_ROOT / "work" / "raw"
_BASE = "https://usneseni.plzen.eu"

# First ZMP meeting of the 2022-2026 term (sources.yml's meeting_enumeration.term_start_date) —
# confirmed via the full 2012-2026 chronological ZMP archive, cross-checked against the
# independently-observed "29. ZMP = 2026-02-05" fact.
_TERM_START_DATE = datetime(2022, 10, 18)

_ZMP_CARD_RE = re.compile(r"<h2>\s*ZMP\s*</h2>", re.S)
_MEETING_ENTRY_RE = re.compile(
    r'href="/ground/ground/detail/(\d+)">\s*<u>\s*([\d.]+)\s*</u>'
)
_POINT_LINK_RE = re.compile(r"/ground/ground/point/(\d+)")
_PROTOCOL_LINK_RE = re.compile(
    r'/ground/ground/point/(\d+)\?groundPoint-fileId=(\d+)&amp;do=groundPoint-downloadProtocol'
)


@contextlib.contextmanager
def _force_ipv4():
    """usneseni.plzen.eu has both A and AAAA records (confirmed via `dig`, 2026-08-28) — same
    IPv6-routing risk on GitHub Actions runners already documented for Brno/Most's downloaders.
    Scoped context manager, restores the original resolver afterward."""
    original = urllib3_connection.allowed_gai_family
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        urllib3_connection.allowed_gai_family = original


def _request(
    session: requests.Session, method: str, url: str, retries: int = 4, timeout: int = 30, **kwargs
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with _force_ipv4():
                resp = session.request(method, url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 5 * attempt
                logging.warning(
                    "%s %s failed (attempt %d/%d): %s -- retrying in %ds",
                    method, url, attempt, retries, exc, wait,
                )
                time.sleep(wait)
    raise RuntimeError(f"Failed to {method} {url} after {retries} attempts") from last_exc


def discover_meetings(session: requests.Session) -> list[dict[str, Any]]:
    """Returns [{"meeting_id": int, "date": "YYYY-MM-DD"}, ...] for every ZMP meeting of the
    2022-2026 term (including any already-scheduled future ones with no votes yet), oldest first.
    See sources.yml's meeting_enumeration for the two-step filter-then-fetch mechanism this
    replicates."""
    _request(
        session,
        "POST",
        f"{_BASE}/ground/ground/dashboard",
        headers={"X-Requested-With": "XMLHttpRequest"},
        data={
            "organisation_list[]": "1",  # "Zastupitelstvo MP" per sources.yml
            "showall_input": "1",
            "sort": "DESC",
            "showall": "Archiv",
            "_do": "groundDashboardFilter-form-submit",
        },
    )
    resp = _request(session, "GET", f"{_BASE}/ground/ground/dashboard?showall=1&order=DESC")
    html = resp.text

    zmp_start = _ZMP_CARD_RE.search(html)
    if not zmp_start:
        raise RuntimeError("ZMP card-header not found in dashboard response -- page shape changed?")
    next_card = html.find("card-header", zmp_start.end())
    block = html[zmp_start.end():next_card if next_card != -1 else len(html)]

    meetings = []
    for meeting_id, date_str in _MEETING_ENTRY_RE.findall(block):
        date = datetime.strptime(date_str.strip(), "%d.%m.%Y")
        if date >= _TERM_START_DATE:
            meetings.append({"meeting_id": int(meeting_id), "date": date.strftime("%Y-%m-%d")})

    meetings.sort(key=lambda m: m["date"])
    logging.info("Discovered %d ZMP meetings in the 2022-2026 term", len(meetings))
    return meetings


def discover_points(session: requests.Session, meeting_id: int) -> list[int]:
    """Agenda-item ('point') ids for one meeting, in page order."""
    resp = _request(session, "GET", f"{_BASE}/ground/ground/detail/{meeting_id}")
    point_ids = sorted({int(pid) for pid in _POINT_LINK_RE.findall(resp.text)})
    return point_ids


def fetch_point_and_protocol(
    session: requests.Session, point_id: int, out_dir: Path
) -> dict[str, Any]:
    """Fetches one agenda point's page (cached separately -- see module docstring's Era 3 note:
    the point page's own <h1>/<h2>/proposer text is the reliable source for the agenda title,
    used INSTEAD of parsing it out of a possibly-corrupted PDF protocol); if the point has a
    'Protokol o hlasování' link, also downloads and caches the protocol file (raw bytes,
    unmodified) unless already cached. Returns a small status dict for the manifest -- never
    raises on a per-point basis (caller logs+skips)."""
    point_path = out_dir / "points" / f"{point_id}.html"
    protocol_path = out_dir / "protocols" / f"{point_id}.html"
    if point_path.exists() and (protocol_path.exists() or point_path.exists()):
        # A cached point page with no cached protocol means "confirmed no protocol" last run --
        # trust that rather than re-fetching every time (points never gain a protocol later).
        return {"point_id": point_id, "has_protocol": protocol_path.exists(), "cached": True}

    resp = _request(session, "GET", f"{_BASE}/ground/ground/point/{point_id}")
    point_path.parent.mkdir(parents=True, exist_ok=True)
    point_path.write_text(resp.text, encoding="utf-8")

    match = _PROTOCOL_LINK_RE.search(resp.text)
    if not match:
        return {"point_id": point_id, "has_protocol": False, "cached": False}

    protocol_point_id, file_id = match.groups()
    protocol_url = (
        f"{_BASE}/ground/ground/point/{protocol_point_id}"
        f"?groundPoint-fileId={file_id}&do=groundPoint-downloadProtocol"
    )
    protocol_resp = _request(session, "GET", protocol_url)

    # See module docstring: without the right session cookie this silently 200s with the wrapper
    # page instead of the file. A real protocol download always sets Content-Disposition.
    if "content-disposition" not in {k.lower() for k in protocol_resp.headers}:
        raise RuntimeError(
            f"Point {point_id}: protocol download had no Content-Disposition header -- likely "
            "returned the wrapper page instead of the file (session cookie problem?)."
        )

    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path.write_bytes(protocol_resp.content)
    return {"point_id": point_id, "has_protocol": True, "cached": False, "file_id": int(file_id)}


def download(out_dir: Path = _DEFAULT_OUT_DIR, delay: float = 0.2) -> Path:
    session = requests.Session()
    meetings = discover_meetings(session)

    manifest: dict[str, Any] = {
        "meetings": [],
        "index_fetch_failed": [],
        "failed_points": [],
    }

    for meeting in meetings:
        meeting_id = meeting["meeting_id"]
        try:
            point_ids = discover_points(session, meeting_id)
        except Exception as exc:  # noqa: BLE001 -- one bad meeting must not abort the whole run
            logging.error("Meeting %d (%s): failed to list agenda points: %s", meeting_id, meeting["date"], exc)
            manifest["index_fetch_failed"].append({"meeting_id": meeting_id, "date": meeting["date"], "error": str(exc)})
            continue

        points_status = []
        for point_id in point_ids:
            try:
                status = fetch_point_and_protocol(session, point_id, out_dir)
                points_status.append(status)
                if not status["cached"]:
                    time.sleep(delay)
            except Exception as exc:  # noqa: BLE001 -- one bad point must not abort the whole meeting
                logging.error("Point %d (meeting %d): %s", point_id, meeting_id, exc)
                manifest["failed_points"].append({"point_id": point_id, "meeting_id": meeting_id, "error": str(exc)})

        manifest["meetings"].append(
            {
                "meeting_id": meeting_id,
                "date": meeting["date"],
                "point_ids": point_ids,
                "points_with_protocol": [s["point_id"] for s in points_status if s["has_protocol"]],
            }
        )
        logging.info(
            "Meeting %d (%s): %d points, %d with a vote protocol",
            meeting_id, meeting["date"], len(point_ids),
            sum(1 for s in points_status if s["has_protocol"]),
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote manifest to %s", manifest_path)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR))
    parser.add_argument("--delay", type=float, default=0.2, help="politeness delay between protocol downloads (seconds)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.out_dir), delay=args.delay)


if __name__ == "__main__":
    main()
