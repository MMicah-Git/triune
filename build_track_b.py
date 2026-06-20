"""
build_track_b.py — mine the HYDRATED OneDrive marked PDFs into the Track B dataset.

Run AFTER hydrate_track_b.ps1 has downloaded marked Takeoff PDFs locally.
Builds a Colab-ready, 640px-tiled dataset (flat layout) with the same collapsed
air-device taxonomy as Track A, EXCLUDING the 12 benchmark projects so the gate
stays valid (no held-out leakage). Reports how many MISS-CLASS examples we gained.

Usage:
    python build_track_b.py                 # mine all hydrated marked PDFs
    python build_track_b.py --limit 300     # cap (for a first pass)
"""
from __future__ import annotations
import sys, argparse, glob, json, re, os, stat
from pathlib import Path
from collections import defaultdict, Counter

_OFFLINE = getattr(stat, 'FILE_ATTRIBUTE_OFFLINE', 0x1000)
_RECALL = 0x00400000  # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


def is_cloud_only(path: str) -> bool:
    """True if the file is a OneDrive placeholder not on local disk. Checking the
    attribute (instead of opening) avoids triggering an on-demand download."""
    try:
        attrs = os.stat(path).st_file_attributes
        return bool(attrs & (_OFFLINE | _RECALL))
    except Exception:
        return True

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'saas' / 'backend'))
import fitz, cv2
from bluebeam_to_yolo import extract_annotations, DEFAULT_DPI
from v10_class_map import V10_CLASSES, map_subject
from build_v11_dataset import tile_with_bboxes

OD = Path.home() / 'OneDrive - Triune Solutions LLC'
OUT = ROOT / 'yolo_dataset_v18_tiled'           # Track B (mined corpus); override with --out
NEW_CLASSES = ['AIR DEVICE'] + [c for c in V10_CLASSES if not c.startswith('AD-')]
NEW_INDEX = {c: i for i, c in enumerate(NEW_CLASSES)}
MISS = {'PACKAGED ROOFTOP UNIT', 'FIRE SMOKE DAMPER', 'HOOD', 'GAS UNIT HEATER',
        'VENT CAP', 'LOUVER'}


def benchmark_keywords():
    m = json.load(open('benchmark_manifest.json'))
    # exclude any PDF whose name shares a distinctive token with a benchmark project
    kws = set()
    for p in m['projects']:
        for tok in re.findall(r'[A-Za-z]{4,}', p['name']):
            kws.add(tok.lower())
    return kws


def collapse(c): return 'AIR DEVICE' if c.startswith('AD-') else c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', default=None, help='output dataset dir (default yolo_dataset_v18_tiled)')
    args = ap.parse_args()
    global OUT
    if args.out:
        OUT = ROOT / args.out
    for d in ('images', 'labels'):
        (OUT / d).mkdir(parents=True, exist_ok=True)

    bench = benchmark_keywords()
    pdfs = sorted(glob.glob(str(OD / '**' / 'Takeoff_*.pdf'), recursive=True))
    cls_count = Counter(); used = 0; skipped_offline = 0; skipped_bench = 0; tiles = 0
    train_list, val_list = [], []
    for n, pdf in enumerate(pdfs):
        if args.limit and used >= args.limit:
            break
        name = Path(pdf).stem.lower()
        if any(k in name for k in bench):           # leakage guard
            skipped_bench += 1; continue
        if is_cloud_only(pdf):                        # NEVER open a placeholder (would trigger download)
            skipped_offline += 1; continue
        try:
            doc = fitz.open(pdf)
        except Exception:
            skipped_offline += 1; continue
        try:
            by_page = defaultdict(list)
            for a in extract_annotations(Path(pdf)):
                v10 = map_subject(a.get('subclass') or a.get('class'))
                if not v10:
                    continue
                nc = collapse(v10)
                if nc in NEW_INDEX:
                    by_page[a['page']].append((NEW_INDEX[nc], a)); cls_count[nc] += 1
            if not by_page:
                continue
            slug = re.sub(r'[^a-z0-9]+', '-', name)[:40]
            split = 'val' if used % 8 == 0 else 'train'   # ~12% val, by project
            for pno in sorted(by_page):
                page = doc[pno - 1]; pw, ph = page.rect.width, page.rect.height
                pix = page.get_pixmap(dpi=DEFAULT_DPI, annots=False)
                import numpy as np
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n >= 3 else img
                bboxes = []
                for cid, a in by_page[pno]:
                    x0, y0, x1, y1 = a['rect_pdf']; sx, sy = pix.width / pw, pix.height / ph
                    bboxes.append({'cls': NEW_CLASSES[cid], 'x1': x0*sx, 'y1': y0*sy, 'x2': x1*sx, 'y2': y1*sy})
                for ti, (tile, lines) in enumerate(tile_with_bboxes(img, bboxes, NEW_INDEX)):
                    stem = f'{slug}__p{pno:03d}__t{ti:03d}'
                    cv2.imwrite(str(OUT / 'images' / f'{stem}.png'), tile)
                    (OUT / 'labels' / f'{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                    (val_list if split == 'val' else train_list).append(f'images/{stem}.png')
                    tiles += 1
            used += 1
            if used % 25 == 0:
                print(f"  mined {used} projects, {tiles} tiles, "
                      f"miss-class so far: {{ {', '.join(f'{k}={cls_count[k]}' for k in MISS if cls_count[k])} }}")
        finally:
            doc.close()

    (OUT / 'classes.txt').write_text('\n'.join(NEW_CLASSES) + '\n', encoding='utf-8')
    (OUT / 'train.txt').write_text('\n'.join(train_list) + '\n', encoding='utf-8')
    (OUT / 'val.txt').write_text('\n'.join(val_list) + '\n', encoding='utf-8')
    names = '\n'.join(f'  {i}: "{c}"' for i, c in enumerate(NEW_CLASSES))
    (OUT / 'data.yaml').write_text(
        f"path: {OUT.as_posix()}\ntrain: train.txt\nval: val.txt\n\nnames:\n{names}\n", encoding='utf-8')

    print(f"\n=== Track B: mined {used} projects → {tiles} tiles "
          f"({len(train_list)} train + {len(val_list)} val) ===")
    print(f"  skipped: {skipped_offline} not-hydrated, {skipped_bench} benchmark (leakage guard)")
    print("class distribution:")
    for c, n in cls_count.most_common():
        flag = '  ← was a MISS class' if c in MISS else ''
        print(f"  {n:>6}  {c}{flag}")
    print(f"\nNext: merge with yolo_dataset_v17_tiled if desired, then make_colab_bundle + Colab train + gate_track_a.py")


if __name__ == '__main__':
    main()
