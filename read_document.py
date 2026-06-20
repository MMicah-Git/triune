"""
read_document.py — consolidated "read everything" pass for a blueprint PDF.

Before any equipment DETECTION, read the document's knowledge layer and report
it in one place:
  - page routing      (which pages are plans / schedules / legend)   [Part 1]
  - legend            (symbol -> meaning dictionary + abbreviations)  [Part 1->3]
  - schedule          (tag -> specs: CFM, model, size, ...)           [Part 3]
  - keynotes/notes    (numbered keynotes on the sheets)               [Part 3]

This is the knowledge that should inform detection (expected classes/counts) and
that the estimator wants to see ("did the tool actually read my legend/schedule?").

Usage:
    python read_document.py "<plan.pdf>"
    python read_document.py "<plan.pdf>" --json out.json
"""
from __future__ import annotations
import sys, io, argparse, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'saas' / 'backend'))


def read_document(pdf_path: str) -> dict:
    out = {'pdf': Path(pdf_path).name}

    # ---- Part 1: page routing ------------------------------------------------
    import page_selector
    verdicts = page_selector.classify_pages(pdf_path)
    out['pages'] = {
        'total': len(verdicts),
        'plan': [v['page'] for v in verdicts if v['is_plan']],
        'by_type': {},
    }
    for v in verdicts:
        out['pages']['by_type'].setdefault(v['type'], []).append(v['page'])

    # ---- Legend: symbol dictionary + abbreviations --------------------------
    try:
        from legend_reader import extract_legend
        leg = extract_legend(Path(pdf_path))
        out['legend'] = {
            'page': leg.get('page'),
            'source': leg.get('source'),
            'abbreviations': leg.get('abbreviations', []),
            'symbols': leg.get('symbols', []),
        }
    except Exception as e:
        out['legend'] = {'error': str(e)}

    # ---- Schedule: tag -> specs ---------------------------------------------
    try:
        import takeoff_cli
        from schedule_parser import parse_pdf_schedules
        sched_pages = takeoff_cli.find_schedule_pages(pdf_path) or None
        schedules, marks, mark_details, _leg, _summ, variables = parse_pdf_schedules(
            str(pdf_path), pages=sched_pages)
        out['schedule'] = {
            'schedule_pages': [p + 1 for p in (sched_pages or [])],
            'tables': sorted({v.get('schedule_name') for v in variables if v.get('schedule_name')}),
            'tag_count': len(marks),
            'tags': marks,
            'variables': variables,
        }
    except Exception as e:
        out['schedule'] = {'error': str(e)}

    # ---- Keynotes ------------------------------------------------------------
    try:
        from keynote_extractor import extract_all_keynotes
        kn = extract_all_keynotes(Path(pdf_path))
        notes = kn.get('keynotes', kn) if isinstance(kn, dict) else kn
        out['keynotes'] = notes
    except Exception as e:
        out['keynotes'] = {'error': str(e)}

    return out


def _print(doc: dict):
    print('=' * 72)
    print(f"DOCUMENT READ — {doc['pdf']}")
    print('=' * 72)
    pg = doc['pages']
    print(f"\nPAGES ({pg['total']} total) — plan pages: {pg['plan']}")
    for t, ps in sorted(pg['by_type'].items()):
        print(f"   {t:16s}: {ps}")

    leg = doc.get('legend', {})
    print(f"\nLEGEND  (page {leg.get('page')}, source {leg.get('source')})")
    if leg.get('error'):
        print(f"   error: {leg['error']}")
    else:
        syms = leg.get('symbols', [])
        abbr = leg.get('abbreviations', [])
        print(f"   {len(syms)} symbol(s), {len(abbr)} abbreviation(s)")
        for s in syms[:12]:
            print(f"     symbol: {s}")
        if abbr:
            sample = abbr[:12] if isinstance(abbr, list) else list(abbr.items())[:12]
            print(f"     abbreviations (sample): {sample}")

    sch = doc.get('schedule', {})
    print(f"\nSCHEDULE  (pages {sch.get('schedule_pages')})")
    if sch.get('error'):
        print(f"   error: {sch['error']}")
    else:
        print(f"   tables: {sch.get('tables')}")
        print(f"   {sch.get('tag_count')} tag(s): {sch.get('tags')}")
        for v in (sch.get('variables') or [])[:6]:
            props = list((v.get('properties') or {}).items())[:4]
            print(f"     {v.get('tag')}  ({v.get('schedule_name')}): {props}")

    kn = doc.get('keynotes', {})
    n = len(kn) if isinstance(kn, (list, dict)) else 0
    print(f"\nKEYNOTES: {n if not (isinstance(kn, dict) and kn.get('error')) else kn}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--json', default=None)
    args = ap.parse_args()
    doc = read_document(args.pdf)
    _print(doc)
    if args.json:
        Path(args.json).write_text(json.dumps(doc, indent=2, default=str), encoding='utf-8')
        print(f"\nWrote {args.json}")
