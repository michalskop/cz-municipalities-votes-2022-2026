"""Download Most's roll-call vote JSON feed to most/work/raw/.

Source and format notes: most/config/sources.yml. Most is on the same shared FIT VUT `zastupko.cz`
backend Brno uses (confirmed 2026-08-27 — the URL pattern `zastupko.fit.vutbr.cz/flask/<city>/
zastupitelstvo/<dataset_id>/dataset` serves multiple cities, not just Brno). Dataset id 8 = Most's
2022-2026 term. The feed is one JSON response covering the whole term to date — always re-fetch,
never trust a stale copy.

IPv4 + retry: ported directly from brno/scripts/downloader.py's already-hardened version (see that
file's docstring for the two real incidents — an IPv6 routing issue and a plain connect timeout —
that motivated each piece) rather than waiting to independently hit the same problems against this
city's copy of the same infrastructure.
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
_DEFAULT_OUT = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_8.json"


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

    retries=5 with a 15s*attempt backoff (was 3/10s): zastupko.fit.vutbr.cz's GitHub-Actions-
    specific connect-timeout flakiness (see this repo's memory: scraper-resilience-practices)
    became frequent enough in practice (5 failures across Brno/Most in one 2026-08-28/29 session,
    up from the occasional single blip originally observed) that the old 3-attempt/~30s-total
    window wasn't reliably outlasting the outage. 5 attempts/15s*attempt gives ~185s of total wait
    across retries, on top of each attempt's own 120s connect timeout.
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
