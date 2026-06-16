"""
benchmark_v10_vs_v11.py

Run v10 and v11 on the same PDF, compare detections side-by-side.
If a Bluebeam markup PDF is supplied as --truth, also score both models
against IoU-matched ground-truth boxes (per-class precision / recall / F1).

Usage:
    # Compare both models, no ground truth:
    python benchmark_v10_vs_v11.py --pdf "<plan.pdf>"

    # With a paired Bluebeam markup as truth:
    python benchmark_v10_vs_v11.py \\
        --pdf "<plan.pdf>" \\
        --truth "<Takeoff_*.pdf>"

    # Render side-by-side annotated PDFs for visual review:
    python benchmark_v10_vs_v11.py --pdf "..." --truth "..." --render

Outputs in benchmark_output/<pdf-stem>/:
    v10_detections.json, v11_detections.json
    comparison.csv         (per-page, per-class counts: v10 / v11 / truth)
    scores.csv             (per-class P / R / F1 for each model, if truth)
    summary.txt            (totals + speed)
    v10_annotated.pdf, v11_annotated.pdf  (only with --render)
"""

import argparse
import json
import time
from collections import defaultdict, Counter
from pathlib import Path

import cv2
import fitz
import numpy as np
from ultralytics import YOLO

from class_normalization import normalize_class

# Lazy imports — only need bluebeam_to_yolo helpers when --truth set
from bluebeam_to_yolo import (
    extract_annotations as extract_truth_annots,
    DEFAULT_DPI,
)

DEFAULT_CONF = 0.4
IOU_THRESHOLD = 0.3  # boxes overlap >= this to count as a match


# ---------- YOLO inference (single page, tiled if needed) ----------

def render_page_image(pdf_path: Path, page_idx: int, dpi: int = DEFAULT_DPI):
    doc = fitz.open(str(pdf_path))
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=dpi, annots=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    pw_pdf, ph_pdf = page.rect.width, page.rect.height
    doc.close()
    return img, pw_pdf, ph_pdf


def tiled_detect(model: YOLO, img: np.ndarray, tile: int = 1280, overlap: int = 200,
                 conf: float = DEFAULT_CONF):
    """Slide a (tile x tile) window with overlap; merge predictions in image space."""
    h, w = img.shape[:2]
    detections = []
    if h <= tile and w <= tile:
        results = model.predict(source=img, conf=conf, imgsz=tile, verbose=False)
        return _extract_dets(results[0], model)
    step = tile - overlap
    for y in range(0, max(1, h - overlap), step):
        for x in range(0, max(1, w - overlap), step):
            x1, y1 = min(x, w - tile), min(y, h - tile)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(x1 + tile, w), min(y1 + tile, h)
            crop = img[y1:y2, x1:x2]
            results = model.predict(source=crop, conf=conf, imgsz=tile, verbose=False)
            for det in _extract_dets(results[0], model):
                det['x1'] += x1; det['x2'] += x1
                det['y1'] += y1; det['y2'] += y1
                detections.append(det)
    return _nms_image(detections, iou_thresh=0.45)


def _extract_dets(result, model: YOLO):
    out = []
    if result.boxes is None or len(result.boxes) == 0:
        return out
    names = model.names
    boxes = result.boxes.xyxy.cpu().numpy()
    cls = result.boxes.cls.cpu().numpy().astype(int)
    conf = result.boxes.conf.cpu().numpy()
    for (x1, y1, x2, y2), c, p in zip(boxes, cls, conf):
        out.append({
            'class': names[c],
            'norm_class': normalize_class(names[c]),
            'conf': float(p),
            'x1': float(x1), 'y1': float(y1),
            'x2': float(x2), 'y2': float(y2),
        })
    return out


def _nms_image(detections, iou_thresh: float):
    """Greedy NMS on image-space detections, per class."""
    by_class = defaultdict(list)
    for d in detections:
        by_class[d['norm_class']].append(d)
    keep = []
    for cls, dets in by_class.items():
        dets = sorted(dets, key=lambda d: -d['conf'])
        suppressed = [False] * len(dets)
        for i, di in enumerate(dets):
            if suppressed[i]:
                continue
            keep.append(di)
            for j in range(i + 1, len(dets)):
                if suppressed[j]:
                    continue
                if _iou_box(di, dets[j]) >= iou_thresh:
                    suppressed[j] = True
    return keep


