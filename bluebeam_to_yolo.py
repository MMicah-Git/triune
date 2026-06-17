"""
bluebeam_to_yolo.py

Extract Bluebeam takeoff Polygon annotations from a marked-up PDF and
convert them into a YOLO training dataset. Each polygon = one labeled box.

Usage (single project):
    python bluebeam_to_yolo.py \\
        --markup-pdf "<...>/Completed Takeoff/Takeoff_*.pdf" \\
        --project-name "music-academy-2026" \\
        --output-dir yolo_dataset_v11

Usage (batch over a sample-files root):
    python bluebeam_to_yolo.py \\
        --batch-root "<...>/SAMPLE FILES 11.05.2026" \\
        --output-dir yolo_dataset_v11

Output structure:
    yolo_dataset_v11/
        images/<project>__p<NNN>.png          (200 DPI rendered page, no markups)
        labels/<project>__p<NNN>.txt          (YOLO format: cls cx cy w h, normalized)
        annotations/<project>.jsonl           (rich per-annotation record)
        classes.txt                           (cumulative class index)
        manifest.json                         (which projects have been processed)
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF

from class_normalization import normalize_class
from v10_class_map import V10_CLASSES, map_subject

# Canonical class -> fixed index, matching the model head order. ALL training
# labels must use this index space so corrections line up with the base dataset
# and the model being fine-tuned.
V10_INDEX = {c: i for i, c in enumerate(V10_CLASSES)}


# Subjects we filter out — annotations that are not equipment counts
NON_EQUIPMENT_SUBJECTS = {
    'Rectangle',
    'Length Measurement',
    'Area Measurement',
    'Perimeter Measurement',
    'Text Box',
    'Note',
    'Callout',
    'Cloud',
    '',
}

# Default render DPI — matches takeoff_cli.py
DEFAULT_DPI = 200


# ---------- Bluebeam annotation parsing ----------

def is_equipment_annotation(annot) -> bool:
    if annot.type[1] != 'Polygon':
        return False
    subj = annot.info.get('subject', '').strip()
    if subj in NON_EQUIPMENT_SUBJECTS:
        return False
    return True


def parse_content(content: str):
    """Bluebeam stores tag info in `content` as 'TAG\\rSEQ'. Return (tag, seq)."""
    if not content:
        return None, None
    parts = content.replace('\r\n', '\r').split('\r')
    tag = parts[0].strip() if parts else None
    seq = parts[1].strip() if len(parts) > 1 else None
    return tag, seq


def extract_annotations(markup_pdf_path: Path):
    """Yield one dict per equipment polygon annotation."""
    doc = fitz.open(str(markup_pdf_path))
    try:
        for pno, page in enumerate(doc):
            annots = page.annots() or []
            for a in annots:
                if not is_equipment_annotation(a):
                    continue
                rect = a.rect
                tag, seq = parse_content(a.info.get('content', ''))
                raw_subject = a.info.get('subject', '').strip()
                canonical = normalize_class(raw_subject)
                yield {
                    'page': pno + 1,
                    'class': canonical,
                    'subclass': raw_subject.upper(),  # preserved for downstream
                    'tag': tag,
                    'seq': seq,
                    'rect_pdf': [rect.x0, rect.y0, rect.x1, rect.y1],
                    'page_width_pdf': page.rect.width,
                    'page_height_pdf': page.rect.height,
                }
    finally:
        doc.close()


# ---------- YOLO label conversion ----------

def yolo_label_line(class_id: int, rect_pdf, page_size_pdf) -> str:
    """Convert PDF-coord rect -> normalized YOLO line."""
    x0, y0, x1, y1 = rect_pdf
    pw, ph = page_size_pdf
    cx = ((x0 + x1) / 2) / pw
    cy = ((y0 + y1) / 2) / ph
    w = abs(x1 - x0) / pw
    h = abs(y1 - y0) / ph
    # Clamp into [0, 1] for safety
    cx, cy = max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy))
    w, h = max(0.0, min(1.0, w)), max(0.0, min(1.0, h))
    return f'{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}'


# ---------- Class index (cumulative across projects) ----------

class ClassIndex:
    def __init__(self, path: Path):
        self.path = path
        self.classes = []
        self.idx = {}
        if path.exists():
            self.classes = [
                line.strip() for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip()
            ]
            self.idx = {c: i for i, c in enumerate(self.classes)}

    def get_or_create(self, name: str) -> int:
        if name not in self.idx:
            self.idx[name] = len(self.classes)
            self.classes.append(name)
        return self.idx[name]

    def save(self):
        self.path.write_text('\n'.join(self.classes) + '\n', encoding='utf-8')


# ---------- Single-project pipeline ----------

def process_project(markup_pdf: Path, project_name: str, output_dir: Path,
                    source_pdf: Path | None = None, dpi: int = DEFAULT_DPI) -> dict:
    """Process one project. Returns summary dict."""
    images_dir = output_dir / 'images'
    labels_dir = output_dir / 'labels'
    annots_dir = output_dir / 'annotations'
    for d in (images_dir, labels_dir, annots_dir):
        d.mkdir(parents=True, exist_ok=True)

    src_pdf = source_pdf or markup_pdf

    by_page = defaultdict(list)
    class_counter = Counter()
    dropped_counter = Counter()
    for a in extract_annotations(markup_pdf):
        # Map the RAW Bluebeam subject onto the canonical 33-class v10 taxonomy
        # and stamp a FIXED index. Boxes with no sensible home are dropped.
        v10cls = map_subject(a.get('subclass') or a.get('class'))
        if v10cls is None or v10cls not in V10_INDEX:
            dropped_counter[a.get('subclass') or a.get('class') or '?'] += 1
            continue
        a['v10_class'] = v10cls
        a['v10_index'] = V10_INDEX[v10cls]
        by_page[a['page']].append(a)
        class_counter[v10cls] += 1

    if not by_page:
        return {'project': project_name, 'pages': 0, 'boxes': 0, 'classes': {}}

    # Write per-project annotations.jsonl (overwrite — idempotent re-runs)
    jsonl_path = annots_dir / f'{project_name}.jsonl'
    jsonl_path.write_text('', encoding='utf-8')

    src_doc = fitz.open(str(src_pdf))
    try:
        for page_num in sorted(by_page):
            page = src_doc[page_num - 1]
            pw_pdf, ph_pdf = page.rect.width, page.rect.height

            stem = f'{project_name}__p{page_num:03d}'
            img_path = images_dir / f'{stem}.png'
            lbl_path = labels_dir / f'{stem}.txt'

            # Render without annotations (clean training image)
            pix = page.get_pixmap(dpi=dpi, annots=False)
            pix.save(str(img_path))

            lines = []
            for a in by_page[page_num]:
                cid = a['v10_index']   # fixed canonical index
                lines.append(yolo_label_line(cid, a['rect_pdf'], (pw_pdf, ph_pdf)))

                with jsonl_path.open('a', encoding='utf-8') as f:
                    f.write(json.dumps({
                        'project': project_name,
                        'image': f'images/{stem}.png',
                        'label': f'labels/{stem}.txt',
                        'class_id': cid,
                        **a,
                    }) + '\n')

            lbl_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    finally:
        src_doc.close()

    # Always write the canonical 33-class list — never a per-file grown index.
    (output_dir / 'classes.txt').write_text('\n'.join(V10_CLASSES) + '\n', encoding='utf-8')

    return {
        'project': project_name,
        'pages': len(by_page),
        'boxes': sum(len(v) for v in by_page.values()),
        'classes': dict(class_counter),
        'dropped': dict(dropped_counter),
    }


# ---------- Batch over a sample-files root ----------

def find_project_pdfs(batch_root: Path):
    """Yield (project_label, markup_pdf, source_pdf|None) for each project.

    Supports two layouts:
      1. Nested:  <batch_root>/<project_dir>/Completed Takeoff/Takeoff_*.pdf
                  <batch_root>/<project_dir>/Plans_Specs/<plan>.pdf
      2. Flat:    <batch_root>/Takeoff_*.pdf  (and Takeoff_*.xlsx alongside)
    """
    # Flat layout: Takeoff_*.pdf siblings
    flat = sorted(batch_root.glob('Takeoff_*.pdf'))
    for markup_pdf in flat:
        label = markup_pdf.stem
        if label.startswith('Takeoff_'):
            label = label[len('Takeoff_'):]
        yield label, markup_pdf, None

    # Nested layout
    for project_dir in sorted(p for p in batch_root.iterdir() if p.is_dir()):
        completed = project_dir / 'Completed Takeoff'
        plans = project_dir / 'Plans_Specs'
        if not completed.is_dir():
            continue
        markup_pdfs = sorted(completed.glob('Takeoff*.pdf'))
        if not markup_pdfs:
            continue
        markup_pdf = markup_pdfs[0]
        source_pdf = None
        if plans.is_dir():
            plan_pdfs = sorted(plans.glob('*.pdf'))
            if plan_pdfs:
                source_pdf = plan_pdfs[0]
        yield project_dir.name, markup_pdf, source_pdf


def slugify(name: str) -> str:
    """Filename-safe slug."""
    keep = []
    for ch in name:
        if ch.isalnum():
            keep.append(ch.lower())
        elif ch in ' -_.':
            keep.append('-')
    s = ''.join(keep)
    while '--' in s:
        s = s.replace('--', '-')
    return s.strip('-')


# ---------- Manifest tracking ----------

def load_manifest(output_dir: Path) -> dict:
    f = output_dir / 'manifest.json'
    if f.exists():
        return json.loads(f.read_text(encoding='utf-8'))
    return {'projects': {}}


def save_manifest(output_dir: Path, manifest: dict):
    f = output_dir / 'manifest.json'
    f.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser(description='Bluebeam markups -> YOLO dataset')
    ap.add_argument('--markup-pdf', help='Single project: path to Takeoff_*.pdf')
    ap.add_argument('--source-pdf', help='Single project: original drawing PDF (defaults to markup-pdf with annotations hidden)')
    ap.add_argument('--project-name', help='Single project: filename slug')
    ap.add_argument('--batch-root', help='Batch: folder containing N project subdirs')
    ap.add_argument('--output-dir', default='yolo_dataset_v11', help='Where to write the dataset')
    ap.add_argument('--dpi', type=int, default=DEFAULT_DPI)
    args = ap.parse_args()

    if not args.markup_pdf and not args.batch_root:
        ap.error('Provide either --markup-pdf or --batch-root')

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(output_dir)

    if args.markup_pdf:
        markup = Path(args.markup_pdf)
        source = Path(args.source_pdf) if args.source_pdf else None
        name = args.project_name or slugify(markup.stem)
        print(f'>> {name}')
        summary = process_project(markup, name, output_dir, source_pdf=source, dpi=args.dpi)
        print(f'   pages={summary["pages"]}  boxes={summary["boxes"]}')
        for cls, n in sorted(summary['classes'].items(), key=lambda x: -x[1]):
            print(f'     {n:5d}  {cls}')
        manifest['projects'][name] = summary
        save_manifest(output_dir, manifest)
        return

    # Batch mode
    batch_root = Path(args.batch_root)
    projects = list(find_project_pdfs(batch_root))
    print(f'Found {len(projects)} projects under {batch_root}')

    grand_totals = Counter()
    for label, markup, source in projects:
        name = slugify(label)
        if name in manifest['projects']:
            print(f'>> {name}  (skipped — already in manifest)')
            continue
        print(f'>> {name}')
        try:
            summary = process_project(markup, name, output_dir, source_pdf=source, dpi=args.dpi)
        except Exception as e:
            print(f'   ERROR: {e}', file=sys.stderr)
            manifest['projects'][name] = {'error': str(e)}
            save_manifest(output_dir, manifest)
            continue
        print(f'   pages={summary["pages"]}  boxes={summary["boxes"]}')
        for cls, n in sorted(summary['classes'].items(), key=lambda x: -x[1])[:5]:
            print(f'     {n:5d}  {cls}')
        grand_totals.update(summary['classes'])
        manifest['projects'][name] = summary
        save_manifest(output_dir, manifest)

    print()
    print('=== GRAND TOTALS ===')
    for cls, n in grand_totals.most_common():
        print(f'  {n:6d}  {cls}')


if __name__ == '__main__':
    main()
