"""
verify_dataset.py

Draw the YOLO labels back onto each rendered page image so you can
visually confirm boxes land on real symbols before training.

Usage:
    python verify_dataset.py [--dataset yolo_dataset_v11] [--shrink 2]

Outputs:
    <dataset>/verify/<image stem>.jpg     half-resolution overlay (boxes + class names)
"""

import argparse
from pathlib import Path

import cv2


# Distinct colors cycling through classes
COLORS = [
    (0, 200, 0), (0, 165, 255), (255, 0, 255), (0, 255, 255),
    (200, 0, 0), (0, 0, 200), (255, 200, 0), (128, 0, 128),
    (0, 128, 255), (255, 128, 0), (64, 255, 64), (255, 64, 64),
    (64, 64, 255), (200, 200, 0), (200, 0, 200), (0, 200, 200),
    (128, 128, 0), (0, 128, 128), (128, 0, 0), (0, 128, 0),
    (0, 0, 128), (128, 128, 128), (255, 255, 64), (64, 255, 255),
    (255, 64, 255),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default='yolo_dataset_v11')
    ap.add_argument('--shrink', type=int, default=2,
                    help='downscale factor on the output image (default: 2)')
    ap.add_argument('--jpeg-quality', type=int, default=85)
    args = ap.parse_args()

    root = Path(args.dataset)
    images_dir = root / 'images'
    labels_dir = root / 'labels'
    classes_path = root / 'classes.txt'
    verify_dir = root / 'verify'
    verify_dir.mkdir(parents=True, exist_ok=True)

    classes = [
        ln.strip() for ln in classes_path.read_text(encoding='utf-8').splitlines()
        if ln.strip()
    ]

    img_files = sorted(images_dir.glob('*.png'))
    print(f'Verifying {len(img_files)} images...')

    for img_path in img_files:
        lbl_path = labels_dir / (img_path.stem + '.txt')
        if not lbl_path.exists():
            print(f'  skip (no labels): {img_path.name}')
            continue
        img = cv2.imread(str(img_path))
        if img is None:
            print(f'  skip (unreadable): {img_path.name}')
            continue
        h, w = img.shape[:2]

        n_boxes = 0
        for line in lbl_path.read_text(encoding='utf-8').strip().splitlines():
            if not line.strip():
                continue
            parts = line.split()
            cid = int(parts[0])
            cx, cy, bw, bh = map(float, parts[1:5])
            x0 = int((cx - bw / 2) * w)
            y0 = int((cy - bh / 2) * h)
            x1 = int((cx + bw / 2) * w)
            y1 = int((cy + bh / 2) * h)
            color = COLORS[cid % len(COLORS)]
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 3)
            label = classes[cid] if 0 <= cid < len(classes) else f'cls{cid}'
            cv2.putText(img, label[:25], (x0, max(15, y0 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
            n_boxes += 1

        # Add a header strip with image stem + box count
        header = f'{img_path.stem}  ({n_boxes} boxes)'
        cv2.rectangle(img, (0, 0), (w, 60), (255, 255, 255), -1)
        cv2.putText(img, header, (15, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)

        if args.shrink > 1:
            img = cv2.resize(img, (w // args.shrink, h // args.shrink),
                             interpolation=cv2.INTER_AREA)

        out_path = verify_dir / (img_path.stem + '.jpg')
        cv2.imwrite(str(out_path), img,
                    [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
        print(f'  {img_path.name} -> {out_path.name} ({n_boxes} boxes)')

    print(f'\nDone. Overlays in: {verify_dir.resolve()}')


if __name__ == '__main__':
    main()
