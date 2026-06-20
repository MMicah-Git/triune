"""
gate_track_a.py — the Track A benchmark gate (air-device-aliasing aware).

A Track A model uses the COLLAPSED taxonomy (one "AIR DEVICE" class), so it can't
be scored against subtype-labeled truth with the raw count gate — it would score 0
even if perfect. This gate collapses air-device subtypes on BOTH sides (truth and
both models), scores on the HELD-OUT projects only, and gives a promote/keep verdict.

Usage:
    python gate_track_a.py models/hvac_yolov8s_v17.pt
"""
from __future__ import annotations
import sys, json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
from benchmark_v10_vs_v11 import render_page_image, tiled_detect, gather_truth, DEFAULT_CONF
from bluebeam_to_yolo import DEFAULT_DPI
from ultralytics import YOLO


def alias(c: str) -> str:
    c = (c or '').upper()
    return 'AIR DEVICE' if c.startswith('AD-') else c


def prf(truth: Counter, pred: Counter):
    classes = set(truth) | set(pred)
    match = sum(min(truth.get(c, 0), pred.get(c, 0)) for c in classes)
    t, p = sum(truth.values()), sum(pred.values())
    r = match / t if t else 0.0
    pr = match / p if p else 0.0
    f1 = 2 * pr * r / (pr + r) if (pr + r) else 0.0
    return r, pr, f1


def score(model, projects):
    rt, pd = Counter(), Counter()
    for it in projects:
        tp = gather_truth(Path(it['truth']), DEFAULT_DPI)
        for pno in sorted(tp.keys()):
            img, _, _ = render_page_image(Path(it['pdf']), pno, DEFAULT_DPI)
            for t in tp[pno]:
                rt[alias(t['class'])] += 1
            for d in tiled_detect(model, img, conf=DEFAULT_CONF):
                pd[alias(d['norm_class'])] += 1
    return prf(rt, pd)


def main():
    cand_path = sys.argv[1] if len(sys.argv) > 1 else 'models/hvac_yolov8s_v17.pt'
    m = json.load(open('benchmark_manifest.json'))
    held = [p for p in m['projects'] if p['split'] == 'held-out']
    print(f"Track A gate (air-device aliased) on {len(held)} HELD-OUT projects")
    print(f"  candidate: {cand_path}\n")
    v10 = YOLO('models/hvac_yolov8s_v10.pt')
    cand = YOLO(cand_path)
    r10, p10, f10 = score(v10, held)
    rc, pc, fc = score(cand, held)
    print(f"  {'model':10s} {'recall':>7} {'prec':>7} {'F1':>7}")
    print(f"  {'v10':10s} {r10:>7.3f} {p10:>7.3f} {f10:>7.3f}")
    print(f"  {'candidate':10s} {rc:>7.3f} {pc:>7.3f} {fc:>7.3f}")
    print()
    if fc > f10 + 0.01:
        print(f"  ✅ PROMOTE: candidate beats v10 on held-out F1 (+{fc-f10:.3f}). Deploy it.")
    else:
        print(f"  ❌ KEEP v10: candidate F1 {fc:.3f} ≤ v10 {f10:.3f}. Do NOT ship.")


if __name__ == '__main__':
    main()
