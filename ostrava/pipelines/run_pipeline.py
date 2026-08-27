"""Ostrava pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check (G2).

Usage:
    python ostrava/pipelines/run_pipeline.py
    python ostrava/pipelines/run_pipeline.py --skip-download   # reuse the cached raw/ pages

Exits non-zero if any stage fails: a failed download, a hard error in the standardizer, a failed
G1 schema validation, or a G2 mismatch rate above the bound below. Data-quality findings that are
documented and bounded (unmapped cast values, individual result mismatches) do NOT fail the run —
they are logged and included in the quality report, matching Praha/Brno's G2/G5 precedent.

No party-affiliation/coalition step here (matches Brno's C2 precedent): Ostrava's per-vote party
grouping is only sometimes populated (see standardize.py's module docstring) and real party/klub
data needs the live composition page as its primary source instead — that's C4's job, needing
owner sign-off (D7), not C9's.

G2 caveat, more limited than Praha/Brno: Ostrava's vote pages are the ONLY published source — there
is no separate independently-published aggregate to cross-check against (unlike Praha's CSV
aggregate columns or Brno's `prijato` field asserted by a different system). The G2 signal here is
therefore a pure self-consistency check (recomputed per-option tally vs. the same page's own
published totals line) — it catches parsing bugs, which is its real purpose, but is not independent
verification of the page's own correctness.
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

_MISMATCH_BOUND = 0.02  # a pure self-consistency check should be ~exact; even 2% suggests a bug.


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


def run(skip_download: bool = False, delay: float = 0.3) -> dict:
    raw_dir = Path(downloader._DEFAULT_OUT_DIR)
    if not skip_download:
        downloader.download(delay=delay)
    elif not (raw_dir / "meetings.json").exists():
        raise FileNotFoundError(f"--skip-download given but {raw_dir / 'meetings.json'} does not exist")
    else:
        logging.info("Skipping download, reusing cached pages under %s", raw_dir)

    data_dir = _CITY_ROOT / "data"
    report = standardize.standardize(raw_dir=raw_dir, out_dir=data_dir)

    _run_validator("validate_tables.py", data_dir)
    _run_validator("validate_records.py", data_dir)

    total = report["result_consistency"]["match"] + report["result_consistency"]["mismatch"]
    mismatch_rate = report["result_consistency"]["mismatch"] / max(1, total)
    logging.info(
        "G2 self-consistency: %d/%d events match their own published aggregate (%.2f%% mismatch)",
        report["result_consistency"]["match"], total, mismatch_rate * 100,
    )
    if mismatch_rate > _MISMATCH_BOUND:
        raise RuntimeError(
            f"G2 gate: self-consistency mismatch rate {mismatch_rate:.2%} exceeds the "
            f"{_MISMATCH_BOUND:.0%} bound — this looks like a parsing bug, refusing to pass silently."
        )

    report_path = _CITY_ROOT / "work" / "reports" / "g2_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote full quality report to %s (gitignored, work/)", report_path)

    logging.info("Ostrava pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true", help="reuse cached raw/ pages instead of crawling again")
    parser.add_argument("--delay", type=float, default=0.3, help="politeness delay between requests (seconds)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download, delay=args.delay)


if __name__ == "__main__":
    main()
