"""
gate_sweep.py — find v19's best confidence threshold on held-out (no retrain).

v19 is high-precision / low-recall at conf 0.4. Lowering the threshold trades
precision for recall. We run detection ONCE at a permissive floor, then compute
air-device-aliased recall/precision/F1 at several thresholds to find the best F1,
and compare to v10's bar (F1 0.889).
"""
import sys, json
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
from benchmark_v10_vs_v11 import render_page_image, tiled_detect, gather_truth
from bluebeam_to_yolo import DEFAULT_DPI
from ultralytics import YOLO

V10_F1 = 0.889
THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]


def alias(c):
    c = (c or '').upper()
    return 'AIR DEVICE' if c.startswith('AD-') else c


def main():
    cand = sys.argv[1] if len(sys.argv) > 1 else 'models/hvac_yolov8s_v19.pt'
    m = json.load(open('benchmark_manifest.json'))
    held = [p for p in m['projects'] if p['split'] == 'held-out']
    model = YOLO(cand)
    # per-project: truth Counter (aliased) + list of (aliased_class, conf)
    proj = []
    for it in held:
        tp = gather_truth(Path(it['truth']), DEFAULT_DPI)
        truth = Counter(); preds = []
        for pno in sorted(tp.keys()):
            img, _, _ = render_page_image(Path(it['pdf']), pno, DEFAULT_DPI)
            for t in tp[pno]:
                truth[alias(t['class'])] += 1
            for d in tiled_detect(model, img, conf=0.05):
                preds.append((alias(d['norm_class']), float(d.get('conf', 0))))
        proj.append((truth, preds))

    print(f"candidate: {cand}   (v10 bar F1={V10_F1})\n")
    print(f"  {'conf':>5} {'recall':>7} {'prec':>7} {'F1':>7}")
    best = (0, None)
    for t in THRESHOLDS:
        tp_sum = fp_sum = fn_sum = match_sum = truth_sum = pred_sum = 0
        for truth, preds in proj:
            pred = Counter(c for c, cf in preds if cf >= t)
            classes = set(truth) | set(pred)
            match = sum(min(truth.get(c, 0), pred.get(c, 0)) for c in classes)
            match_sum += match; truth_sum += sum(truth.values()); pred_sum += sum(pred.values())
        r = match_sum / truth_sum if truth_sum else 0
        p = match_sum / pred_sum if pred_sum else 0
        f1 = 2 * p * r / (p + r) if (p + r) else 0
        flag = '  <-- beats v10' if f1 > V10_F1 else ''
        print(f"  {t:>5.2f} {r:>7.3f} {p:>7.3f} {f1:>7.3f}{flag}")
        if f1 > best[0]:
            best = (f1, t)
    print(f"\nbest v19 F1 = {best[0]:.3f} at conf {best[1]}")
    if best[0] > V10_F1:
        print(f"==> PROMOTE v19 at conf {best[1]} (F1 {best[0]:.3f} > v10 {V10_F1})")
    else:
        print(f"==> KEEP v10 (best v19 F1 {best[0]:.3f} <= {V10_F1}). Needs the bigger dataset retrain.")


if __name__ == '__main__':
    main()
