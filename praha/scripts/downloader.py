"""Download Praha's roll-call vote CSV (open-data export) to praha/work/raw/.

Source and format notes: praha/config/sources.yml (D8 / research/R1-praha.md in the planning
repo). The file is a live export (irregular update cadence) — always re-fetch, never trust a
stale copy.
"""
import argparse
import logging
from pathlib import Path

import requests
import yaml

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SOURCES = _CITY_ROOT / "config" / "sources.yml"
_DEFAULT_OUT = _CITY_ROOT / "work" / "raw" / "Vysledky_hlasovani_ZHMP.csv"


def download(
    sources_path: Path = _DEFAULT_SOURCES,
    out_path: Path = _DEFAULT_OUT,
    timeout: int = 120,
) -> Path:
    """Fetch the source CSV named in sources.yml and save it verbatim."""
    cfg = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
    url = cfg["vysledky_hlasovani_zhmp"]["primary"]["url"]

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
