"""Validate votes/vote-events/motions against the published dt "record" schemas.

These three publish as JSON-Schema-shaped documents ({"definitions": {<Name>: {...}}}), unlike
persons/organizations/memberships (see validate_tables.py) — a known inconsistency in the
upstream standard (legislature-audit-standard-2026-07-07.md, S1/S5), not something to paper over
here. votes is a CSV (one row per vote); vote-events and motions are JSON arrays.

Shared across all cities (gate G1 in the project plan).
"""
import argparse
import csv
import json
import logging
from pathlib import Path

import requests
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]

RECORD_DATASETS = {
    "votes": "votes.csv",
    "vote-events": "vote_events.json",
    "motions": "motions.json",
}


def _load_schema(url: str) -> dict:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def _required_and_allowed(schema: dict, definition: str) -> tuple[set[str], set[str]]:
    node = schema.get("definitions", {}).get(definition, {})
    # Some record schemas wrap the per-record object in an outer "type": "array"
    # definition (e.g. motions.dt.json's "DtMotions" is {"type": "array", "items":
    # {...}}), unlike DtVoteEvent/DtTableVotesRow which are top-level objects. The
    # actual "properties"/"required" live at node["items"] in that case — unwrap it so
    # both schema shapes validate the same way (found + fixed during C7, see
    # config/schemas.yml's comment on the motions entry for the reproduction).
    if node.get("type") == "array" and "items" in node:
        node = node["items"]
    allowed = set(node.get("properties", {}).keys())
    required = set(node.get("required", []))
    return required, allowed


def _validate_records(records: list[dict], required: set[str], allowed: set[str], name: str) -> None:
    if not records:
        raise ValueError(f"{name}: no rows")

    for i, r in enumerate(records[:50]):
        missing = sorted(k for k in required if k not in r or r.get(k) in (None, ""))
        if missing:
            raise ValueError(f"{name}: record {i} missing required keys: {missing}")
        unexpected = sorted(k for k in r.keys() if k not in allowed)
        if unexpected:
            raise ValueError(f"{name}: record {i} unexpected keys: {unexpected}")


def validate_votes_csv(csv_path: Path, required: set[str], allowed: set[str]) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    _validate_records(records, required, allowed, str(csv_path))


def validate_json_array(json_path: Path, required: set[str], allowed: set[str]) -> None:
    records = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{json_path}: expected a JSON array")
    _validate_records(records, required, allowed, str(json_path))


def validate_from_config(config_path: Path, data_dir: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    for key, filename in RECORD_DATASETS.items():
        entry = cfg.get(key, {})
        url = entry.get("url")
        definition = entry.get("definition")
        if not url or not definition:
            raise ValueError(f"{config_path}: missing url/definition for {key}")

        record_path = data_dir / filename
        if not record_path.exists():
            logging.info("Skipping validation for %s (missing %s)", key, record_path)
            continue

        schema = _load_schema(url)
        required, allowed = _required_and_allowed(schema, definition)

        if filename.endswith(".csv"):
            validate_votes_csv(record_path, required, allowed)
        else:
            validate_json_array(record_path, required, allowed)
        logging.info("Validated %s against %s (%s)", record_path, url, definition)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemas", default=str(_REPO_ROOT / "config" / "schemas.yml"))
    parser.add_argument("--data-dir", required=True, help="e.g. brno/data")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    validate_from_config(Path(args.schemas), Path(args.data_dir))


if __name__ == "__main__":
    main()
