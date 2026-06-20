"""
tile_v17.py — tile the full-page yolo_dataset_v17 into 640px training tiles.

Full-page training fails (symbols become a few pixels at imgsz 640), so we slice
each page into 640px tiles with 160px overlap, reusing build_v11_dataset's tiler.
Output: yolo_dataset_v17_tiled/ (Colab-ready).
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cv2
from build_v11_dataset import tile_with_bboxes, TILE_SIZE, TILE_OVERLAP

SRC = ROOT / 'yolo_dataset_v17'
OUT = ROOT / 'yolo_dataset_v17_tiled'
CLASSES = (SRC / 'classes.txt').read_text(encoding='utf-8').split('\n')
CLASSES = [c for c in CLASSES if c.strip()]
CLASS_MAP = {c: i for i, c in enumerate(CLASSES)}


def main():
    # FLAT layout (matches train_v11._make_data_yaml_portable + ultralytics):
    # all tiles in images/, all labels in labels/; split membership via *.txt.
    for d in ('images', 'labels'):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    train_list, val_list = [], []
    n_tiles = 0
    for split in ('train', 'val'):
        for img_path in sorted((SRC / 'images' / split).glob('*.png')):
            lbl_path = SRC / 'labels' / split / (img_path.stem + '.txt')
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
                    ci, cx, cy, bw, bh = int(p[0]), *map(float, p[1:])
                    px, py, pw, ph = cx * w, cy * h, bw * w, bh * h
                    bboxes.append({'cls': CLASSES[ci],
                                   'x1': px - pw / 2, 'y1': py - ph / 2,
                                   'x2': px + pw / 2, 'y2': py + ph / 2})
            t = 0
            for tile, lines in tile_with_bboxes(img, bboxes, CLASS_MAP):
                stem = f'{img_path.stem}__t{t:03d}'
                cv2.imwrite(str(OUT / 'images' / f'{stem}.png'), tile)
                (OUT / 'labels' / f'{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                (train_list if split == 'train' else val_list).append(f'images/{stem}.png')
                t += 1; n_tiles += 1
            print(f"  [{split:5s}] {img_path.stem[:40]:40s} -> {t} tiles")
    (OUT / 'classes.txt').write_text('\n'.join(CLASSES) + '\n', encoding='utf-8')
    (OUT / 'train.txt').write_text('\n'.join(train_list) + '\n', encoding='utf-8')
    (OUT / 'val.txt').write_text('\n'.join(val_list) + '\n', encoding='utf-8')
    names = '\n'.join(f'  {i}: "{c}"' for i, c in enumerate(CLASSES))
    (OUT / 'data.yaml').write_text(
        f"path: {OUT.as_posix()}\ntrain: train.txt\nval: val.txt\n\nnames:\n{names}\n", encoding='utf-8')
    print(f"\nDONE: {n_tiles} tiles ({len(train_list)} train + {len(val_list)} val), {len(CLASSES)} classes -> {OUT.name}")


if __name__ == '__main__':
    main()
