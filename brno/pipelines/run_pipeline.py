"""Brno pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check (G2).

Usage:
    python brno/pipelines/run_pipeline.py
    python brno/pipelines/run_pipeline.py --skip-download   # reuse the last downloaded JSON

Exits non-zero if any stage fails: a failed download, a hard error in the standardizer (schema
drift on the raw JSON's top-level shape — see standardize.py's `_load_raw`), or a failed G1 schema
validation. The documented data-quality findings (corrupted hlas values on one event, the G5
namesake disambiguation, G2 result-consistency mismatches) do NOT fail the run — they are logged
and included in the quality report, per the plan's G2/G5 acceptance checks (detect + log + never
silently drop/merge, but don't hard-fail on documented, bounded exceptions).

No party-affiliation/coalition step here (unlike Praha's C6, which runs `party_affiliation.py`
after this): Brno's C2 deliberately does not build party/coalition organizations (C4's job, needs
owner sign-off per D7) — see standardize.py's module docstring for the scope boundary.
"""
import argparse
import json
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


def run(skip_download: bool = False, protocol_cross_check_sample: int = 10) -> dict:
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

    # G2: source cross-check. standardize() already computed the primary (prijato-consistency)
    # signal; here we additionally attempt the best-effort live protocol-page cross-check.
    vote_events = json.loads((data_dir / "vote_events.json").read_text(encoding="utf-8"))
    protocol_check = standardize.run_protocol_cross_check(
        vote_events, sample_size=protocol_cross_check_sample
    )
    report["protocol_cross_check"] = protocol_check

    mismatch_rate = report["result_consistency"]["mismatch"] / max(
        1, report["result_consistency"]["match"] + report["result_consistency"]["mismatch"]
    )
    logging.info(
        "G2 cross-check summary: %d/%d events prijato-consistency match (%.2f%% mismatch rate "
        "among decidable events), %d events had a corrupted-vote anomaly (logged, not "
        "fabricated), protocol-page cross-check reached %d/%d sampled URLs",
        report["result_consistency"]["match"],
        report["result_consistency"]["match"] + report["result_consistency"]["mismatch"],
        mismatch_rate * 100,
        len(report["corrupted_hlas_events"]),
        protocol_check["reachable"],
        protocol_check["attempted"],
    )
    # Bound: a small mismatch rate is expected (supermajority/quorum rules this simple check
    # doesn't model); a large one would suggest a real parsing bug. 5% is a conservative ceiling —
    # observed rate as of 2026-08-07 is far below this (see report/commit message).
    if mismatch_rate > 0.05:
        raise RuntimeError(
            f"G2 gate: prijato-consistency mismatch rate {mismatch_rate:.2%} exceeds the 5% "
            "bound — this looks like a parsing bug, not the expected small number of "
            "supermajority/quorum exceptions. Refusing to pass silently."
        )

    report_path = _CITY_ROOT / "work" / "reports" / "g2_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote full quality report to %s (gitignored, work/)", report_path)

    logging.info("Brno pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse the previously downloaded raw JSON instead of fetching again",
    )
    parser.add_argument(
        "--protocol-sample",
        type=int,
        default=10,
        help="number of official protocol pages to sample for the best-effort G2 cross-check",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download, protocol_cross_check_sample=args.protocol_sample)


if __name__ == "__main__":
    main()
