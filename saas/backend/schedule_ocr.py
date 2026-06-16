"""
schedule_ocr.py — Stage 4 OCR fallback for raster schedules.

The text-layer schedule parser (schedule_parser.py) fails on CAD-exported
PDFs where the schedule LOOKS like a table but the cell contents are raster
graphics, not embedded text. Art Vascular is the canonical case:
variables.json came out as `[]` because pdfplumber couldn't read the cells.

This module:
  1. Renders the schedule page at high DPI (300+).
  2. Runs EasyOCR across the page.
  3. Reconstructs table structure by clustering OCR boxes into rows + columns.
  4. Identifies the tag column.
  5. Extracts TagVariable-shaped dicts.

It is invoked as a fallback when the text-layer parser returned no tags
for a page that the page classifier identified as 'schedule' or 'air_balance'.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


# EasyOCR is expensive to import (~100 MB model load). Lazy.
_reader = None
def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _reader


# A tag looks like 1-4 letters + dash + 1-3 digits, optionally with trailing letter.
# Requires digits — avoids catching English words like CFM, THE, MUST, ON as tags.
TAG_PATTERN = re.compile(r'^[A-Z]{1,4}-?\d{1,3}[A-Z]?$')

# Common English words that look like potential tags but aren't. Defensive.
NON_TAG_WORDS = {'CFM', 'MUST', 'THE', 'ON', 'AND', 'FOR', 'NO', 'YES',
                'TO', 'OF', 'IN', 'BE', 'OR', 'WITH', 'BY', 'AT',
                'A', 'B', 'C', 'D', 'E',  # too short alone
                'NTS', 'NIC', 'TYP', 'EXIST', 'NEW'}

# Header keywords that mark the tag column. 'SYMBOL' is added for drawings
# where the schedule maps drawn symbols to specs (Art Vascular style).
TAG_HEADER_WORDS = {'MARK', 'TAG', 'UNIT', 'ID', 'NUMBER', 'NO', 'NO.', '#', 'SYMBOL'}

# Property column header keywords — used to identify "this is a schedule"
PROP_HEADER_WORDS = {
    'MANUFACTURER', 'MAKE', 'MODEL', 'TYPE', 'SIZE', 'CFM', 'AIR',
    'MCA', 'MOCP', 'VOLT', 'WT', 'WEIGHT', 'LBS', 'NECK',
    'MOUNTING', 'SERVICE', 'REMARK', 'NOTE',
}


def render_page(pdf_path: Path, page_index: int, dpi: int = 300) -> 'numpy.ndarray':
    """Render a page to a numpy RGB image."""
    import numpy as np
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        pix = page.get_pixmap(dpi=dpi, annots=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = img[:, :, :3]
        return img
    finally:
        doc.close()


def ocr_page(img) -> list[dict]:
    """Return list of {text, bbox, conf} where bbox is (x1,y1,x2,y2) in pixel coords."""
    reader = _get_reader()
    raw = reader.readtext(img, detail=1, paragraph=False)
    out = []
    for box, text, conf in raw:
        # box is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({
            'text': text.strip(),
            'bbox': (min(xs), min(ys), max(xs), max(ys)),
            'conf': float(conf),
            'cx': (min(xs) + max(xs)) / 2,
            'cy': (min(ys) + max(ys)) / 2,
        })
    return out


def cluster_rows(items: list[dict], row_tol_px: float = 25) -> list[list[dict]]:
    """Cluster OCR items into rows by Y center. Returns list of rows, each sorted by X."""
    if not items:
        return []
    sorted_by_y = sorted(items, key=lambda d: d['cy'])
    rows = [[sorted_by_y[0]]]
    last_y = sorted_by_y[0]['cy']
    for it in sorted_by_y[1:]:
        if abs(it['cy'] - last_y) <= row_tol_px:
            rows[-1].append(it)
        else:
            rows.append([it])
        last_y = it['cy']
    # Sort each row by X
    for r in rows:
        r.sort(key=lambda d: d['cx'])
    return rows


def cluster_columns(rows: list[list[dict]], col_tol_px: float = 50) -> list[float]:
    """Find shared column x-centers across rows. Returns sorted list of x positions."""
    all_cx = []
    for row in rows:
        for it in row:
            all_cx.append(it['cx'])
    if not all_cx:
        return []
    all_cx.sort()
    cols = [all_cx[0]]
    for cx in all_cx[1:]:
        if cx - cols[-1] > col_tol_px:
            cols.append(cx)
    return cols


def assign_to_columns(row: list[dict], col_centers: list[float],
                      col_tol_px: float = 80) -> dict[int, str]:
    """For each row, snap each item to the nearest column. Returns {col_idx: text}."""
    out: dict[int, str] = {}
    for it in row:
        # Nearest column
        best_i, best_d = -1, 1e9
        for i, cx in enumerate(col_centers):
            d = abs(it['cx'] - cx)
            if d < best_d:
                best_d, best_i = d, i
        if best_i >= 0 and best_d <= col_tol_px:
            existing = out.get(best_i, '')
            out[best_i] = (existing + ' ' + it['text']).strip() if existing else it['text']
    return out


def find_header_row(rows: list[list[dict]]) -> tuple[int, int]:
    """Find the row that's most likely the header. Returns (row_index, tag_col_index_within_row).
    Returns (-1, -1) if no header found.
    """
    best_score = 0
    best_row = -1
    best_tag_idx = -1
    for ri, row in enumerate(rows):
        score = 0
        tag_idx = -1
        for ci, it in enumerate(row):
            t = it['text'].upper().strip(' :,.-')
            if t in TAG_HEADER_WORDS:
                tag_idx = ci
                score += 3  # tag header is a strong signal
            elif any(p in t for p in PROP_HEADER_WORDS):
                score += 1
        if score > best_score:
            best_score = score
            best_row = ri
            best_tag_idx = tag_idx
    return best_row, best_tag_idx


def extract_variables_from_page(pdf_path: Path, page_index: int,
                                schedule_name: str = '',
                                dpi: int = 300) -> list[dict]:
    """OCR + parse a single schedule page. Returns TagVariable-shaped dicts."""
    img = render_page(pdf_path, page_index, dpi=dpi)
    items = ocr_page(img)
    rows = cluster_rows(items)
    if not rows:
        return []

    header_ri, tag_col_local = find_header_row(rows)
    if header_ri < 0:
        return []

    header_row = rows[header_ri]
    # Get column centers from header positions (more reliable than all rows)
    col_centers = [it['cx'] for it in header_row]
    column_names = [it['text'] for it in header_row]

    # Find the canonical tag column index
    tag_col_idx = -1
    for i, name in enumerate(column_names):
        if name.upper().strip(' :,.-') in TAG_HEADER_WORDS:
            tag_col_idx = i
            break

    variables = []
    for ri, row in enumerate(rows):
        if ri <= header_ri:
            continue
        cell_dict = assign_to_columns(row, col_centers, col_tol_px=80)
        # The tag value is whatever sits in the tag column
        tag_raw = cell_dict.get(tag_col_idx, '').strip()
        # Validate that this looks like a tag
        tag_norm = tag_raw.upper().replace(' ', '').replace(',', '')
        if tag_norm in NON_TAG_WORDS:
            continue
        if not TAG_PATTERN.match(tag_norm):
            continue
        properties = {}
        for ci, name in enumerate(column_names):
            if ci == tag_col_idx:
                continue
            val = cell_dict.get(ci, '').strip()
            if val:
                properties[name.upper()] = val

        variables.append({
            'tag': tag_norm,
            'schedule_name': schedule_name or f'OCR schedule p{page_index+1}',
            'page': page_index + 1,
            'properties': properties,
            'source_row_index': ri,
            'inferred_yolo_class': None,    # downstream tag inference can fill this
            '_source': 'ocr',
            '_conf': sum(it['conf'] for it in row) / max(1, len(row)),
        })

    return variables


def extract_all_schedules(pdf_path: Path,
                          schedule_page_nums: list[int],
                          dpi: int = 300) -> list[dict]:
    """Run OCR on every page identified as a schedule, return aggregated variables."""
    out = []
    for p in schedule_page_nums:
        try:
            vars_ = extract_variables_from_page(pdf_path, p - 1, dpi=dpi)
            out.extend(vars_)
        except Exception as e:
            print(f'[schedule_ocr] page {p}: {e}')
    return out


if __name__ == '__main__':
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--page', type=int, required=True, help='1-based page number to OCR')
    ap.add_argument('--dpi', type=int, default=300)
    ap.add_argument('--out', help='Output JSON file (default: stdout)')
    args = ap.parse_args()

    vars_ = extract_variables_from_page(Path(args.pdf), args.page - 1, dpi=args.dpi)
    print(f'Found {len(vars_)} tag variables on page {args.page}:')
    for v in vars_:
        print(f'  {v["tag"]:8s}  {len(v["properties"])} props  conf={v["_conf"]:.2f}')
        for k, val in list(v['properties'].items())[:5]:
            print(f'      {k[:25]:25s} = {val[:40]}')

    if args.out:
        Path(args.out).write_text(json.dumps(vars_, indent=2), encoding='utf-8')
        print(f'\nWrote {args.out}')
