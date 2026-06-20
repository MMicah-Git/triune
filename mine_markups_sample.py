"""
Phase 1b — sample-mine the OneDrive marked-takeoff corpus to confirm it can
supply the miss-class training examples we lack (RTU, FIRE SMOKE DAMPER, etc.).

Reads Bluebeam annotation layers only (fast — no rendering). Samples every Kth
Takeoff_*.pdf so a quick pass estimates the full corpus's class distribution.
"""
from __future__ import annotations
import sys, io, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
from bluebeam_to_yolo import extract_annotations

OD = Path.home() / 'OneDrive - Triune Solutions LLC'
MISS = {'ROOFTOP UNIT', 'PACKAGED ROOFTOP UNIT', 'FIRE SMOKE DAMPER', 'RAIN CAP',
        'RELIEF HOOD', 'GAS UNIT HEATER', 'AD-LINEAR SLOT DIFFUSER'}
SAMPLE_EVERY = 9     # ~1122/9 ≈ 125 PDFs

def main():
    allpdfs = sorted(glob.glob(str(OD / '**' / 'Takeoff_*.pdf'), recursive=True))
    sample = allpdfs[::SAMPLE_EVERY]
    print(f"corpus={len(allpdfs)} marked PDFs · sampling {len(sample)} (every {SAMPLE_EVERY}th)\n")
    cls = Counter(); ok = 0; boxes = 0; bad = 0
    for i, p in enumerate(sample):
        try:
            c = Counter(a['class'] for a in extract_annotations(Path(p)))
            if c:
                cls.update(c); ok += 1; boxes += sum(c.values())
        except Exception:
            bad += 1
        if (i + 1) % 25 == 0:
            print(f"  ...{i+1}/{len(sample)} scanned, {boxes} boxes so far")
    print(f"\nscanned {len(sample)} PDFs: {ok} had annotations, {bad} errored, {boxes} total boxes")
    scale = len(allpdfs) / max(1, len(sample))
    print(f"\n=== MISS-CLASS examples found (sample → projected full corpus ×{scale:.0f}) ===")
    for m in sorted(MISS):
        n = cls.get(m, 0)
        print(f"  {m:28s} sample={n:>4}   projected≈{int(n*scale):>5}")
    print(f"\n=== top 25 classes in sample ===")
    for c, n in cls.most_common(25):
        print(f"  {n:>5}  {c}")

if __name__ == '__main__':
    main()
