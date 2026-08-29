"""Download Ústí nad Labem's ZM (city council) roll-call vote PDFs to
usti-nad-labem/work/raw/.

Source shape fully documented in usti-nad-labem/config/sources.yml (research pass 2026-08-29 —
see this repo's memory: usti-nad-labem-source-research). Summary: the cleanest source of any city
built so far — no login, no session cookies, no format drift, no font-encoding corruption. The
listing page directly enumerates every meeting of the current term with a predictable PDF URL per
meeting (`/files/rm-zm/h{NN}zm{YYYY}.pdf`); ONE PDF bundles ALL of that meeting's votes (unlike
Plzeň's one-PDF-per-vote), so the whole term needs only ~24 downloads total.

Retry-with-backoff included proactively (same practice as every other city, even though this
source has shown no flakiness during research) — no IPv4-forcing needed (usti.cz has no AAAA
record, confirmed via `dig`, unlike zastupko.fit.vutbr.cz).
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

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT_DIR = _CITY_ROOT / "work" / "raw"
_LISTING_URL = "https://www.usti.cz/cz/uredni-portal/sprava-mesta/mesto-jeho-organy/zastupitelstvo-mesta/zapisy-z-jednani-zm.html"
_BASE = "https://www.usti.cz"

_PDF_LINK_RE = re.compile(r'href="(/files/rm-zm/h(\d+)zm(\d{4})\.pdf)"')


def _request(method: str, url: str, retries: int = 4, timeout: int = 60, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(method, url, timeout=timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 10 * attempt
                logging.warning("%s %s failed (attempt %d/%d): %s -- retrying in %ds", method, url, attempt, retries, exc, wait)
                time.sleep(wait)
    raise RuntimeError(f"Failed to {method} {url} after {retries} attempts") from last_exc


def discover_meetings() -> list[dict[str, Any]]:
    """Returns [{"meeting_no": int, "year": int, "pdf_url": str}, ...] for every meeting listed on
    the current-term listing page, in the order they appear (already chronological per
    sources.yml's confirmed h01..h24 sequential-per-term numbering)."""
    resp = _request("GET", _LISTING_URL)
    meetings = []
    seen = set()
    for path, no_str, year_str in _PDF_LINK_RE.findall(resp.text):
        key = (no_str, year_str)
        if key in seen:
            continue
        seen.add(key)
        meetings.append({"meeting_no": int(no_str), "year": int(year_str), "pdf_url": f"{_BASE}{path}"})
    meetings.sort(key=lambda m: m["meeting_no"])
    logging.info("Discovered %d meetings on the listing page", len(meetings))
    return meetings


def download(out_dir: Path = _DEFAULT_OUT_DIR) -> Path:
    meetings = discover_meetings()
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"meetings": []}
    for m in meetings:
        pdf_path = pdf_dir / f"h{m['meeting_no']:02d}zm{m['year']}.pdf"
        if not pdf_path.exists():
            resp = _request("GET", m["pdf_url"])
            pdf_path.write_bytes(resp.content)
            logging.info("Wrote %s (%d bytes)", pdf_path, len(resp.content))
        else:
            logging.info("Already cached: %s", pdf_path)
        manifest["meetings"].append(
            {"meeting_no": m["meeting_no"], "year": m["year"], "pdf_url": m["pdf_url"], "pdf_file": pdf_path.name}
        )

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote manifest to %s", manifest_path)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.out_dir))


if __name__ == "__main__":
    main()
