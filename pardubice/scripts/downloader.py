"""Download Pardubice's ZmP (city council) per-meeting archives and extract the roll-call voting
PDF from each, into pardubice/work/raw/.

Source shape fully documented in pardubice/config/sources.yml (research pass 2026-08-31 — see this
repo's memory: pardubice-source-research). Summary: `pardubice.eu/zmp-<year>` lists one ZIP per
meeting (hashed `/data/files/...` paths, scraped from the page HTML). Each ZIP bundles the
narrative minutes, per-resolution attachments, and a dedicated voting PDF whose filename is
inconsistent — matched here case-insensitively on "hlasován" and confirmed by the presence of the
"HLASOVÁNÍ č." block structure in its extracted text.

Only the CURRENT term is kept: a hard `date >= 2022-10-01` cutoff (the zmp-2022 page also lists
previous-term meetings numbered 38..45 / roman XXXIX-XL, all dated Jan-Sep 2022; meeting numbers
restart each term so the cutoff is by date, never by number). The 2022-10-17 constitutive session
is published as a bare minutes PDF with no voting ZIP — nothing to download there.

Retry-with-backoff and `_force_ipv4` included proactively (pardubice.eu publishes both A and AAAA
records — `dig pardubice.eu AAAA` is non-empty — so GitHub Actions' IPv6 routing issue could bite
even though it hasn't in local testing; same practice as brno/most/plzen/hradec-kralove).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import re
import socket
import time
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import requests
import urllib3.util.connection as urllib3_connection
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT_DIR = _CITY_ROOT / "work" / "raw"

_BASE = "https://pardubice.eu"
_TERM_START = date(2022, 10, 1)  # hard cutoff: everything earlier is the previous term

# The meeting-ZIP slug has several real shapes across the term, all ending "...zmp-dne-D-M-YYYY":
#   zapis-z-11-zasedani-zmp-dne-27-11-2023-2-1.zip   (the common one)
#   zapis-ze-4-zasedani-zmp-dne-30-01-2023-2.zip     ("ze" instead of "z")
#   zapis-19-zasedani-zmp-dne-24-06-2024.zip         (no "z-"/"ze-" prefix)
#   zapis-ze-14-zmp-dne-22-01-2024-2-1.zip           (no "zasedani-")
#   zapis-ze-38-zasedadni-zmp-dne-25-05-2026-2.zip   (source typo "zasedadni")
_ZIP_LINK_RE = re.compile(
    r'href="(/data/files/[^"]+/zapis-(?:z[e]?-)?([0-9ivxIVX]+)-(?:zased[a-z]*-)?zmp-dne-(\d{1,2})-(\d{1,2})-(\d{4})[^"]*\.zip)"'
)


@contextlib.contextmanager
def _force_ipv4():
    """Temporarily make urllib3 (requests' transport) resolve AF_INET only, restored afterward."""
    original = urllib3_connection.allowed_gai_family
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        urllib3_connection.allowed_gai_family = original


def _request(method: str, url: str, retries: int = 5, timeout: int = 90, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with _force_ipv4():
                resp = requests.request(method, url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 15 * attempt
                logging.warning("%s %s failed (attempt %d/%d): %s — retrying in %ds", method, url, attempt, retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Failed to {method} {url} after {retries} attempts") from last_exc


def _listing_pages(sources_path: Path) -> list[str]:
    """Pages from sources.yml, plus any zmp-<year> up to the current calendar year (so the list
    needs no edit each January when a new year's page appears)."""
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    pages = list(cfg["listing"]["pages"])
    have_years = {int(m.group(1)) for p in pages if (m := re.search(r"zmp-(\d{4})", p))}
    for y in range(2022, date.today().year + 1):
        if y not in have_years:
            pages.append(f"{_BASE}/zmp-{y}")
    return sorted(set(pages))


def discover_meetings(sources_path: Path = _DEFAULT_SOURCES) -> list[dict[str, Any]]:
    """Returns [{"meeting_no": int, "date": "YYYY-MM-DD", "zip_url": str}, ...] for every
    current-term ZmP meeting listed across the zmp-<year> pages, chronological, deduped by
    (meeting_no, date) keeping the first href seen (re-uploads get a -2/-3 suffix)."""
    seen: dict[tuple[int, str], dict[str, Any]] = {}
    for page in _listing_pages(sources_path):
        try:
            resp = _request("GET", page)
        except RuntimeError:
            logging.warning("Listing page %s unreachable — skipping (a future year may not exist yet)", page)
            continue
        page_hits = 0
        for path, no_str, d, m, y in _ZIP_LINK_RE.findall(resp.text):
            if not no_str.isdigit():
                continue  # roman-numeral meeting numbers only ever appear in the previous term
            mdate = date(int(y), int(m), int(d))
            if mdate < _TERM_START:
                continue
            key = (int(no_str), mdate.isoformat())
            if key in seen:
                continue
            seen[key] = {"meeting_no": int(no_str), "date": mdate.isoformat(), "zip_url": f"{_BASE}{path}"}
            page_hits += 1
        logging.info("Listing %s: %d current-term meetings", page, page_hits)

    meetings = sorted(seen.values(), key=lambda x: (x["date"], x["meeting_no"]))
    # sanity: meeting numbers should be strictly increasing with date across the term
    nos = [x["meeting_no"] for x in meetings]
    if nos != sorted(nos):
        logging.warning("Meeting numbers not monotonic with date: %s", nos)
    logging.info("Discovered %d current-term meetings (%s .. %s)", len(meetings),
                 meetings[0]["date"] if meetings else None, meetings[-1]["date"] if meetings else None)
    return meetings


def _ascii_lower(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


def _extract_voting_file(zip_bytes: bytes, meeting_no: int) -> tuple[bytes, str]:
    """Pick the one ZIP member whose diacritic-folded name contains 'hlasovan' and ends .pdf or
    .txt (the filename varies wildly and is sometimes de-diacriticised — "Hlasovani 5 bez osobnich
    udaju.pdf" — or, rarely, a .txt instead of a .pdf — meeting 20). Prefer .pdf over .txt, then
    the shortest name. Returns (bytes, "pdf"|"txt"); the 'HLASOVÁNÍ č.' structure check is
    standardize.py's job."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    candidates = [
        n for n in zf.namelist()
        if "hlasovan" in _ascii_lower(n)
        and (_ascii_lower(n).endswith(".pdf") or _ascii_lower(n).endswith(".txt"))
        and not n.endswith("/")
    ]
    if not candidates:
        raise ValueError(f"Meeting {meeting_no}: no 'hlasovan*.(pdf|txt)' member in ZIP (members: {zf.namelist()})")
    chosen = min(candidates, key=lambda n: (0 if _ascii_lower(n).endswith(".pdf") else 1, len(n), n))
    ext = "pdf" if _ascii_lower(chosen).endswith(".pdf") else "txt"
    data = zf.read(chosen)
    if ext == "pdf" and not data.startswith(b"%PDF"):
        raise ValueError(f"Meeting {meeting_no}: chosen member {chosen!r} is not a PDF")
    logging.info("Meeting %d: extracted %r (%d bytes, %s)", meeting_no, chosen, len(data), ext)
    return data, ext


def download(sources_path: Path = _DEFAULT_SOURCES, out_dir: Path = _DEFAULT_OUT_DIR) -> Path:
    meetings = discover_meetings(sources_path)
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"meetings": []}
    for mtg in meetings:
        stem = f"m{mtg['meeting_no']:02d}_{mtg['date']}"
        existing = next((p for p in pdf_dir.glob(f"{stem}.*")), None)
        if existing is not None:
            logging.info("Already cached: %s", existing)
            out_name = existing.name
        else:
            resp = _request("GET", mtg["zip_url"])
            data, ext = _extract_voting_file(resp.content, mtg["meeting_no"])
            out_name = f"{stem}.{ext}"
            (pdf_dir / out_name).write_bytes(data)
            logging.info("Wrote %s (%d bytes)", pdf_dir / out_name, len(data))
        manifest["meetings"].append(
            {"meeting_no": mtg["meeting_no"], "date": mtg["date"], "zip_url": mtg["zip_url"], "vote_file": out_name}
        )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote manifest to %s (%d meetings)", manifest_path, len(manifest["meetings"]))
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.sources), Path(args.out_dir))


if __name__ == "__main__":
    main()
