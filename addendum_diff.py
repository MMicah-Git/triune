"""
addendum_diff.py

Compare two versions of HVAC plans (e.g. original vs addendum) and
produce a structured change list: added / removed / moved / unchanged.

Workflow:
  1. Run YOLO inference on both PDFs (or load cached detections.json sidecars).
  2. For each page (matched by index), IoU-match detections between old/new.
  3. Classify:
       unchanged  IoU >= 0.5 and same class
       moved      IoU between 0.1 and 0.5 (same nearest match), same class
       added      detection in NEW with no match in OLD
       removed    detection in OLD with no match in NEW
       relabeled  IoU >= 0.5 but class differs
  4. Emit diff.csv, diff_summary.txt, diff_annotated.pdf (boxes color-coded).

Usage:
    python addendum_diff.py --old "<v1.pdf>" --new "<v2.pdf>"
    python addendum_diff.py --old v1.pdf --new v2.pdf --model models/hvac_yolov8s_v11.pt --render
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import fitz
import numpy as np
from ultralytics import YOLO

from class_normalization import normalize_class

DEFAULT_DPI = 200
DEFAULT_CONF = 0.4
IOU_SAME = 0.5     # >= -> unchanged
IOU_MOVED = 0.1    # between this and IOU_SAME -> moved


# ---------- Render + detect ----------

def render_page(pdf_path: Path, page_idx: int, dpi: int = DEFAULT_DPI):
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=dpi, annots=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if pix.n == 4 else cv2.COLOR_RGB2BGR)
    doc.close()
    return img


def tiled_detect(model: YOLO, img: np.ndarray, tile: int = 1280, overlap: int = 200,
                 conf: float = DEFAULT_CONF):
    h, w = img.shape[:2]
    detections = []
    if h <= tile and w <= tile:
        return _from_result(model.predict(source=img, conf=conf, imgsz=tile, verbose=False)[0], model)
    step = tile - overlap
    for y in range(0, max(1, h - overlap), step):
        for x in range(0, max(1, w - overlap), step):
            x1 = max(0, min(x, w - tile))
            y1 = max(0, min(y, h - tile))
            x2 = min(x1 + tile, w)
            y2 = min(y1 + tile, h)
            crop = img[y1:y2, x1:x2]
            for d in _from_result(model.predict(source=crop, conf=conf, imgsz=tile, verbose=False)[0], model):
                d['x1'] += x1; d['x2'] += x1
                d['y1'] += y1; d['y2'] += y1
                detections.append(d)
    return _nms(detections, 0.45)


def _from_result(result, model):
    out = []
    if result.boxes is None or len(result.boxes) == 0:
        return out
    names = model.names
    boxes = result.boxes.xyxy.cpu().numpy()
    cls = result.boxes.cls.cpu().numpy().astype(int)
    conf = result.boxes.conf.cpu().numpy()
    for (x1, y1, x2, y2), c, p in zip(boxes, cls, conf):
        out.append({
            'class': normalize_class(names[c]),
            'conf': float(p),
            'x1': float(x1), 'y1': float(y1),
            'x2': float(x2), 'y2': float(y2),
        })
    return out


def _iou(a, b):
    ix1 = max(a['x1'], b['x1']); iy1 = max(a['y1'], b['y1'])
    ix2 = min(a['x2'], b['x2']); iy2 = min(a['y2'], b['y2'])
    iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    aw = a['x2'] - a['x1']; ah = a['y2'] - a['y1']
    bw = b['x2'] - b['x1']; bh = b['y2'] - b['y1']
    union = max(1e-9, aw*ah + bw*bh - inter)
    return inter / union


def _nms(dets, iou_thr):
    by_class = defaultdict(list)
    for d in dets:
        by_class[d['class']].append(d)
    keep = []
    for cls, arr in by_class.items():
        arr = sorted(arr, key=lambda d: -d['conf'])
        suppressed = [False] * len(arr)
        for i, di in enumerate(arr):
            if suppressed[i]:
                continue
            keep.append(di)
            for j in range(i + 1, len(arr)):
                if not suppressed[j] and _iou(di, arr[j]) >= iou_thr:
                    suppressed[j] = True
    return keep


# ---------- Diff core ----------

def diff_page(old_dets, new_dets):
    """Match detections between two versions of the same page.

    Returns a list of records: {status, old, new}
      status in {unchanged, moved, relabeled, added, removed}
    """
    new_used = [False] * len(new_dets)
    records = []

    # For each OLD detection, find best new match by IoU
    for od in old_dets:
        best_j, best_iou = -1, 0.0
        for j, nd in enumerate(new_dets):
            if new_used[j]:
                continue
            iou = _iou(od, nd)
            if iou > best_iou:
                best_iou, best_j = iou, j
        if best_j < 0 or best_iou < IOU_MOVED:
            records.append({'status': 'removed', 'old': od, 'new': None, 'iou': best_iou})
            continue
        new_used[best_j] = True
        nd = new_dets[best_j]
        if best_iou >= IOU_SAME:
            if od['class'] == nd['class']:
                records.append({'status': 'unchanged', 'old': od, 'new': nd, 'iou': best_iou})
            else:
                records.append({'status': 'relabeled', 'old': od, 'new': nd, 'iou': best_iou})
        else:
            if od['class'] == nd['class']:
                records.append({'status': 'moved', 'old': od, 'new': nd, 'iou': best_iou})
            else:
                # Marginal IoU + class change — treat as removed + added separately
                records.append({'status': 'removed', 'old': od, 'new': None, 'iou': best_iou})
                new_used[best_j] = False

    for j, nd in enumerate(new_dets):
        if not new_used[j]:
            records.append({'status': 'added', 'old': None, 'new': nd, 'iou': 0.0})

    return records


# ---------- Output ----------

COLOR = {
    'added':     (0, 200, 0),     # green
    'removed':   (0, 0, 200),     # red (BGR)
    'moved':     (0, 200, 200),   # yellow
    'relabeled': (200, 0, 200),   # magenta
    'unchanged': (180, 180, 180), # gray
}


def write_csv(out_path: Path, diffs):
    with out_path.open('w', encoding='utf-8') as f:
        f.write('page,status,old_class,new_class,iou,x1,y1,x2,y2\n')
        for d in diffs:
            page = d['page']
            status = d['status']
            old_cls = d['old']['class'] if d['old'] else ''
            new_cls = d['new']['class'] if d['new'] else ''
            box = d['new'] or d['old']
            f.write(f'{page},{status},"{old_cls}","{new_cls}",{d["iou"]:.3f},'
                    f'{box["x1"]:.1f},{box["y1"]:.1f},{box["x2"]:.1f},{box["y2"]:.1f}\n')


def render_annotated_pdf(new_pdf: Path, diffs, out_pdf: Path, dpi: int):
    by_page = defaultdict(list)
    for d in diffs:
        by_page[d['page']].append(d)
    doc = fitz.open(str(new_pdf))
    tmp_dir = out_pdf.parent
    for pno in sorted(by_page):
        img = render_page(new_pdf, pno - 1, dpi)
        for d in by_page[pno]:
            box = d['new'] or d['old']
            color = COLOR.get(d['status'], (128, 128, 128))
            x1, y1, x2, y2 = int(box['x1']), int(box['y1']), int(box['x2']), int(box['y2'])
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 3)
            label = d['status'][:1].upper() + ' ' + (d['new']['class'] if d['new'] else d['old']['class'])[:18]
            cv2.putText(img, label, (x1, max(15, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        tmp = tmp_dir / f'_tmp_diff_p{pno}.png'
        cv2.imwrite(str(tmp), img)
        page = doc[pno - 1]
        page.insert_image(page.rect, filename=str(tmp), keep_proportion=False)
        tmp.unlink()
    doc.save(str(out_pdf))
    doc.close()


# ---------- Main ----------

def run_inference(model: YOLO, pdf_path: Path, dpi: int, conf: float):
    """Return dict[page_idx_1based] -> list of detection dicts in image coords."""
    doc = fitz.open(str(pdf_path))
    pages = list(range(doc.page_count))
    doc.close()
    out = {}
    for pno in pages:
        img = render_page(pdf_path, pno, dpi)
        out[pno + 1] = tiled_detect(model, img, conf=conf)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old', required=True, help='Original PDF (v1)')
    ap.add_argument('--new', required=True, help='Addendum / updated PDF (v2)')
    ap.add_argument('--model', default='models/hvac_yolov8s_v10.pt')
    ap.add_argument('--conf', type=float, default=DEFAULT_CONF)
    ap.add_argument('--dpi', type=int, default=DEFAULT_DPI)
    ap.add_argument('--output-dir', default='addendum_output')
    ap.add_argument('--render', action='store_true', help='Write annotated diff PDF')
    args = ap.parse_args()

    old_pdf = Path(args.old)
    new_pdf = Path(args.new)
    out_dir = Path(args.output_dir) / new_pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'Loading {args.model}...')
    model = YOLO(args.model)

    print(f'Running inference on OLD: {old_pdf.name}')
    t0 = time.time(); dets_old = run_inference(model, old_pdf, args.dpi, args.conf)
    print(f'  done in {time.time()-t0:.1f}s — {sum(len(v) for v in dets_old.values())} detections')
    print(f'Running inference on NEW: {new_pdf.name}')
    t0 = time.time(); dets_new = run_inference(model, new_pdf, args.dpi, args.conf)
    print(f'  done in {time.time()-t0:.1f}s — {sum(len(v) for v in dets_new.values())} detections')

    # Diff page by page
    all_pages = sorted(set(dets_old) | set(dets_new))
    all_diffs = []
    for pno in all_pages:
        page_diffs = diff_page(dets_old.get(pno, []), dets_new.get(pno, []))
        for d in page_diffs:
            d['page'] = pno
            all_diffs.append(d)

    write_csv(out_dir / 'diff.csv', all_diffs)

    # Summary
    counts = Counter(d['status'] for d in all_diffs)
    by_page_status = defaultdict(Counter)
    by_class_added = Counter()
    by_class_removed = Counter()
    for d in all_diffs:
        by_page_status[d['page']][d['status']] += 1
        if d['status'] == 'added':
            by_class_added[d['new']['class']] += 1
        elif d['status'] == 'removed':
            by_class_removed[d['old']['class']] += 1

    lines = [
        f'OLD: {old_pdf}',
        f'NEW: {new_pdf}',
        f'Pages compared: {len(all_pages)}',
        '',
        '=== Totals ===',
    ]
    for status in ('unchanged', 'moved', 'relabeled', 'added', 'removed'):
        lines.append(f'  {status:10s} {counts.get(status, 0):6d}')
    lines.append('')
    lines.append('=== Per-page ===')
    lines.append(f'{"page":>6s} {"unch":>6s} {"moved":>6s} {"relab":>6s} {"added":>6s} {"removed":>7s}')
    for pno in sorted(by_page_status):
        c = by_page_status[pno]
        lines.append(f'{pno:>6d} {c.get("unchanged",0):>6d} {c.get("moved",0):>6d} '
                     f'{c.get("relabeled",0):>6d} {c.get("added",0):>6d} {c.get("removed",0):>7d}')

    if by_class_added or by_class_removed:
        lines += ['', '=== Class breakdown of changes ===',
                  f'{"class":35s} {"+":>4s} {"-":>4s}']
        for cls in sorted(set(by_class_added) | set(by_class_removed)):
            lines.append(f'{cls:35s} {by_class_added.get(cls, 0):>4d} {by_class_removed.get(cls, 0):>4d}')

    summary = '\n'.join(lines) + '\n'
    (out_dir / 'diff_summary.txt').write_text(summary, encoding='utf-8')
    print()
    print(summary)

    if args.render:
        out_pdf = out_dir / 'diff_annotated.pdf'
        print(f'Rendering annotated diff PDF -> {out_pdf}')
        render_annotated_pdf(new_pdf, all_diffs, out_pdf, args.dpi)

    print(f'\nAll outputs in: {out_dir.resolve()}')


if __name__ == '__main__':
    main()
