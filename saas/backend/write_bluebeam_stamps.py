"""
write_bluebeam_stamps.py

Take AI detections + the original PDF, write a NEW PDF with each detection as
a real Bluebeam-compatible PolygonCount annotation (the same kind your team
creates when they stamp equipment with the NSW ToolBox).

When the estimator opens the output PDF in Bluebeam:
  - Every AI detection appears as a real count stamp in the Markups List
  - Counts auto-aggregate by Subject (so 46 AD-GRD will show as one row "46")
  - Estimator deletes wrong ones / stamps missed ones with their own toolbox
  - Saves → upload as correction → bluebeam_to_yolo reads back the same /Subj

Usage:
    python write_bluebeam_stamps.py --job a0cea255f726
    python write_bluebeam_stamps.py --pdf input.pdf --detections dets.json --out out.pdf

What it writes per detection:
    /Subtype /Polygon              (PyMuPDF)
    /IT      /PolygonCount         (raw — tells Bluebeam it's a count stamp)
    /Subj    (Polygon Count)       (raw — Bluebeam annotation type label)
    /Subject (AD-GRD)              (the toolbox subject from toolbox_mapping)
    /IC      [r g b]               (interior color from toolbox_mapping)
    /Vertices [x1 y1 x2 y2 ...]    (polygon corners from the bbox)
    /CA      0.5                   (50% opacity so the underlying drawing stays visible)
    /T       (AI)                  (author — so estimator can filter "AI" stamps later)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

# Make toolbox_mapping importable whether run from backend/ or repo root
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from toolbox_mapping import map_ai_class  # noqa: E402
from context_enrich import enrich  # noqa: E402
from page_classifier import classify_pdf, NON_PLAN_TYPES  # noqa: E402


REPO_ROOT = HERE.parent.parent  # …/saas/backend → repo root
DATA_DIR = REPO_ROOT / 'saas' / 'data'


# ---------- core stamp writer ----------

def _parse_color(color_str: str) -> tuple[float, float, float]:
    """'1 1 0' → (1.0, 1.0, 0.0)"""
    parts = [float(x) for x in color_str.split()]
    return parts[0], parts[1], parts[2]


# Bluebeam custom-column definitions, replicated from the team's takeoff so our
# stamped PDF's Markup List shows the SAME columns (BRAND/MODEL/NECK/TYPE/...).
# Catalog key /BSIAnnotColumns -> this array; each annotation's /BSIColumnData
# array fills values BY INDEX into this array (Choice cols simplified to Text).
BSI_COLUMN_DEFS = (
    "[ << /Subtype /Text /Name (ACCESSORIES1) /DisplayOrder 8 /Multiline false >>"
    " << /Subtype /Text /Name (MODEL) /DisplayOrder 1 /Multiline false >>"
    " << /Subtype /Text /Name (BRAND) /DisplayOrder 0 /Multiline false >>"
    " << /Subtype /Text /Name (REMARK) /DisplayOrder 10 /Multiline false >>"
    " << /Subtype /Text /Name (NECK SIZE) /DisplayOrder 2 /Multiline false >>"
    " << /Subtype /Text /Name (TYPE) /DisplayOrder 6 /Multiline false >>"
    " << /Subtype /Text /Name (UNIT) /DisplayOrder -1 /Deleted true /Multiline false >>"
    " << /Subtype /Text /Name (MOUNTING) /DisplayOrder 7 /Multiline false >>"
    " << /Subtype /Text /Name (CFM) /DisplayOrder 5 /Multiline false >>"
    " << /Subtype /Text /Name (R1) /DisplayOrder -1 /Deleted true /Multiline false >>"
    " << /Subtype /Text /Name (U1) /DisplayOrder -1 /Deleted true /Multiline false >>"
    " << /Subtype /Text /Name (DUCT SIZE) /DisplayOrder 4 /Multiline false >>"
    " << /Subtype /Text /Name (UNITS) /DisplayOrder 11 /Multiline false >>"
    " << /Subtype /Text /Name (TEST) /DisplayOrder -1 /Deleted true /Multiline false >>"
    " << /Subtype /Text /Name (MODULE SIZE) /DisplayOrder 3 /Multiline false >>"
    " << /Subtype /Text /Name (ACCESSORIES2) /DisplayOrder 9 /Multiline false >>"
    " << /Subtype /Text /Name (DAMPER TYPE) /DisplayOrder 12 /Multiline false >>"
    " << /Subtype /Text /Name (LOCATION) /DisplayOrder -1 /Deleted true /Multiline false >>"
    " << /Subtype /Text /Name (HET OR DAM) /DisplayOrder -1 /Deleted true /Multiline false >> ]"
)
# index in BSIColumnData -> our value key (others stay empty)
BSI_INDEX = {1: 'model', 2: 'brand', 4: 'neck', 5: 'type', 7: 'mounting',
             8: 'cfm', 11: 'duct', 14: 'module', 15: 'accessories', 16: 'damper'}
BSI_NCOLS = 19


def _pdf_str_escape(s):
    return str(s or '').replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')


def _bsi_column_data(values: dict) -> str:
    """Build a /BSIColumnData array string from a {key: value} dict."""
    cells = [''] * BSI_NCOLS
    for idx, key in BSI_INDEX.items():
        cells[idx] = values.get(key, '') or ''
    return '[' + ' '.join('(' + _pdf_str_escape(c) + ')' for c in cells) + ']'


def write_stamps(input_pdf: Path, detections_json: Path, output_pdf: Path,
                 author: str = 'AI', do_enrich: bool = True,
                 do_page_filter: bool = True) -> dict:
    """Write Bluebeam-compatible PolygonCount stamps onto input_pdf.

    If do_enrich is True (default), runs context_enrich.enrich() first to
    apply the Deck-2 tagging rules (FSD context, CRD detection, linear
    diffuser merging) before writing stamps.

    If do_page_filter is True (default), classifies every page via
    page_classifier and SKIPS pages identified as cover/schedule/legend/
    details/riser/air_balance — those are the #1 source of phantom stamps.

    Returns a summary dict with counts of stamps written and skipped.
    """
    det_data = json.loads(detections_json.read_text(encoding='utf-8'))
    if do_enrich:
        det_data = enrich(det_data, input_pdf)
    pages = det_data.get('pages', {})
    src_dpi = det_data.get('dpi', 200)

    # Load the parsed schedule (variables.json sidecar) so each markup's hover
    # can carry the tag's real specs (type, neck/module/duct size) — the same
    # data the Excel uses. Keyed by canonical tag (EF1 == EF-1).
    import re as _re_ct
    def _canon(t):
        return _re_ct.sub(r'[\s\-_.]+', '', str(t or '').upper())
    def _prop(props, keywords):
        if not props:
            return ''
        for k, v in props.items():
            kn = ' '.join(str(k).upper().split())
            for kw in keywords:
                if kw in kn:
                    return str(v)
        return ''
    vars_by_tag = {}
    try:
        _vpath = detections_json.parent / detections_json.name.replace(
            '_detections.json', '_variables.json')
        if _vpath.exists():
            for _var in json.loads(_vpath.read_text(encoding='utf-8')):
                _t = _var.get('tag')
                if _t:
                    vars_by_tag[_canon(_t)] = _var.get('properties') or {}
    except Exception:
        vars_by_tag = {}

    # Page-type classification — skip non-plan pages
    skipped_pages_meta = {}
    skipped_non_plan_pages = set()
    if do_page_filter:
        dets_per_page = {int(k): len(v) for k, v in pages.items()}
        classifications = classify_pdf(input_pdf, detections_per_page=dets_per_page)
        for c in classifications:
            skipped_pages_meta[c.page] = c.to_dict()
            if c.type in NON_PLAN_TYPES:
                skipped_non_plan_pages.add(c.page)
    # Detection coords are in rendered-image pixel space at src_dpi.
    # PDF coords are in points (72 DPI). Scale factor:
    px_to_pt = 72.0 / src_dpi

    doc = fitz.open(str(input_pdf))

    # Register Bluebeam custom columns in the catalog so the Markup List shows
    # the team's columns (BRAND/MODEL/NECK SIZE/TYPE/MOUNTING/MODULE/DUCT/...).
    try:
        _cdx = doc.get_new_xref()
        doc.update_object(_cdx, BSI_COLUMN_DEFS)
        doc.xref_set_key(doc.pdf_catalog(), 'BSIAnnotColumns', f'{_cdx} 0 R')
    except Exception as _e:
        print(f'[bluebeam] column-def injection skipped: {_e}')

    # PyMuPDF's add_polygon_annot doesn't apply page rotation when writing
    # /Rect and /Vertices — it only does a Y-flip. For rotated pages this
    # places stamps in the wrong location. Workaround: capture derotation
    # matrices first, temporarily set every page's rotation to 0, write
    # annotations in pre-transformed mediabox coords, then restore rotation.
    # Restoring rotation makes viewers re-apply it for display, putting the
    # stamps back in the right visual spot.
    saved_rotations: dict[int, int] = {}
    derot_matrices: dict[int, fitz.Matrix] = {}
    for pno in range(doc.page_count):
        p = doc[pno]
        saved_rotations[pno] = p.rotation
        if p.rotation != 0:
            derot_matrices[pno] = fitz.Matrix(p.derotation_matrix)
            p.set_rotation(0)

    written_by_subject = Counter()
    written_by_tag = Counter()
    skipped_unmapped = Counter()
    skipped_no_match = 0
    n_merged_runs = 0
    n_crd = 0
    n_fsd_op = 0

    n_skipped_by_page_filter = 0
    for pkey, dets in pages.items():
        # detections.json keys are 0-indexed page indices (str(page_idx)),
        # NOT 1-indexed PDF page numbers. The previous `int(pkey) - 1` was
        # off by one and caused every plan-page detection to be silently
        # skipped as "non-plan", producing empty stamped PDFs.
        pno = int(pkey)
        if pno < 0 or pno >= doc.page_count:
            continue
        # Trust the detector: a page that HAS detections is a plan page, even if
        # the (text-based) page classifier mislabeled it. Dropping real
        # detections here produced near-empty Bluebeam PDFs (e.g. 2 written vs
        # 135 skipped) while the annotated PDF showed everything. So only honor
        # the non-plan skip for pages that have no detections anyway.
        if (pno + 1) in skipped_non_plan_pages and not dets:
            n_skipped_by_page_filter += len(dets)
            continue
        page = doc[pno]
        page_h_pt = page.rect.height  # PDF page height in points

        for det in dets:
            ai_cls = det.get('cls', '')
            subj, color, conf, note = map_ai_class(ai_cls)
            if subj is None:
                if conf == 'UNMAPPED':
                    skipped_unmapped[ai_cls] += 1
                else:
                    skipped_no_match += 1
                continue

            # Bbox in rendered-image pixels at src_dpi → PDF points.
            # YOLO/PyMuPDF both use top-down y, so no flip needed here.
            x1 = det['x1'] * px_to_pt
            y1 = det['y1'] * px_to_pt
            x2 = det['x2'] * px_to_pt
            y2 = det['y2'] * px_to_pt

            # Polygon vertices clockwise from top-left, in display space
            display_verts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

            # If the original page was rotated, pre-transform the verts to
            # mediabox space so they end up in the right spot after we
            # restore the rotation below.
            if pno in derot_matrices:
                mat = derot_matrices[pno]
                verts = [tuple(fitz.Point(vx, vy) * mat) for (vx, vy) in display_verts]
            else:
                verts = display_verts

            # 1. Create the polygon via PyMuPDF (handles /Subtype, /Rect, /Vertices)
            annot = page.add_polygon_annot(verts)

            # Compose the Contents string with all enriched info so the
            # estimator sees the team's tagging convention on hover.
            content_parts = [ai_cls]
            tag_for_count = None
            det_tag = det.get('context_tag') or det.get('tag')
            if det.get('context_tag'):                          # Rule A (context wins)
                tag_for_count = det['context_tag']
                content_parts.append(f"tag={det['context_tag']}")
                content_parts.append(f"type={det['context_type']}")
                if det['context_tag'] == 'FSD-OP':
                    n_fsd_op += 1
            elif det.get('tag'):                                 # inferred schedule tag
                tag_for_count = det['tag']
                content_parts.append(f"tag={det['tag']}")
            # Schedule-derived specs on the hover (type, neck/module/duct size) —
            # same data as the Excel, so the markup carries the full detail.
            if det_tag:
                _props = vars_by_tag.get(_canon(det_tag))
                if _props:
                    _neck = _prop(_props, ['NECK', 'SIZE (NECK)'])
                    _face = _prop(_props, ['FACE SIZE', 'MODULE SIZE', 'MODULE', 'NOMINAL SIZE', 'FACE'])
                    _duct = _prop(_props, ['DUCT SIZE', 'DUCT'])
                    if not _neck:
                        _g = _prop(_props, ['SIZE'])
                        if _g and _g not in (_face, _duct):
                            _neck = _g
                    _etype = _prop(_props, ['TYPE', 'DESCRIPTION', 'SERVICE'])
                    if _etype and not any(p.startswith('type=') for p in content_parts):
                        content_parts.append(f"type={_etype}")
                    if _neck:
                        content_parts.append(f"neck={_neck}")
                    if _face:
                        content_parts.append(f"module={_face}")
                    if _duct:
                        content_parts.append(f"duct={_duct}")
            # Per-INSTANCE neck size read off the plan callout (neck_size_reader).
            # This is the real size for THIS device; flag it when low-confidence
            # so the estimator verifies instead of trusting a guess.
            if det.get('neck_size_plan'):
                _flag = ' [verify]' if det.get('neck_tier') == 'LOW' else ''
                content_parts.append(f"neck(plan)={det['neck_size_plan']}{_flag}")
            if det.get('damper_type'):                          # Rule B
                content_parts.append(f"damper={det['damper_type']}")
                n_crd += 1
            if det.get('merged_count'):                         # Rule C
                content_parts.append(
                    f"merged from {det['merged_count']} parts, "
                    f"face_length≈{det['face_length_px'] * px_to_pt:.1f} pt"
                )
                n_merged_runs += 1
            content_parts.append(f"({conf})")
            contents = ' · '.join(content_parts)

            # 2. Set human-facing fields
            annot.set_info(
                title=author,        # /T
                subject=subj,        # /Subj (PyMuPDF naming) — the count subject
                content=contents,    # /Contents — shows on hover
            )
            if tag_for_count:
                written_by_tag[tag_for_count] += 1

            # 3. Colors — stroke (border) + fill (interior)
            r, g, b = _parse_color(color)
            annot.set_colors(stroke=(r, g, b), fill=(r, g, b))
            annot.set_opacity(0.5)
            annot.update()

            # 4. Raw PDF dict patches Bluebeam needs to recognize this as a
            #    PolygonCount measurement (PyMuPDF doesn't expose these).
            #    NOTE: do NOT overwrite /Subj here — set_info() above already
            #    wrote the toolbox subject to it, and that's what the
            #    correction-loop parser reads.
            xref = annot.xref
            doc.xref_set_key(xref, 'IT', '/PolygonCount')

            # Bluebeam custom-column values so the Markup List columns populate
            # (BRAND/MODEL/NECK/TYPE/MOUNTING/MODULE/DUCT/CFM) — same as the
            # team's takeoff. Label = the tag (Bluebeam 'Label' column).
            if det_tag:
                _p = vars_by_tag.get(_canon(det_tag)) or {}
                _brand = _prop(_p, ['MANUFACTURER', 'BRAND', 'MAKE'])
                _model = _prop(_p, ['MODEL NUMBER', 'MODEL'])
                _bm = _prop(_p, ['MANUFACTURER & MODEL', 'MAKE / MODEL', 'MAKE/MODEL'])
                if _bm and not _brand:
                    _parts = _bm.split(' / ') if ' / ' in _bm else _bm.split(' ', 1)
                    _brand = _parts[0]
                    _model = _model or (_parts[1] if len(_parts) > 1 else '')
                _facev = _prop(_p, ['FACE SIZE', 'MODULE SIZE', 'MODULE', 'NOMINAL SIZE', 'FACE'])
                _ductv = _prop(_p, ['DUCT SIZE', 'DUCT'])
                _neckv = det.get('neck_size_plan') or _prop(_p, ['NECK', 'SIZE (NECK)'])
                if not _neckv:
                    _gs = _prop(_p, ['SIZE'])
                    if _gs and _gs not in (_facev, _ductv):
                        _neckv = _gs
                _typev = _prop(_p, ['TYPE', 'DESCRIPTION', 'SERVICE'])
                _mountv = _prop(_p, ['MOUNTING', 'MOUNT'])
                if not _mountv and _typev:    # derive mounting like write_excel does
                    _tu = _typev.upper()
                    if 'DUCT' in _tu:
                        _mountv = 'DUCT'
                    elif 'SIDEWALL' in _tu:
                        _mountv = 'SURFACE'
                    elif any(k in _tu for k in ('LAY-IN', 'LAY IN', 'T-BAR', 'T BAR')):
                        _mountv = 'LAY-IN'
                vals = {
                    'model': _model, 'brand': _brand, 'neck': _neckv,
                    'type': _typev, 'mounting': _mountv,
                    'cfm': _prop(_p, ['CFM']), 'duct': _ductv, 'module': _facev,
                    'accessories': _prop(_p, ['ACCESSOR', 'MATERIAL']),
                    'damper': det.get('damper_type', ''),
                }
                try:
                    doc.xref_set_key(xref, 'BSIColumnData', _bsi_column_data(vals))
                    doc.xref_set_key(xref, 'Label', f'({_pdf_str_escape(det_tag)})')
                except Exception:
                    pass

            written_by_subject[subj] += 1

    # Restore original page rotations so viewers display the page correctly.
    # All annotations were written while rotation was 0, in mediabox coords;
    # restoring rotation makes viewers re-apply the rotation to the whole
    # page (content + annotations) for display.
    for pno, rot in saved_rotations.items():
        if rot != 0:
            doc[pno].set_rotation(rot)

    # Make sure output dir exists, save
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_pdf), garbage=3, deflate=True)
    doc.close()

    return {
        'stamps_written': sum(written_by_subject.values()),
        'by_subject': dict(written_by_subject),
        'by_tag': dict(written_by_tag),
        'fsd_op_count': n_fsd_op,
        'crd_count': n_crd,
        'merged_runs': n_merged_runs,
        'skipped_unmapped': dict(skipped_unmapped),
        'skipped_no_match': skipped_no_match,
        'skipped_by_page_filter': n_skipped_by_page_filter,
        'page_classifications': skipped_pages_meta,
    }


# ---------- round-trip verification ----------

def verify_roundtrip(stamped_pdf: Path) -> dict:
    """Re-read the stamps we wrote with the same code the correction loop
    uses (bluebeam_to_yolo.extract_annotations). Proves the format is right."""
    repo_root = REPO_ROOT
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from bluebeam_to_yolo import extract_annotations  # noqa: E402

    by_class = Counter()
    total = 0
    for a in extract_annotations(stamped_pdf):
        by_class[a['class']] += 1
        total += 1
    return {'total_read_back': total, 'by_class': dict(by_class)}


# ---------- CLI ----------

def _resolve_job_paths(job_id: str) -> tuple[Path, Path, Path]:
    """Given a job id, locate input PDF + detections.json + propose output path."""
    job_dir = DATA_DIR / 'jobs' / job_id
    if not job_dir.is_dir():
        raise FileNotFoundError(f'no job dir at {job_dir}')

    inputs = list((job_dir / 'inputs').glob('*.pdf'))
    if not inputs:
        raise FileNotFoundError(f'no input PDF under {job_dir/"inputs"}')
    input_pdf = inputs[0]

    dets = list(job_dir.glob('*_detections.json'))
    if not dets:
        raise FileNotFoundError(f'no *_detections.json in {job_dir}')
    detections_json = dets[0]

    output_pdf = job_dir / f'{input_pdf.stem}_bluebeam_stamped.pdf'
    return input_pdf, detections_json, output_pdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', help='Job id (looks up paths in saas/data/jobs/)')
    ap.add_argument('--pdf', help='Input PDF (alternative to --job)')
    ap.add_argument('--detections', help='Detections JSON (alternative to --job)')
    ap.add_argument('--out', help='Output PDF (alternative to --job)')
    ap.add_argument('--no-verify', action='store_true',
                    help='Skip the round-trip verification step')
    args = ap.parse_args()

    if args.job:
        input_pdf, dets_json, out_pdf = _resolve_job_paths(args.job)
    elif args.pdf and args.detections and args.out:
        input_pdf = Path(args.pdf)
        dets_json = Path(args.detections)
        out_pdf = Path(args.out)
    else:
        ap.error('Provide either --job or all of --pdf/--detections/--out')

    print(f'Input PDF:     {input_pdf}')
    print(f'Detections:    {dets_json}')
    print(f'Output PDF:    {out_pdf}')
    print()
    print('Writing stamps...')
    summary = write_stamps(input_pdf, dets_json, out_pdf)
    print()
    print(f'  Stamps written: {summary["stamps_written"]}')
    for subj, n in sorted(summary['by_subject'].items(), key=lambda x: -x[1]):
        print(f'    {n:4d}  {subj}')
    if summary.get('by_tag'):
        print()
        print(f'  Context tags applied:')
        for t, n in summary['by_tag'].items():
            print(f'    {n:4d}  {t}')
    if summary.get('fsd_op_count'):
        print(f'  Rule A — FSD-OP (with-GRD): {summary["fsd_op_count"]}')
    if summary.get('crd_count'):
        print(f'  Rule B — CRD damper-type tagged: {summary["crd_count"]}')
    if summary.get('merged_runs'):
        print(f'  Rule C — continuous linear runs merged: {summary["merged_runs"]}')
    if summary['skipped_unmapped']:
        print(f'  Skipped UNMAPPED classes:')
        for cls, n in summary['skipped_unmapped'].items():
            print(f'    {n:4d}  {cls}')
    if summary['skipped_no_match']:
        print(f'  Skipped (OTHER MECHANICAL / no toolbox match): {summary["skipped_no_match"]}')

    if not args.no_verify:
        print()
        print('Round-trip verification (read our stamps back with the correction-loop parser)...')
        rt = verify_roundtrip(out_pdf)
        print(f'  Total stamps read back: {rt["total_read_back"]}')
        written_n = summary['stamps_written']
        read_n = rt['total_read_back']
        if read_n == written_n:
            print(f'  Matches written count:  YES')
        else:
            print(f'  Matches written count:  NO ({written_n - read_n} missing)')
        print(f'  By class:')
        for cls, n in sorted(rt['by_class'].items(), key=lambda x: -x[1]):
            print(f'    {n:4d}  {cls}')

    print()
    print(f'Done. Open in Bluebeam:')
    print(f'  {out_pdf}')


if __name__ == '__main__':
    main()
