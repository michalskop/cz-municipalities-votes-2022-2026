"""Download Brno's roll-call vote JSON feed to brno/work/raw/.

Source and format notes: brno/config/sources.yml. The original primary
(`kod.brno.cz/zastupitelstvo/`) has been returning HTTP 503 since at least 2026-08-04 (re-verified
still down 2026-08-06 and 2026-08-07) — this downloader fetches the `zastupko_current` fallback
instead (a live, per-person JSON feed on a different platform, `zastupko.cz`, discovered via the
ArcGIS Hub item search; see sources.yml for the full trail). The feed is ~10 MB and covers the
whole term to date in one response — always re-fetch, never trust a stale copy. `zastupko_current`
was repointed 2026-08-26 from a stale mirror to the real origin server (see sources.yml's
`mirror_vs_origin` note) — the previously documented "several months" lag was an artifact of that
wrong URL, not a property of the source itself; re-fetching is still the right default, just no
longer a workaround for a known-stale copy.

IPv6 note (2026-08-26, first nightly CI run after the origin switch): the origin
(zastupko.fit.vutbr.cz) publishes both an A and an AAAA record; GitHub Actions runners raised
`ConnectionError: ... Network is unreachable` trying the AAAA (IPv6) address specifically — a
well-documented GitHub Actions runner limitation (IPv6 looks configured on the interface but isn't
actually routed to many external hosts), not a problem with the source or this repo's code, and
not reproducible in this project's own dev sandbox (which resolves IPv6 fine). Forcing IPv4-only
resolution for the duration of this script's one request works around it — see `_force_ipv4`.

Retry note (2026-08-27): a later nightly run hit a plain `ConnectTimeoutError` (120s, IPv4 this
time — a different failure mode than the IPv6 issue above) reaching the same origin server. FIT
VUT's own infrastructure is not under this project's control and evidently has occasional
availability blips; a bare single-attempt `requests.get` turns any one of them into a full pipeline
failure for the night. Retries with backoff (same pattern already proven in
ostrava/scripts/downloader.py, built the next day after independently hitting the same class of
problem against a different server) — 3 attempts, 10s/20s between them.
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
    retries: int = 3,
) -> Path:
    """Fetch the source JSON named in sources.yml and save it verbatim."""
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
                wait = 10 * attempt
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
