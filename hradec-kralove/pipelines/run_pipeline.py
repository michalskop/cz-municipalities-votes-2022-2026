"""Hradec Králové pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check
(G2).

Usage:
    python hradec-kralove/pipelines/run_pipeline.py
    python hradec-kralove/pipelines/run_pipeline.py --skip-download   # reuse the last downloaded JSON

Exits non-zero if any stage fails: a failed download, a hard error in the standardizer (schema
drift on the raw JSON's top-level shape — see standardize.py's `_load_raw`), or a failed G1 schema
validation. G2 result-consistency mismatches do NOT fail the run below a 5% bound — logged and
included in the quality report, per the plan's G2 acceptance checks (detect + log, don't hard-fail
on documented, bounded exceptions). Unlike Brno, this feed has NO `urlProtokol` values populated on
any event (checked at C9 time), so there is no protocol-page cross-check step here — Brno's
`run_protocol_cross_check` would always be a no-op against this feed.

No party-affiliation/coalition step here (matches every zastupko-network city's C2/C9 precedent):
building real party/coalition organizations from the feed's `politickeSubjekty`/`koalice`/`lidr`
data is C4's job, needing owner sign-off (D7), not this pipeline's.
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

_CITY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _CITY_ROOT.parent

sys.path.insert(0, str(_CITY_ROOT / "scripts"))
import downloader  # noqa: E402
import standardize  # noqa: E402


def _run_validator(script_name: str, data_dir: Path) -> None:
    script_path = _REPO_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script_path), "--data-dir", str(data_dir)]
    logging.info("Running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.stdout:
        logging.info("%s stdout:\n%s", script_name, result.stdout.strip())
    if result.returncode != 0:
        logging.error("%s stderr:\n%s", script_name, result.stderr.strip())
        raise RuntimeError(f"{script_name} failed (G1 schema gate) — see log above")
    logging.info("%s passed (G1)", script_name)


def run(skip_download: bool = False) -> dict:
    raw_path = Path(downloader._DEFAULT_OUT)
    if not skip_download:
        raw_path = downloader.download()
    elif not raw_path.exists():
        raise FileNotFoundError(f"--skip-download given but {raw_path} does not exist")
    else:
        logging.info("Skipping download, reusing %s", raw_path)

    data_dir = _CITY_ROOT / "data"
    report = standardize.standardize(raw_path=raw_path, out_dir=data_dir)

    # G1: schema gate (shared validators)
    _run_validator("validate_tables.py", data_dir)
    _run_validator("validate_records.py", data_dir)

    # G2: source cross-check (self-consistency only, see module docstring).
    mismatch_rate = report["result_consistency"]["mismatch"] / max(
        1, report["result_consistency"]["match"] + report["result_consistency"]["mismatch"]
    )
    logging.info(
        "G2 cross-check summary: %d/%d events prijato-consistency match (%.2f%% mismatch rate "
        "among decidable events), %d events had a corrupted-vote anomaly (logged, not fabricated)",
        report["result_consistency"]["match"],
        report["result_consistency"]["match"] + report["result_consistency"]["mismatch"],
        mismatch_rate * 100,
        len(report["corrupted_hlas_events"]),
    )
    # Bound: a small mismatch rate is expected (supermajority/quorum rules this simple check
    # doesn't model); a large one would suggest a real parsing bug. 5% ceiling, same as Brno's —
    # observed rate at C9 time is 13/1620 = 0.80%, far below this.
    if mismatch_rate > 0.05:
        raise RuntimeError(
            f"G2 gate: prijato-consistency mismatch rate {mismatch_rate:.2%} exceeds the 5% "
            "bound — this looks like a parsing bug, not the expected small number of "
            "supermajority/quorum exceptions. Refusing to pass silently."
        )

    logging.info("Hradec Králové pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse the previously downloaded raw JSON instead of fetching again",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
