"""
verify_pipeline.py — pre-flight readiness check for ANY new PDF

Drops you a structured report on whether a PDF will process cleanly through
the full takeoff pipeline, WITHOUT running the slow YOLO inference. Useful
for:
  - Triage when a new project lands ("can we even process this?")
  - SaaS UI pre-flight ("show readiness before user commits to 5 min")
  - CI/regression check ("did our last change break a known-good PDF?")

Runs each pre-YOLO stage in dry-run mode:
  1. PDF metadata    — readable, page count, file size, rotation
  2. Title block     — extract_project_info on page 0
  3. Sheet filter    — full survey, classify M-series pages (incl. OCR
                       fallback on raster pages)
  4. Schedule parser — only on the M-series pages found in (3)
  5. Auto-scale      — per-page scale detection

Outputs:
  Console: pass/fail per stage + readiness verdict + estimated runtime
  JSON   : <pdf-stem>_readiness.json sidecar with structured findings

Usage:
    python verify_pipeline.py "<plan.pdf>"
    python verify_pipeline.py "<plan.pdf>" --json-only
    python verify_pipeline.py "<plan.pdf>" --no-ocr        (faster, skip raster-page OCR fallback)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

import fitz

# Lazy imports so this script remains usable even if some optional deps
# (easyocr, pdfplumber) aren't installed — we'll surface that as a stage
# failure rather than crashing.


# ---- Stage runners ----

def _stage_pdf_metadata(pdf_path: Path) -> dict:
    t0 = time.time()
    try:
        doc = fitz.open(str(pdf_path))
        info = {
            'ok': True,
            'page_count': doc.page_count,
            'size_mb': round(pdf_path.stat().st_size / 1024 / 1024, 2),
            'pdf_format': doc.metadata.get('format'),
            'creator': doc.metadata.get('creator'),
            'rotation_per_page': [doc[i].rotation for i in range(doc.page_count)],
        }
        doc.close()
        info['elapsed_s'] = round(time.time() - t0, 2)
        return info
    except Exception as e:
        return {'ok': False, 'error': str(e), 'elapsed_s': round(time.time() - t0, 2)}


def _stage_title_block(pdf_path: Path) -> dict:
    t0 = time.time()
    try:
        from takeoff_cli import extract_project_info
        info = extract_project_info(pdf_path) or {}
        useful_keys = [k for k in info if k != '_error' and info[k]]
        return {
            'ok': len(useful_keys) > 0,
            'fields_found': useful_keys,
            'all_fields': info,
            'elapsed_s': round(time.time() - t0, 2),
        }
    except Exception as e:
        return {'ok': False, 'error': str(e), 'elapsed_s': round(time.time() - t0, 2)}


def _stage_sheet_filter(pdf_path: Path, use_ocr: bool) -> dict:
    t0 = time.time()
    try:
        from sheet_filter import survey_summary, is_m_series
        summary = survey_summary(pdf_path)
        survey = summary['survey']
        m_plan = summary['m_plan_pages']
        m_series = summary['m_series_pages']
        # Count discipline distribution
        disc_counts: dict[str, int] = {}
        ocr_recoveries = 0
        for s in survey:
            disc = s.discipline or '?'
            disc_counts[disc] = disc_counts.get(disc, 0) + 1
            if 'via ocr' in (s.reason or ''):
                ocr_recoveries += 1
        return {
            'ok': len(m_plan) > 0,
            'page_count': len(survey),
            'm_series_pages': [p + 1 for p in m_series],
            'm_plan_pages': [p + 1 for p in m_plan],
            'discipline_distribution': disc_counts,
            'ocr_recoveries': ocr_recoveries,
            'elapsed_s': round(time.time() - t0, 2),
        }
    except Exception as e:
        return {'ok': False, 'error': str(e), 'elapsed_s': round(time.time() - t0, 2)}


def _stage_schedule_parser(pdf_path: Path, m_series_pages: Optional[list]) -> dict:
    t0 = time.time()
    try:
        from schedule_parser import parse_pdf_schedules
        schedules, marks, mark_details, legend, summary, variables = parse_pdf_schedules(
            str(pdf_path),
            pages=m_series_pages,
        )
        out = {
            'ok': len(variables) > 0,
            'tables_found': len(schedules),
            'unique_tags': len(marks),
            'variables': len(variables),
            'legend_items': len(legend),
            'sample_tags': marks[:10],
            'elapsed_s': round(time.time() - t0, 2),
        }
        if len(schedules) > 0 and len(variables) == 0:
            out['warning'] = ('schedule tables found but no variables extracted — '
                              'likely non-English headers or outlined CAD text')
        return out
    except Exception as e:
        return {'ok': False, 'error': str(e), 'elapsed_s': round(time.time() - t0, 2)}


def _stage_auto_scale(pdf_path: Path) -> dict:
    t0 = time.time()
    try:
        from auto_scale import best_scale_for_page
        doc = fitz.open(str(pdf_path))
        scales_per_page = []
        for pno in range(doc.page_count):
            res = best_scale_for_page(doc[pno], dpi=200)
            scales_per_page.append({
                'page': pno + 1,
                'kind': res['kind'] if res else 'not_found',
                'scale_text': res.get('scale_text') if res else None,
            })
        doc.close()
        found = sum(1 for s in scales_per_page if s['kind'] not in ('not_found', 'no_scale'))
        unique_scales = sorted({s['scale_text'] for s in scales_per_page if s['scale_text']})
        return {
            'ok': found > 0,
            'pages_with_scale': found,
            'total_pages': len(scales_per_page),
            'unique_scales': unique_scales,
            'elapsed_s': round(time.time() - t0, 2),
        }
    except Exception as e:
        return {'ok': False, 'error': str(e), 'elapsed_s': round(time.time() - t0, 2)}


# ---- Verdict + runtime estimate ----

def _runtime_estimate(stages: dict) -> dict:
    """Rough projection for the full pipeline based on what survey found."""
    sf = stages.get('sheet_filter', {})
    n_plan = len(sf.get('m_plan_pages', []) or [])
    # Empirical: ~8 s/page YOLO on CPU at imgsz=1280, ~0.5–1 s/det for L2b OCR
    yolo_s = n_plan * 8
    # OCR fallback in sheet_filter for raster pages costs ~1s each
    ocr_s = sf.get('ocr_recoveries', 0) * 1.5
    # Schedule parse already measured
    sched_s = stages.get('schedule_parser', {}).get('elapsed_s', 0)
    # Rough multiplier for tag_inference + Excel writing
    other_s = 20
    total = yolo_s + ocr_s + sched_s + other_s
    return {
        'estimated_total_seconds': round(total),
        'estimated_total_minutes': round(total / 60, 1),
        'breakdown': {
            'yolo_inference_s': yolo_s,
            'sheet_filter_ocr_s': ocr_s,
            'schedule_parser_s': sched_s,
            'tag_inference_and_output_s': other_s,
        },
    }


def _verdict(stages: dict) -> dict:
    pdf = stages.get('pdf_metadata', {})
    sf = stages.get('sheet_filter', {})
    sch = stages.get('schedule_parser', {})
    tb = stages.get('title_block', {})

    blockers = []
    warnings = []

    if not pdf.get('ok'):
        blockers.append('PDF unreadable')
    if not sf.get('ok'):
        if pdf.get('ok'):
            blockers.append('no M-series plan pages found (filter fell all the way back, pipeline will run on all pages)')
        else:
            blockers.append('sheet filter failed')
    if not sch.get('ok'):
        warnings.append('schedule parser extracted 0 variables — Excel will lack brand/model/size')
    if not tb.get('ok'):
        warnings.append('title block not parsed — project metadata will be missing')

    if blockers:
        status = 'WILL_LIKELY_FAIL'
    elif warnings:
        status = 'PROCEED_WITH_WARNINGS'
    else:
        status = 'READY'

    return {
        'status': status,
        'blockers': blockers,
        'warnings': warnings,
    }


# ---- Output formatters ----

def _print_console(report: dict):
    pdf = report['stages']['pdf_metadata']
    print('=' * 72)
    print(f'PIPELINE READINESS — {report["pdf"]}')
    print('=' * 72)
    print()

    def line(label, ok, body):
        flag = 'OK  ' if ok else 'FAIL'
        print(f'  [{flag}]  {label:25s}  {body}')

    line('PDF metadata', pdf.get('ok', False),
         f'{pdf.get("page_count", "?")} pages, {pdf.get("size_mb", "?")} MB')

    tb = report['stages']['title_block']
    line('Title block', tb.get('ok', False),
         f'fields: {tb.get("fields_found", [])}' if tb.get('ok') else 'no fields parsed')

    sf = report['stages']['sheet_filter']
    if sf.get('ok'):
        line('Sheet filter', True,
             f'{len(sf["m_series_pages"])} M-series / {sf["page_count"]} total, '
             f'{len(sf["m_plan_pages"])} plan; {sf["ocr_recoveries"]} via OCR')
    else:
        line('Sheet filter', False, sf.get('error', '?'))

    sch = report['stages']['schedule_parser']
    if sch.get('ok'):
        line('Schedule parser', True,
             f'{sch["tables_found"]} tables, {sch["unique_tags"]} tags, {sch["variables"]} variables')
    else:
        body = f'{sch.get("tables_found", 0)} tables, 0 variables'
        if sch.get('warning'):
            body += f' — {sch["warning"]}'
        line('Schedule parser', False, body)

    asc = report['stages']['auto_scale']
    if asc.get('ok'):
        line('Auto-scale', True,
             f'{asc["pages_with_scale"]}/{asc["total_pages"]} pages, scales: {asc["unique_scales"][:3]}')
    else:
        line('Auto-scale', False, 'no scale text detected on any page')

    rt = report['estimate']
    print()
    print(f'  Estimated full-pipeline runtime: {rt["estimated_total_minutes"]} min  '
          f'({rt["breakdown"]})')

    v = report['verdict']
    print()
    print(f'  STATUS: {v["status"]}')
    if v['blockers']:
        print('  Blockers:')
        for b in v['blockers']:
            print(f'    - {b}')
    if v['warnings']:
        print('  Warnings:')
        for w in v['warnings']:
            print(f'    - {w}')
    print()


# ---- Main ----

def run(pdf_path: Path, use_ocr: bool = True) -> dict:
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        return {
            'pdf': str(pdf_path),
            'ok': False,
            'error': 'file not found',
        }

    stages: dict = {}

    stages['pdf_metadata'] = _stage_pdf_metadata(pdf_path)
    if not stages['pdf_metadata'].get('ok'):
        # Can't do anything else if PDF is unreadable
        return {
            'pdf': str(pdf_path),
            'stages': stages,
            'verdict': _verdict(stages),
            'estimate': {'estimated_total_seconds': 0, 'estimated_total_minutes': 0, 'breakdown': {}},
        }

    stages['title_block'] = _stage_title_block(pdf_path)
    stages['sheet_filter'] = _stage_sheet_filter(pdf_path, use_ocr)

    # Pass the M-series page list to the schedule parser to mirror production
    m_series = stages['sheet_filter'].get('m_series_pages') or []
    # Convert 1-indexed back to 0-indexed for the parser
    m_series_idx = [p - 1 for p in m_series] if m_series else None
    stages['schedule_parser'] = _stage_schedule_parser(pdf_path, m_series_idx)

    stages['auto_scale'] = _stage_auto_scale(pdf_path)

    return {
        'pdf': str(pdf_path),
        'stages': stages,
        'verdict': _verdict(stages),
        'estimate': _runtime_estimate(stages),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf', help='Path to PDF to check')
    ap.add_argument('--json-only', action='store_true',
                    help='Suppress console report, print JSON to stdout instead')
    ap.add_argument('--no-ocr', action='store_true',
                    help='Skip OCR fallback in sheet_filter (faster, less complete)')
    ap.add_argument('--output', default=None,
                    help='Path for the JSON sidecar (default: alongside the PDF)')
    args = ap.parse_args()

    report = run(Path(args.pdf), use_ocr=not args.no_ocr)

    out_path = (Path(args.output) if args.output else
                Path(args.pdf).with_name(Path(args.pdf).stem + '_readiness.json'))
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')

    if args.json_only:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_console(report)
        print(f'  JSON report saved to: {out_path}')

    # Exit code reflects verdict for CI use
    verdict = report.get('verdict', {}).get('status')
    if verdict == 'WILL_LIKELY_FAIL':
        sys.exit(2)
    elif verdict == 'PROCEED_WITH_WARNINGS':
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
