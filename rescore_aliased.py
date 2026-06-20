"""
rescore_aliased.py — Part 2 / Change #1 prototype.

Re-scores the detection benchmark with the air-device SUBTYPES collapsed into a
single "AIR DEVICE" class (on both truth and predictions). This isolates
OBJECT-LEVEL detection recall: does the model FIND the diffusers/grilles,
regardless of whether it labels the T-BAR/SURF/LINEAR subtype correctly?

Hypothesis (from per_class.csv): per-subtype recall is ~1-30%, but the model
detects ~74% of air-device OBJECTS — it just mislabels the subtype. If true, the
fix is to detect a coarse air-device class and derive subtype from the tag/
schedule, NOT to make the vision model guess the subtype.

v10 only (half the time of the v10-vs-v11 suite).
"""
from __future__ import annotations
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'saas' / 'backend'))

from benchmark_v10_vs_v11 import render_page_image, tiled_detect, gather_truth, DEFAULT_CONF
from bluebeam_to_yolo import DEFAULT_DPI
from ultralytics import YOLO


def alias(cls: str) -> str:
    """Collapse air-device subtypes to one class; pass everything else through."""
    c = (cls or '').upper()
    if c.startswith('AD-') or c in ('AD-GRD',):
        return 'AIR DEVICE'
    return c


def prf(truth: Counter, pred: Counter):
    classes = set(truth) | set(pred)
    match = sum(min(truth.get(c, 0), pred.get(c, 0)) for c in classes)
    t, p = sum(truth.values()), sum(pred.values())
    return match, t, p, (match / t if t else 0.0), (match / p if p else 0.0)


def main():
    manifest = json.load(open('benchmark_manifest.json'))
    model = YOLO('models/hvac_yolov8s_v10.pt')
    print(f"Re-score with air-device aliasing — v10, {len(manifest['projects'])} projects\n")

    agg = {'held-out': [Counter(), Counter(), Counter(), Counter()],   # raw_t, raw_p, ali_t, ali_p
           'in-sample': [Counter(), Counter(), Counter(), Counter()]}
    print(f"{'project':42s} {'split':9s} {'raw_R':>6} {'alias_R':>8} {'gain':>6}")
    for it in manifest['projects']:
        pdf, truth = Path(it['pdf']), Path(it['truth'])
        if not pdf.exists():
            print(f"{it['name'][:42]:42s} (missing pdf)"); continue
        tp = gather_truth(truth, DEFAULT_DPI)
        pages = sorted(tp.keys())
        if not pages:
            print(f"{it['name'][:42]:42s} (no truth)"); continue
        rt, rp, at, ap = Counter(), Counter(), Counter(), Counter()
        for pno in pages:
            img, _, _ = render_page_image(pdf, pno, DEFAULT_DPI)
            dets = tiled_detect(model, img, conf=DEFAULT_CONF)
            for t in tp[pno]:
                rt[t['class']] += 1; at[alias(t['class'])] += 1
            for d in dets:
                rp[d['norm_class']] += 1; ap[alias(d['norm_class'])] += 1
        _, _, _, rR, _ = prf(rt, rp)
        _, _, _, aR, _ = prf(at, ap)
        sp = it['split']
        agg[sp][0].update(rt); agg[sp][1].update(rp); agg[sp][2].update(at); agg[sp][3].update(ap)
        print(f"{it['name'][:42]:42s} {sp:9s} {rR:>6.0%} {aR:>8.0%} {aR-rR:>+6.0%}")

    print("\n== AGGREGATE ==")
    for sp in ('held-out', 'in-sample'):
        rt, rp, at, ap = agg[sp]
        _, tt, _, rR, rP = prf(rt, rp)
        _, _, _, aR, aP = prf(at, ap)
        print(f"  {sp:9s} ({tt} truth): raw recall={rR:.0%} (P={rP:.0%})  →  "
              f"aliased recall={aR:.0%} (P={aP:.0%})")
        # air-device object recall specifically
        adt, adp = at.get('AIR DEVICE', 0), ap.get('AIR DEVICE', 0)
        if adt:
            print(f"            air devices: truth={adt} detected={adp} "
                  f"object-recall={min(adt,adp)/adt:.0%}")


if __name__ == '__main__':
    main()
