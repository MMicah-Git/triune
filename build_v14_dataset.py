"""
build_v14_dataset.py — extract a TILED YOLO dataset from data hvac for
fine-tuning models/hvac_yolov8s_v10.pt.

v10 was trained on 640x640 TILES at 200 DPI (not full pages), and inference
tiles too. Full-page training fails: the symbols become a few pixels and the
model (incl. v10 itself) scores 0 mAP. So we replicate v10's tiling exactly,
reusing build_v11_dataset.tile_with_bboxes.

Per page: render the marked PDF at 200 DPI WITHOUT annotations (clean image),
map each Bluebeam polygon -> v10 class -> pixel bbox, then tile. Labels use
v10's 33-class ids so the fine-tune head aligns.

Run:  python -u build_v14_dataset.py
"""
import sys, glob, os, json, shutil
from pathlib import Path
import fitz
import numpy as np
import cv2

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import bluebeam_to_yolo as b2y  # noqa: E402
from v10_class_map import map_subject, V10_CLASSES  # noqa: E402
from build_v11_dataset import tile_with_bboxes, TILE_SIZE, TILE_OVERLAP  # noqa: E402

DATA_ROOT = Path(r'C:\Users\TriuneTakeoff\Downloads\data hvac')
OUT = REPO / 'yolo_dataset_v14'
DPI = 200
SCALE = DPI / 72.0
if OUT.exists():
    shutil.rmtree(OUT)
(OUT / 'images').mkdir(parents=True)
(OUT / 'labels').mkdir(parents=True)
(OUT / 'classes.txt').write_text('\n'.join(V10_CLASSES) + '\n', encoding='utf-8')
CLASS_MAP = {c: i for i, c in enumerate(V10_CLASSES)}

HELD = ['palomar medical center - floor 10', 'hilton garden inn', 'music academy',
        'swa tech ops', 'busy bees', 'amazon djs7']

pdfs = sorted(glob.glob(str(DATA_ROOT / '*' / 'Completed Takeoff' / 'Takeoff_*.pdf')))
train, held = [], []
for p in pdfs:
    proj = os.path.splitext(os.path.basename(p))[0]
    proj = proj[len('Takeoff_'):] if proj.lower().startswith('takeoff_') else proj
    (held if any(h in proj.lower() for h in HELD) else train).append((proj, p))

print(f'projects: train {len(train)} | held-out {len(held)} | tile {TILE_SIZE}px ov {TILE_OVERLAP}', flush=True)

n_tiles = n_box = dropped = done = 0
class_counter = {}
for proj, p in train:
    slug = b2y.slugify(proj)
    doc = fitz.open(p)
    proj_tiles = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        bboxes = []
        for a in (page.annots() or []):
            if a.type[1] != 'Polygon':
                continue
            cls = map_subject(a.info.get('subject') or '')
            if cls is None:
                dropped += 1
                continue
            r = a.rect
            # Offset by the page's CropBox origin so annotation coords line up
            # with the rendered pixels (origin = page.rect top-left).
            ox, oy = page.rect.x0, page.rect.y0
            bboxes.append({'cls': cls,
                           'x1': (r.x0 - ox) * SCALE, 'y1': (r.y0 - oy) * SCALE,
                           'x2': (r.x1 - ox) * SCALE, 'y2': (r.y1 - oy) * SCALE})
            class_counter[cls] = class_counter.get(cls, 0) + 1
        if not bboxes:
            continue
        # Clean render (no annotation overlay) at 200 DPI -> BGR
        pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), annots=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        for ti, (tile, lines) in enumerate(tile_with_bboxes(img, bboxes, CLASS_MAP)):
            stem = f'{slug}__p{pno+1:03d}__t{ti:03d}'
            cv2.imwrite(str(OUT / 'images' / f'{stem}.png'), tile)
            (OUT / 'labels' / f'{stem}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
            n_tiles += 1
            n_box += len(lines)
            proj_tiles += 1
    doc.close()
    done += 1
    print(f'[{done}/{len(train)}] {proj[:40]:40s} {proj_tiles:>4} tiles', flush=True)

(OUT / 'held_out.json').write_text(
    json.dumps([{'project': pr, 'pdf': pp} for pr, pp in held], indent=2), encoding='utf-8')

print(f'\nDONE: {done} projects -> {n_tiles} tiles, {n_box} box-instances ({dropped} dropped)', flush=True)
print(f'classes seeded: {len(V10_CLASSES)} (v10 head) | used: {len(class_counter)}', flush=True)
for c, n in sorted(class_counter.items(), key=lambda x: -x[1])[:12]:
    print(f'  {n:5d}  {c}', flush=True)
