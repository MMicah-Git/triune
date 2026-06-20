"""
curate_training_data.py — Phase 1 of the retrain flywheel.

Inventories the labeled data we can legitimately train on and exposes the gaps —
especially for the classes v10 genuinely MISSES (ROOFTOP UNIT, FIRE SMOKE DAMPER,
RAIN CAP, RELIEF HOOD, GAS UNIT HEATER, LINEAR SLOT).

Sources:
  - Bluebeam-marked PDFs in benchmark_manifest.json (split-aware: HELD-OUT stays
    OUT of training so the gate remains valid).
  - (reported) UI correction jobs in saas/data/corrections/.

Output: per-class box counts for TRAINABLE (in-sample) vs HELD-OUT, with the
miss-classes flagged. This tells us what to source more of before building v17.
"""
from __future__ import annotations
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
from bluebeam_to_yolo import extract_annotations

MISS = {'ROOFTOP UNIT', 'PACKAGED ROOFTOP UNIT', 'FIRE SMOKE DAMPER', 'RAIN CAP',
        'RELIEF HOOD', 'GAS UNIT HEATER', 'AD-LINEAR SLOT DIFFUSER'}


def main():
    m = json.load(open('benchmark_manifest.json'))
    trainable, heldout = Counter(), Counter()
    proj_rows = []
    for p in m['projects']:
        truth = Path(p['truth'])
        if not truth.exists():
            proj_rows.append((p['name'], p['split'], 'MISSING', 0)); continue
        c = Counter()
        for a in extract_annotations(truth):
            c[a['class']] += 1
        tgt = heldout if p['split'] == 'held-out' else trainable
        tgt.update(c)
        proj_rows.append((p['name'], p['split'], 'ok', sum(c.values())))

    print("=== per-project label counts ===")
    for name, split, status, n in proj_rows:
        print(f"  [{split:9s}] {n:>5} boxes  {status:8s}  {name[:46]}")

    print(f"\n=== TRAINABLE (in-sample) per-class — {sum(trainable.values())} boxes ===")
    for cls, n in trainable.most_common():
        flag = '  ← MISS-CLASS' if cls.upper() in MISS else ''
        print(f"  {n:>5}  {cls}{flag}")

    print(f"\n=== MISS-CLASS coverage (trainable vs held-out) ===")
    for cls in sorted(MISS):
        t, h = trainable.get(cls, 0), heldout.get(cls, 0)
        verdict = 'THIN — need more' if t < 30 else 'ok'
        print(f"  {cls:28s} trainable={t:>4}  held-out={h:>3}   {verdict}")

    print(f"\n  (held-out total {sum(heldout.values())} boxes — EXCLUDED from training)")


if __name__ == '__main__':
    main()
