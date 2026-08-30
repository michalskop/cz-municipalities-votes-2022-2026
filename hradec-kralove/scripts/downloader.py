"""Download Hradec Králové's roll-call vote JSON feed to hradec-kralove/work/raw/.

Source and format notes: hradec-kralove/config/sources.yml. Same `zastupko.fit.vutbr.cz` backend
as Brno/Most/most-rada (discovered via that backend's `/flask/municipalities` endpoint, not a
mirror-vs-origin situation like Brno's original discovery — this IS the origin already). The feed
covers the whole term to date in one response — always re-fetch, never trust a stale copy. NOTE: as
of 2026-08-30 this feed itself has a known ~1-year staleness gap (last meeting 2025-08-26 vs. real
meetings confirmed through 2026-06-22) — see sources.yml's `known_staleness_gap` for the full
investigation trail; not a downloader bug, a documented source-side limitation.

Same IPv6/retry handling as Brno's/Most's downloaders (verbatim port, city-agnostic by
construction — only this docstring and the output filename differ): GitHub Actions runners can't
route to zastupko.fit.vutbr.cz's AAAA record (`_force_ipv4` works around it), and the same host has
shown occasional connect-timeout flakiness escalated to 5 retries/15s*attempt backoff (see this
repo's memory: scraper-resilience-practices).
"""
import argparse
import contextlib
import logging
import socket
import time
from pathlib import Path

import requests
import urllib3.util.connection as urllib3_connection
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"


@contextlib.contextmanager
def _force_ipv4():
    """Temporarily make urllib3 (requests' transport) resolve AF_INET only.

    Scoped to this context manager's block, not a permanent process-wide patch — restores the
    original resolver afterward so this script's IPv4-forcing choice can't leak into unrelated
    code that happens to import this module.
    """
    original = urllib3_connection.allowed_gai_family
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
    try:
        yield
    finally:
        urllib3_connection.allowed_gai_family = original


def download(
    sources_path: Path = _DEFAULT_SOURCES,
    out_path: Path = _DEFAULT_OUT,
    timeout: int = 120,
    retries: int = 5,
) -> Path:
    """Fetch the source JSON named in sources.yml and save it verbatim.

    retries=5 with a 15s*attempt backoff, same escalated setting as Brno's/Most's/most-rada's
    downloaders — zastupko.fit.vutbr.cz's GitHub-Actions-specific connect-timeout flakiness (see
    this repo's memory: scraper-resilience-practices) has recurred enough times across those
    cities that starting Hradec Králové at the already-escalated setting is the right default,
    not the original 3/10s baseline.
    """
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    url = cfg["zastupko_current"]["url"]

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        logging.info("Downloading %s (attempt %d/%d)", url, attempt, retries)
        try:
            with _force_ipv4():
                resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                wait = 15 * attempt
                logging.warning("Download failed (attempt %d/%d): %s — retrying in %ds", attempt, retries, exc, wait)
                time.sleep(wait)
    else:
        raise RuntimeError(f"Failed to download {url} after {retries} attempts") from last_exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    logging.info("Wrote %s (%d bytes)", out_path, len(resp.content))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default=str(_DEFAULT_SOURCES))
    parser.add_argument("--out", default=str(_DEFAULT_OUT))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    download(Path(args.sources), Path(args.out))


if __name__ == "__main__":
    main()