def _iou_box(a, b):
    ix1, iy1 = max(a['x1'], b['x1']), max(a['y1'], b['y1'])
    ix2, iy2 = min(a['x2'], b['x2']), min(a['y2'], b['y2'])
    iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    aw = a['x2'] - a['x1']; ah = a['y2'] - a['y1']
    bw = b['x2'] - b['x1']; bh = b['y2'] - b['y1']
    union = max(1e-9, aw * ah + bw * bh - inter)
    return inter / union


# ---------- Ground-truth from Bluebeam markup ----------

def gather_truth(truth_pdf: Path, dpi: int):
    """Return dict[page_idx] -> list of {class, x1,y1,x2,y2} in image pixel coords.

    Annotation rects come back in UNROTATED page coordinates, but render_page_image
    rasterizes the page with its rotation applied. For rotated pages (e.g. 90/270)
    the two spaces don't match, so we map each rect through the page rotation matrix
    before scaling to pixels — otherwise IoU vs predictions is always ~0.
    """
    scale = dpi / 72.0
    truth = defaultdict(list)
    doc = fitz.open(str(truth_pdf))
    rot_mat = {pno: doc[pno].rotation_matrix for pno in range(doc.page_count)}
    doc.close()
    for a in extract_truth_annots(truth_pdf):
        pno = a['page'] - 1
        x0p, y0p, x1p, y1p = a['rect_pdf']
        # Map unrotated rect -> displayed (rotated) page space, then to pixels.
        # Rotation can swap corners, so re-derive min/max before scaling.
        r = fitz.Rect(x0p, y0p, x1p, y1p) * rot_mat[pno]
        rx0, rx1 = sorted((r.x0, r.x1))
        ry0, ry1 = sorted((r.y0, r.y1))
        truth[pno].append({
            'class': a['class'],  # already normalized in extractor
            'tag': a.get('tag'),
            'x1': rx0 * scale, 'y1': ry0 * scale,
            'x2': rx1 * scale, 'y2': ry1 * scale,
        })
    return dict(truth)


# ---------- Page selection ----------

MECH_PLAN_KEYWORDS = ('MECHANICAL PLAN', 'HVAC PLAN', 'MECHANICAL FLOOR PLAN')
SKIP_KEYWORDS = ('LEGEND', 'SCHEDULE', 'DETAILS', 'NOTES', 'COVER', 'TITLE SHEET')


def pick_pages(pdf_path: Path, forced_pages=None):
    if forced_pages:
        return [p - 1 for p in forced_pages]
    doc = fitz.open(str(pdf_path))
    candidates = []
    for pno in range(doc.page_count):
        text = doc[pno].get_text('text').upper()[:3000]
        if any(k in text for k in SKIP_KEYWORDS) and not any(k in text for k in MECH_PLAN_KEYWORDS):
            continue
        if any(k in text for k in MECH_PLAN_KEYWORDS):
            candidates.append(pno)
    doc.close()
    return candidates or list(range(doc.page_count))


# ---------- Scoring ----------

def score(predictions, truth, iou_thresh=IOU_THRESHOLD):
    """Per-class P/R/F1 against truth."""
    pred_used = [False] * len(predictions)
    truth_matched = [False] * len(truth)
    tp_per_class = Counter()
    fp_per_class = Counter()
    fn_per_class = Counter()
    confusion = Counter()  # (truth_cls, pred_cls): n
    # Greedy: each truth box matched to highest-IoU prediction with same class
    for ti, t in enumerate(truth):
        best_j, best_iou = -1, 0.0
        for pj, p in enumerate(predictions):
            if pred_used[pj]:
                continue
            iou = _iou_box(t, p)
            if iou > best_iou:
                best_iou, best_j = iou, pj
        if best_j >= 0 and best_iou >= iou_thresh:
            pred_used[best_j] = True
            truth_matched[ti] = True
            confusion[(t['class'], predictions[best_j]['norm_class'])] += 1
            if t['class'] == predictions[best_j]['norm_class']:
                tp_per_class[t['class']] += 1
            else:
                # Right location, wrong class — counts as FN for truth, FP for pred
                fn_per_class[t['class']] += 1
                fp_per_class[predictions[best_j]['norm_class']] += 1
        else:
            fn_per_class[t['class']] += 1
    for pj, p in enumerate(predictions):
        if not pred_used[pj]:
            fp_per_class[p['norm_class']] += 1

    classes = sorted(set(list(tp_per_class) + list(fp_per_class) + list(fn_per_class)))
    rows = []
    for c in classes:
        tp, fp, fn = tp_per_class[c], fp_per_class[c], fn_per_class[c]
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        rows.append({
            'class': c, 'tp': tp, 'fp': fp, 'fn': fn,
            'precision': round(prec, 4),
            'recall': round(rec, 4),
            'f1': round(f1, 4),
        })
    return rows, confusion


