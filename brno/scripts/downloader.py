"""Download Brno's roll-call vote JSON feed to brno/work/raw/.

Source and format notes: brno/config/sources.yml. The original primary
(`kod.brno.cz/zastupitelstvo/`) has been returning HTTP 503 since at least 2026-08-04 (re-verified
still down 2026-08-06 and 2026-08-07) — this downloader fetches the `zastupko_current` fallback
instead (a live, per-person JSON feed on a different platform, `zastupko.cz`, discovered via the
ArcGIS Hub item search; see sources.yml for the full trail). The feed is ~8.5 MB and covers the
whole term to date in one response — always re-fetch, never trust a stale copy (the feed itself is
also known to lag the council's actual meeting schedule by several months as of 2026-08-07, see
sources.yml's `coverage_and_known_gap` note; re-fetching won't fix that, only the upstream feed
catching up will, but the downloader should still always pull the latest available snapshot).
"""
import argparse
import logging
from pathlib import Path

import requests
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT = _CITY_ROOT / "work" / "raw" / "zastupko_dataset_9.json"


def download(
    sources_path: Path = _DEFAULT_SOURCES,
    out_path: Path = _DEFAULT_OUT,
    timeout: int = 120,
) -> Path:
    """Fetch the source JSON named in sources.yml and save it verbatim."""
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    url = cfg["zastupko_current"]["url"]

    logging.info("Downloading %s", url)
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()

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
