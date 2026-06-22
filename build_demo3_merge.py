"""
build_demo3_merge.py — collapse the mined demo-3 projects (PNC, Atascocita,
CityVet) to the v19s taxonomy, tile to 640px, and MERGE into
yolo_dataset_v19s_tiled so the retrain learns those exact projects.

  demo3 (33-class, flat full pages)  --collapse AD-* -> AIR DEVICE-->  v19s 26-class
                                     --tile 640px-->  appended to v19s train split

Oversamples the demo-3 tiles (OVERSAMPLE x) so ~6 pages aren't drowned by the
10k existing tiles. Run:  python build_demo3_merge.py
"""
from __future__ import annotations
import sys, shutil
from pathlib import Path
import cv2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from build_v11_dataset import tile_with_bboxes  # noqa: E402

SRC = ROOT / 'yolo_dataset_demo3'
DST = ROOT / 'yolo_dataset_v19s_tiled'
OVERSAMPLE = 4            # repeat each demo-3 tile this many times in train
VAL_FRACTION_PROJECT = 'pnc-medical'  # hold one project's tiles for val sanity

# v19s target taxonomy (name -> index), read from its data.yaml
import re
_names = {}
for ln in (DST / 'data.yaml').read_text(encoding='utf-8').splitlines():
    m = re.match(r'\s*(\d+):\s*"(.+)"', ln)
    if m:
        _names[m.group(2)] = int(m.group(1))
NAME_TO_IDX = _names
IDX_TO_NAME = {i: n for n, i in NAME_TO_IDX.items()}

demo_classes = [c for c in (SRC / 'classes.txt').read_text(encoding='utf-8').splitlines() if c.strip()]


def collapse(name: str) -> str:
    """demo-3 (33-class) name -> v19s (26-class) name."""
    n = name.strip().upper()
    if n.startswith('AD') or 'LINEAR PLENUM' in n or 'SLOT DIFFUSER' in n:
        return 'AIR DEVICE'
    return name.strip()


def main():
    assert DST.exists(), f'missing {DST}'
    (DST / 'images').mkdir(exist_ok=True)
    (DST / 'labels').mkdir(exist_ok=True)
    train_add, val_add = [], []
    n_tiles = 0
    skipped_cls = set()
    for img_path in sorted((SRC / 'images').glob('*.png')):
        lbl_path = SRC / 'labels' / (img_path.stem + '.txt')
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        h, w = img.shape[:2]
        bboxes = []
        if lbl_path.exists():
            for line in lbl_path.read_text(encoding='utf-8').splitlines():
                p = line.split()
                if len(p) != 5:
                    continue
                ci = int(p[0]); cx, cy, bw, bh = map(float, p[1:])
                name = collapse(demo_classes[ci]) if ci < len(demo_classes) else None
                if name not in NAME_TO_IDX:
                    skipped_cls.add(demo_classes[ci] if ci < len(demo_classes) else str(ci))
                    continue
                px, py, pw, ph = cx * w, cy * h, bw * w, bh * h
                bboxes.append({'cls': name, 'x1': px - pw / 2, 'y1': py - ph / 2,
                               'x2': px + pw / 2, 'y2': py + ph / 2})
        is_val = img_path.stem.startswith(VAL_FRACTION_PROJECT)
        reps = 1 if is_val else OVERSAMPLE
        t = 0
        for tile, lines in tile_with_bboxes(img, bboxes, NAME_TO_IDX):
            if not lines:           # skip empty tiles (no equipment)
                continue
            for r in range(reps):
                stem = f'demo3__{img_path.stem}__t{t:03d}' + (f'__r{r}' if r else '')
                cv2.imwrite(str(DST / 'images' / f'{stem}.png'), tile)
                (DST / 'labels' / f'{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                (val_add if is_val else train_add).append(f'images/{stem}.png')
                n_tiles += 1
            t += 1
    # append to the split lists
    tr = DST / 'train.txt'; va = DST / 'val.txt'
    tr.write_text(tr.read_text(encoding='utf-8').rstrip('\n') + '\n' + '\n'.join(train_add) + '\n', encoding='utf-8')
    va.write_text(va.read_text(encoding='utf-8').rstrip('\n') + '\n' + '\n'.join(val_add) + '\n', encoding='utf-8')
    print(f'Added {n_tiles} demo-3 tiles ({len(train_add)} train + {len(val_add)} val) to {DST.name}')
    print(f'  oversample={OVERSAMPLE}, val project={VAL_FRACTION_PROJECT}')
    if skipped_cls:
        print(f'  skipped unmapped classes: {sorted(skipped_cls)}')


if __name__ == '__main__':
    main()