# ---------- Per-page render (boxes + class) ----------

COLORS = [(0, 200, 0), (0, 165, 255), (255, 0, 255), (0, 255, 255),
          (200, 0, 0), (0, 0, 200), (255, 200, 0), (128, 0, 128)]


def annotate_image(img, detections, classes, label_prefix=''):
    out = img.copy()
    for d in detections:
        c = classes.index(d['norm_class']) if d['norm_class'] in classes else 0
        color = COLORS[c % len(COLORS)]
        cv2.rectangle(out, (int(d['x1']), int(d['y1'])), (int(d['x2']), int(d['y2'])), color, 3)
        label = f'{label_prefix}{d["norm_class"][:18]}'
        cv2.putText(out, label, (int(d['x1']), max(15, int(d['y1']) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return out


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', required=True, help='Plan PDF to run inference on')
    ap.add_argument('--truth', help='Optional Bluebeam-markup PDF for ground truth')
    ap.add_argument('--v10', default='models/hvac_yolov8s_v10.pt')
    ap.add_argument('--v11', default='models/hvac_yolov8s_v11.pt')
    ap.add_argument('--output-dir', default='benchmark_output')
    ap.add_argument('--conf', type=float, default=DEFAULT_CONF)
    ap.add_argument('--dpi', type=int, default=DEFAULT_DPI)
    ap.add_argument('--pages', nargs='+', type=int, help='1-indexed pages')
    ap.add_argument('--render', action='store_true', help='Save annotated PDFs')
    args = ap.parse_args()

    pdf = Path(args.pdf)
    truth_pdf = Path(args.truth) if args.truth else None
    out_dir = Path(args.output_dir) / pdf.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    if not Path(args.v10).exists():
        raise SystemExit(f'Missing model: {args.v10}')
    if not Path(args.v11).exists():
        raise SystemExit(f'Missing model: {args.v11}. Did you finish training?')

    print(f'Loading models...')
    m10 = YOLO(args.v10)
    m11 = YOLO(args.v11)

    pages = pick_pages(pdf, args.pages)
    print(f'Pages to process: {[p+1 for p in pages]}')

    truth_per_page = gather_truth(truth_pdf, args.dpi) if truth_pdf else {}

    all_v10, all_v11, all_truth = [], [], []
    per_page_v10 = {}
    per_page_v11 = {}
    speed_v10 = 0.0
    speed_v11 = 0.0

    for pno in pages:
        print(f'\nPage {pno+1}:')
        img, _, _ = render_page_image(pdf, pno, args.dpi)

        t0 = time.time(); dets10 = tiled_detect(m10, img, conf=args.conf); speed_v10 += time.time() - t0
        t0 = time.time(); dets11 = tiled_detect(m11, img, conf=args.conf); speed_v11 += time.time() - t0

        for d in dets10:
            d['page'] = pno + 1
            all_v10.append(d)
        for d in dets11:
            d['page'] = pno + 1
            all_v11.append(d)

        per_page_v10[pno + 1] = dets10
        per_page_v11[pno + 1] = dets11
        truths = truth_per_page.get(pno, [])
        for t in truths:
            t['page'] = pno + 1
            all_truth.append(t)

        print(f'  v10: {len(dets10)} dets    v11: {len(dets11)} dets    truth: {len(truths)} polys')

    # Write detections jsonl
    for tag, arr in (('v10', all_v10), ('v11', all_v11)):
        (out_dir / f'{tag}_detections.json').write_text(
            json.dumps(arr, indent=2), encoding='utf-8')

    # Per-class counts table
    counts_v10 = Counter(d['norm_class'] for d in all_v10)
    counts_v11 = Counter(d['norm_class'] for d in all_v11)
    counts_truth = Counter(t['class'] for t in all_truth)
    all_classes = sorted(set(counts_v10) | set(counts_v11) | set(counts_truth))

    cmp_path = out_dir / 'comparison.csv'
    with cmp_path.open('w', encoding='utf-8') as f:
        f.write('class,v10,v11,truth\n')
        for c in all_classes:
            f.write(f'"{c}",{counts_v10[c]},{counts_v11[c]},{counts_truth.get(c, 0)}\n')

    # Per-class P/R/F1 if we have truth
    if all_truth:
        scores_v10, _ = score(all_v10, all_truth)
        scores_v11, _ = score(all_v11, all_truth)

        scores_path = out_dir / 'scores.csv'
        with scores_path.open('w', encoding='utf-8') as f:
            f.write('class,model,tp,fp,fn,precision,recall,f1\n')
            for r in scores_v10:
                f.write(f'"{r["class"]}",v10,{r["tp"]},{r["fp"]},{r["fn"]},{r["precision"]},{r["recall"]},{r["f1"]}\n')
            for r in scores_v11:
                f.write(f'"{r["class"]}",v11,{r["tp"]},{r["fp"]},{r["fn"]},{r["precision"]},{r["recall"]},{r["f1"]}\n')

        # Aggregate
        def micro(rows):
            tp = sum(r['tp'] for r in rows)
            fp = sum(r['fp'] for r in rows)
            fn = sum(r['fn'] for r in rows)
            p = tp / (tp + fp) if tp + fp else 0
            r = tp / (tp + fn) if tp + fn else 0
            f1 = 2 * p * r / (p + r) if p + r else 0
            return tp, fp, fn, p, r, f1

        v10_micro = micro(scores_v10)
        v11_micro = micro(scores_v11)
    else:
        v10_micro = v11_micro = None

    # Summary
    summary_path = out_dir / 'summary.txt'
    lines = [
        f'PDF: {pdf}',
        f'Truth: {truth_pdf or "(none)"}',
        f'Pages: {len(pages)}  ({[p+1 for p in pages]})',
        '',
        f'{"":25s} {"v10":>8s} {"v11":>8s} {"truth":>8s}',
        f'{"Total detections":25s} {len(all_v10):>8d} {len(all_v11):>8d} {len(all_truth):>8d}',
        f'{"Total classes seen":25s} {len(counts_v10):>8d} {len(counts_v11):>8d} {len(counts_truth):>8d}',
        f'{"Total time (s)":25s} {speed_v10:>8.1f} {speed_v11:>8.1f}',
        '',
        '== Per-class counts ==',
        f'{"class":35s} {"v10":>6s} {"v11":>6s} {"truth":>6s}',
    ]
    for c in all_classes:
        lines.append(f'{c:35s} {counts_v10[c]:>6d} {counts_v11[c]:>6d} {counts_truth.get(c,0):>6d}')

    if v10_micro and v11_micro:
        lines += ['', '== Micro-averaged scoring against truth ==',
                  f'{"":10s} {"tp":>6s} {"fp":>6s} {"fn":>6s} {"prec":>7s} {"rec":>7s} {"f1":>7s}']
        for tag, m in (('v10', v10_micro), ('v11', v11_micro)):
            tp, fp, fn, p, r, f1 = m
            lines.append(f'{tag:10s} {tp:>6d} {fp:>6d} {fn:>6d} {p:>7.3f} {r:>7.3f} {f1:>7.3f}')

    summary_text = '\n'.join(lines) + '\n'
    summary_path.write_text(summary_text, encoding='utf-8')
    print()
    print(summary_text)

    # Optional render
    if args.render:
        print('Rendering annotated PDFs...')
        classes_list = sorted(all_classes)
        for tag, per_page in (('v10', per_page_v10), ('v11', per_page_v11)):
            out_pdf_path = out_dir / f'{tag}_annotated.pdf'
            doc = fitz.open(str(pdf))
            for pno, dets in per_page.items():
                img, pw_pdf, ph_pdf = render_page_image(pdf, pno - 1, args.dpi)
                ann = annotate_image(img, dets, classes_list, label_prefix=f'{tag}:')
                # Replace page content with annotated raster
                tmp = out_dir / f'_tmp_{tag}_{pno}.png'
                cv2.imwrite(str(tmp), ann)
                page = doc[pno - 1]
                page.insert_image(page.rect, filename=str(tmp), keep_proportion=False)
                tmp.unlink()
            doc.save(str(out_pdf_path))
            doc.close()
            print(f'  wrote {out_pdf_path}')

    print(f'\nAll outputs in: {out_dir.resolve()}')


if __name__ == '__main__':
    main()
