"""Corpus-wide regression sweep for the schedule parser.

Why this exists
---------------
Verifying parser changes on one or two PDFs hides regressions. This runs
``parse_pdf_schedules`` on EVERY blueprint we have on hand and diffs the
extracted tag set against a baseline, so a change that helps one schedule and
silently breaks five others is caught immediately.

Corpus
------
Every ``saas/data/jobs/*/inputs/*.pdf`` (the real jobs already processed).

Baseline (what "before" means)
------------------------------
  --baseline snapshot.json : a snapshot saved by a previous sweep (preferred
                             for measuring a *new* change against a known point)
  (default)                : each job's stored ``*_variables.json`` — the
                             historical output from before the current edits.

Output
------
Per-job diff: LOST tags (in baseline, gone now = regression) and GAINED tags
(new now = win or new noise), plus a corpus summary. Optionally writes a fresh
snapshot (``--save snapshot.json``) to use as the baseline next time.

Usage
-----
    python -X utf8 schedule_regression_sweep.py                 # vs stored variables.json
    python -X utf8 schedule_regression_sweep.py --save base.json
    python -X utf8 schedule_regression_sweep.py --baseline base.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import schedule_parser as sp

REPO_ROOT = Path(__file__).resolve().parent
JOBS_DIR = REPO_ROOT / "saas" / "data" / "jobs"

# Skip absurdly large PDFs — pdfplumber chokes on multi-GB files (CLAUDE.md §6).
MAX_PDF_MB = 80


def _tags_from_variables(variables) -> set[str]:
    return {str(v.get("tag")).upper() for v in variables if v.get("tag")}


def _load_baseline_for_job(job_dir: Path, pdf: Path) -> set[str] | None:
    """The job's stored *_variables.json = output from before current edits."""
    vj = job_dir / f"{pdf.stem}_variables.json"
    if not vj.exists():
        # fall back to any *_variables.json in the job dir
        candidates = list(job_dir.glob("*_variables.json"))
        if not candidates:
            return None
        vj = candidates[0]
    try:
        data = json.loads(vj.read_text(encoding="utf-8"))
        return _tags_from_variables(data)
    except Exception:
        return None


def run_sweep(baseline_snapshot: dict | None) -> dict:
    pdfs = sorted(JOBS_DIR.glob("*/inputs/*.pdf"))
    results: dict[str, dict] = {}
    print(f"Found {len(pdfs)} blueprint(s) under {JOBS_DIR}\n")

    for pdf in pdfs:
        job_dir = pdf.parent.parent
        key = f"{job_dir.name}/{pdf.name}"
        size_mb = pdf.stat().st_size / 1e6
        if size_mb > MAX_PDF_MB:
            print(f"  SKIP (>{MAX_PDF_MB}MB)  {key}")
            results[key] = {"status": "skipped_large", "size_mb": round(size_mb, 1)}
            continue

        t0 = time.time()
        try:
            variables = sp.parse_pdf_schedules(str(pdf))[-1]
            current = sorted(_tags_from_variables(variables))
        except Exception as exc:  # noqa: BLE001 — report, don't abort the sweep
            print(f"  CRASH  {key}: {exc.__class__.__name__}: {exc}")
            results[key] = {"status": "crashed",
                            "error": "".join(traceback.format_exception_only(type(exc), exc)).strip()}
            continue

        # Baseline: snapshot file takes priority, else stored variables.json
        if baseline_snapshot is not None:
            base = baseline_snapshot.get(key, {}).get("tags")
            base = set(t.upper() for t in base) if base is not None else None
        else:
            base = _load_baseline_for_job(job_dir, pdf)

        entry = {"status": "ok", "tags": current,
                 "n": len(current), "runtime_s": round(time.time() - t0, 1)}
        if base is not None:
            cur_set = set(t.upper() for t in current)
            entry["lost"] = sorted(base - cur_set)     # regressions
            entry["gained"] = sorted(cur_set - base)   # wins or new noise
            entry["baseline_n"] = len(base)
        results[key] = entry

        flag = ""
        if base is not None and entry["lost"]:
            flag = "  <-- LOST TAGS"
        print(f"  {entry['n']:>3} tags  ({entry['runtime_s']:>4}s)  {key}{flag}")

    return results


def print_summary(results: dict) -> None:
    ok = {k: v for k, v in results.items() if v.get("status") == "ok"}
    crashed = [k for k, v in results.items() if v.get("status") == "crashed"]
    skipped = [k for k, v in results.items() if v.get("status") == "skipped_large"]
    with_base = {k: v for k, v in ok.items() if "lost" in v}

    regressions = {k: v for k, v in with_base.items() if v["lost"]}
    gains = {k: v for k, v in with_base.items() if v["gained"]}

    print("\n" + "=" * 70)
    print("SWEEP SUMMARY")
    print("=" * 70)
    print(f"  blueprints parsed OK : {len(ok)}")
    print(f"  crashed              : {len(crashed)}")
    print(f"  skipped (too large)  : {len(skipped)}")
    print(f"  compared to baseline : {len(with_base)}")
    print(f"  jobs with LOST tags  : {len(regressions)}  (potential regressions)")
    print(f"  jobs with GAINED tags: {len(gains)}")

    if regressions:
        print("\n  -- REGRESSIONS (tags present before, gone now) --")
        for k, v in regressions.items():
            print(f"    {k}\n        lost: {v['lost']}")
    else:
        print("\n  No regressions: every tag in the baseline is still extracted.")

    if gains:
        print("\n  -- GAINS (new tags now extracted) --")
        for k, v in gains.items():
            print(f"    {k}\n        gained: {v['gained']}")

    if crashed:
        print("\n  -- CRASHED --")
        for k in crashed:
            print(f"    {k}: {results[k]['error']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", type=Path, help="snapshot.json from a previous sweep")
    ap.add_argument("--save", type=Path, help="write current results as a snapshot")
    args = ap.parse_args()

    baseline_snapshot = None
    if args.baseline:
        baseline_snapshot = json.loads(args.baseline.read_text(encoding="utf-8"))
        print(f"Baseline: snapshot {args.baseline}")
    else:
        print("Baseline: each job's stored *_variables.json (pre-change output)")

    results = run_sweep(baseline_snapshot)
    print_summary(results)

    if args.save:
        args.save.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nSnapshot written to {args.save}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
