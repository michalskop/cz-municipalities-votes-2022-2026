"""Shared helpers for Praha's analysis runners (praha/scripts/analyses/run_*.py).

These runners invoke the *shared, unmodified* scripts from the separate `legislature-data-analyses`
repository (per praha/analyses/README.md — city-specific content lives only in the `*_definition.json`
files, never in the analysis code). The `--script`/`--flourish-script`-style external-repo-location
argument follows the same convention as cz-psp-data-2025-202x's `scripts/analyses/run_*.py` (see
that repo for the pattern this was modeled on) — the analysis repo's location relative to this repo
is not assumed, since it isn't checked out inside this repo.

Unlike the psp runners, there is no B2 download step here: per plan.md decision D3, city data is
small enough to commit directly, so `praha/data/votes.csv` and `praha/data/vote_events.json` are
always already present (written by `praha/scripts/standardize.py`).
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

_CITY_ROOT = Path(__file__).resolve().parents[2]


def ensure_dt_schema_file(*, script_path: Path, relative_schema_path: str, url: str) -> None:
    """Ensure a published dt/dt.analyses schema JSON exists where the external analysis script
    expects to find it: relative to the external script's own file location, mirroring how e.g.
    `legislature-data-analyses/govity/govity.py` computes `_SCHEMA_BASE` as
    `Path(__file__).parent.parent.parent / "legislature-data-standard" / "dist"`.

    This is computed relative to --script rather than hardcoded to /tmp (as cz-psp's runners do)
    so it works whether the analyses repo is checked out locally (this session) or in a future CI
    job's own scratch directory (C6, out of scope for this task).
    """
    schema_base = script_path.resolve().parent.parent.parent / "legislature-data-standard" / "dist"
    out_path = schema_base / relative_schema_path
    if out_path.exists() and out_path.stat().st_size > 0:
        logging.debug("Schema already present: %s", out_path)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    logging.info("Fetching schema %s -> %s", url, out_path)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    out_path.write_text(resp.text, encoding="utf-8")


_SCHEMA_BASE_URL = "https://michalskop.github.io/legislature-data-standard"

# (relative path under .../legislature-data-standard/dist/, source URL)
COMMON_SCHEMAS = [
    ("dt/latest/schemas/votes-table.dt.json", f"{_SCHEMA_BASE_URL}/dt/0.1.0/schemas/votes-table.dt.json"),
    ("dt/latest/schemas/vote-events.dt.json", f"{_SCHEMA_BASE_URL}/dt/0.1.0/schemas/vote-events.dt.json"),
    (
        "dt.analyses/all-members/latest/schemas/all-members.dt.analyses.json",
        f"{_SCHEMA_BASE_URL}/dt.analyses/all-members/latest/schemas/all-members.dt.analyses.json",
    ),
]


def definition_schema(slug: str) -> tuple[str, str]:
    return (
        f"dt.analyses/{slug}-definition/latest/schemas/{slug}-definition.dt.analyses.json",
        f"{_SCHEMA_BASE_URL}/dt.analyses/{slug}-definition/latest/schemas/{slug}-definition.dt.analyses.json",
    )


def output_schema(slug: str) -> tuple[str, str]:
    return (
        f"dt.analyses/{slug}/latest/schemas/{slug}.dt.analyses.json",
        f"{_SCHEMA_BASE_URL}/dt.analyses/{slug}/latest/schemas/{slug}.dt.analyses.json",
    )


def ensure_all_schemas(script_path: Path, extra: list[tuple[str, str]]) -> None:
    for relative_path, url in COMMON_SCHEMAS + extra:
        ensure_dt_schema_file(script_path=script_path, relative_schema_path=relative_path, url=url)
