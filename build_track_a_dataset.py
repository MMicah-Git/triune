"""
build_track_a_dataset.py — Track A dataset for the v17 retrain.

Design (from the Part 2 findings):
  - Subtype now comes from the TAG/schedule (Change #1 + PRODUCT-from-schedule), so the
    detector only needs to FIND air devices, not classify their subtype. Collapse all
    AD-* subclasses → one "AIR DEVICE" class (the config the rescore proved hits ~84%
    object recall) — removes the inter-subclass confusion that diluted detection.
  - Keep the distinct equipment classes (FAN, ROOFTOP UNIT, FIRE SMOKE DAMPER, ...).
  - IN-SAMPLE ONLY: held-out projects (Barings LA, St. Francis) are EXCLUDED so the
    benchmark gate stays valid.

Output: yolo_dataset_v17/ (clean page PNGs + YOLO labels + data.yaml + train/val split).
Training hyperparameters are set in the bundle step (lr0=0.01, mosaic=0.5 — the v14 fix).
"""
from __future__ import annotations
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
import fitz
from bluebeam_to_yolo import extract_annotations, yolo_label_line, DEFAULT_DPI
from v10_class_map import V10_CLASSES, map_subject

OUT = ROOT / 'yolo_dataset_v17'
# Collapsed taxonomy: AIR DEVICE + the non-AD classes (preserve order).
NEW_CLASSES = ['AIR DEVICE'] + [c for c in V10_CLASSES if not c.startswith('AD-')]
NEW_INDEX = {c: i for i, c in enumerate(NEW_CLASSES)}
VAL_PROJECTS = {'Citadel Irvine', 'SPE Sinton - Quote'}   # held FROM train, used as val


def collapse(v10cls: str) -> str:
    return 'AIR DEVICE' if v10cls.startswith('AD-') else v10cls


def main():
    m = json.load(open('benchmark_manifest.json'))
    insample = [p for p in m['projects'] if p['split'] != 'held-out']
    print(f"Building Track A dataset from {len(insample)} in-sample projects "
          f"(held-out excluded). Classes: {len(NEW_CLASSES)}\n")
    for d in ('images/train', 'images/val', 'labels/train', 'labels/val'):
        (OUT / d).mkdir(parents=True, exist_ok=True)

    cls_count = Counter(); train_imgs = []; val_imgs = []; dropped = Counter()
    for p in insample:
        truth = Path(p['truth'])
        if not truth.exists():
            print(f"  SKIP (missing): {p['name']}"); continue
        split = 'val' if p['name'] in VAL_PROJECTS else 'train'
        slug = p['slug']
        by_page = defaultdict(list)
        for a in extract_annotations(truth):
            v10 = map_subject(a.get('subclass') or a.get('class'))
            if not v10 or v10 not in NEW_INDEX and not v10.startswith('AD-'):
                dropped[a.get('subclass') or '?'] += 1; continue
            nc = collapse(v10)
            if nc not in NEW_INDEX:
                dropped[a.get('subclass') or '?'] += 1; continue
            by_page[a['page']].append((NEW_INDEX[nc], a)); cls_count[nc] += 1
        if not by_page:
            print(f"  {p['name'][:40]:40s} — 0 boxes"); continue
        doc = fitz.open(str(truth)); nb = 0
        try:
            for pno in sorted(by_page):
                page = doc[pno - 1]; pw, ph = page.rect.width, page.rect.height
                stem = f'{slug}__p{pno:03d}'
                pix = page.get_pixmap(dpi=DEFAULT_DPI, annots=False)
                pix.save(str(OUT / f'images/{split}/{stem}.png'))
                lines = [yolo_label_line(cid, a['rect_pdf'], (pw, ph)) for cid, a in by_page[pno]]
                (OUT / f'labels/{split}/{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                (val_imgs if split == 'val' else train_imgs).append(f'images/{split}/{stem}.png')
                nb += len(lines)
        finally:
            doc.close()
        print(f"  [{split:5s}] {p['name'][:40]:40s} — {len(by_page)} pages, {nb} boxes")

    (OUT / 'classes.txt').write_text('\n'.join(NEW_CLASSES) + '\n', encoding='utf-8')
    (OUT / 'train.txt').write_text('\n'.join(train_imgs) + '\n', encoding='utf-8')
    (OUT / 'val.txt').write_text('\n'.join(val_imgs) + '\n', encoding='utf-8')
    names = '\n'.join(f'  {i}: "{c}"' for i, c in enumerate(NEW_CLASSES))
    (OUT / 'data.yaml').write_text(
        f"path: {OUT.as_posix()}\ntrain: train.txt\nval: val.txt\n\nnames:\n{names}\n",
        encoding='utf-8')

    print(f"\n=== built {OUT.name}: {len(train_imgs)} train + {len(val_imgs)} val images ===")
    print("class distribution:")
    for c, n in cls_count.most_common():
        print(f"  {n:>5}  {c}")
    if dropped:
        print(f"dropped (unmapped subjects): {sum(dropped.values())} — {dict(dropped.most_common(5))}")


if __name__ == '__main__':
    main()
