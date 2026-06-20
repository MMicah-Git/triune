"""
subsample_v19.py — shrink yolo_dataset_v19_tiled into an upload-friendly subset
(no Google Drive needed; small enough for Colab files.upload()).

Keeps EVERY tile that contains a thin/rare class (so the miss classes stay fully
represented), then samples the remaining common/air-device-only tiles up to a
target. Preserves the original train/val split.
"""
from __future__ import annotations
import sys, glob, shutil, random
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
SRC = ROOT / 'yolo_dataset_v19_tiled'
OUT = ROOT / 'yolo_dataset_v19s_tiled'
TARGET_TILES = 10000
THIN_MAX = 700           # classes with fewer total boxes than this => keep all their tiles
random.seed(42)

CLASSES = [c for c in (SRC / 'classes.txt').read_text(encoding='utf-8').splitlines() if c.strip()]


def main():
    # split membership from train.txt/val.txt (entries like images/<stem>.png)
    split = {}
    for s in ('train', 'val'):
        for line in (SRC / f'{s}.txt').read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line:
                split[Path(line).stem] = s

    # box counts per class + per-tile class sets
    box_count = Counter()
    tile_classes = {}
    for f in glob.glob(str(SRC / 'labels' / '*.txt')):
        idxs = []
        for ln in Path(f).read_text(encoding='utf-8').splitlines():
            if ln.strip():
                idxs.append(int(ln.split()[0]))
        box_count.update(idxs)
        tile_classes[Path(f).stem] = set(idxs)

    thin = {i for i in range(len(CLASSES)) if box_count.get(i, 0) < THIN_MAX}
    print(f"thin classes (<{THIN_MAX} boxes): {sorted(CLASSES[i] for i in thin)}")

    must_keep = [t for t, cs in tile_classes.items() if cs & thin]
    rest = [t for t, cs in tile_classes.items() if not (cs & thin)]
    random.shuffle(rest)
    need = max(0, TARGET_TILES - len(must_keep))
    keep = set(must_keep) | set(rest[:need])
    print(f"keep {len(keep)} tiles ({len(must_keep)} thin-bearing + {min(need,len(rest))} sampled)")

    for d in ('images', 'labels'):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    train_list, val_list = [], []
    for stem in keep:
        img = SRC / 'images' / f'{stem}.png'
        lbl = SRC / 'labels' / f'{stem}.txt'
        if not img.exists():
            continue
        shutil.copy(img, OUT / 'images' / f'{stem}.png')
        shutil.copy(lbl, OUT / 'labels' / f'{stem}.txt')
        (val_list if split.get(stem) == 'val' else train_list).append(f'images/{stem}.png')

    (OUT / 'classes.txt').write_text('\n'.join(CLASSES) + '\n', encoding='utf-8')
    (OUT / 'train.txt').write_text('\n'.join(train_list) + '\n', encoding='utf-8')
    (OUT / 'val.txt').write_text('\n'.join(val_list) + '\n', encoding='utf-8')
    names = '\n'.join(f'  {i}: "{c}"' for i, c in enumerate(CLASSES))
    (OUT / 'data.yaml').write_text(
        f"path: {OUT.as_posix()}\ntrain: train.txt\nval: val.txt\n\nnames:\n{names}\n", encoding='utf-8')

    # report retained miss-class box counts
    kept_boxes = Counter()
    for stem in keep:
        for ln in (SRC / 'labels' / f'{stem}.txt').read_text(encoding='utf-8').splitlines():
            if ln.strip():
                kept_boxes[int(ln.split()[0])] += 1
    print(f"\n=== v19s: {len(train_list)} train + {len(val_list)} val tiles ===")
    for i, n in kept_boxes.most_common():
        print(f"  {n:>6}  {CLASSES[i]}")


if __name__ == '__main__':
    main()
