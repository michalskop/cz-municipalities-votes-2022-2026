"""Plzeň pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check (G2).

Usage:
    python plzen/pipelines/run_pipeline.py
    python plzen/pipelines/run_pipeline.py --skip-download   # reuse the cached raw/ corpus

Exits non-zero if any stage fails: a failed download, a hard error in the standardizer, a failed
G1 schema validation, or a G2 mismatch rate above the bound below. The documented, bounded
exceptions (era1's ~1% supermajority/quorum result-consistency mismatches, era3's occasional
unresolved klub-text cell -- see standardize.py's module docstring and
plzen/config/sources.yml's protocol_file_format section) do NOT fail the run -- logged and
included in the report, matching every prior city's G2/G5 acceptance-check precedent.

G2 here is a pure self-consistency check (recomputed yes/no majority vs. era1's own occasionally-
stated "Návrh byl/nebyl přijat" text, where present) -- there is no independently-published
aggregate to cross-check against for this source, same caveat as Ostrava's.

No party-affiliation/coalition step here (matches every other city's C2 precedent): building real
party/klub organizations from the klub text already captured per vote is C4's job, needing owner
sign-off (D7), not C9's/C2's.

Requires `pdftotext` (poppler-utils) on PATH for era3 (PDF) protocol parsing -- a required system
dependency for this city only, not needed by any other city in this repo.
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

_MISMATCH_BOUND = 0.05  # matches Brno's precedent: era1's own stated result vs our majority-rule computation


def _run_validator(script_name: str, data_dir: Path) -> None:
    script_path = _REPO_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script_path), "--data-dir", str(data_dir)]
    logging.info("Running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.stdout:
        logging.info("%s stdout:\n%s", script_name, result.stdout.strip())
    if result.returncode != 0:
        logging.error("%s stderr:\n%s", script_name, result.stderr.strip())
        raise RuntimeError(f"{script_name} failed (G1 schema gate) -- see log above")
    logging.info("%s passed (G1)", script_name)


def _compute_era1_mismatch_rate(raw_dir: Path, manifest: dict) -> tuple[int, int]:
    """Pure self-consistency check: for era1 protocols with an explicit stated result, compare
    against the recomputed yes/no majority. Returns (mismatches, total_with_stated_result)."""
    mismatches = 0
    total = 0
    for meeting, point_id, raw, era in standardize._iter_protocols(raw_dir, manifest):
        if era != "era1":
            continue
        parsed = standardize.parse_era1(raw)
        if not parsed.get("result_text"):
            continue
        total += 1
        yes = sum(1 for v in parsed["votes"] if v["option_raw"] == "Pro")
        no = sum(1 for v in parsed["votes"] if v["option_raw"] == "Proti")
        computed_pass = yes > no
        stated_pass = "nebyl" not in parsed["result_text"]
        if computed_pass != stated_pass:
            mismatches += 1
    return mismatches, total


def run(skip_download: bool = False, delay: float = 0.2) -> dict:
    raw_dir = downloader._DEFAULT_OUT_DIR
    if not skip_download:
        downloader.download(delay=delay)
    elif not (raw_dir / "manifest.json").exists():
        raise FileNotFoundError(f"--skip-download given but {raw_dir / 'manifest.json'} does not exist")
    else:
        logging.info("Skipping download, reusing cached corpus under %s", raw_dir)

    data_dir = _CITY_ROOT / "data"
    report = standardize.standardize(raw_dir=raw_dir, out_dir=data_dir)

    _run_validator("validate_tables.py", data_dir)
    _run_validator("validate_records.py", data_dir)

    manifest = standardize._load_manifest(raw_dir)
    mismatches, total = _compute_era1_mismatch_rate(raw_dir, manifest)
    mismatch_rate = mismatches / max(1, total)
    logging.info(
        "G2 self-consistency (era1 only, %d/%d events have a stated result to check against): "
        "%d mismatches (%.2f%%)",
        total, report["by_era"]["era1"], mismatches, mismatch_rate * 100,
    )
    if mismatch_rate > _MISMATCH_BOUND:
        raise RuntimeError(
            f"G2 gate: era1 result-consistency mismatch rate {mismatch_rate:.2%} exceeds the "
            f"{_MISMATCH_BOUND:.0%} bound -- this looks like a parsing bug, not the expected "
            "small number of supermajority/quorum exceptions. Refusing to pass silently."
        )

    report["era1_g2_mismatches"] = mismatches
    report["era1_g2_total_with_stated_result"] = total

    report_path = _CITY_ROOT / "work" / "reports" / "g2_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote full quality report to %s (gitignored, work/)", report_path)

    logging.info("Plzeň pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-download", action="store_true", help="reuse the cached raw/ corpus instead of re-fetching")
    parser.add_argument("--delay", type=float, default=0.2, help="politeness delay between protocol downloads (seconds)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download, delay=args.delay)


if __name__ == "__main__":
    main()
