"""
Part 1 — cross-corpus page-selection diagnostic.

Runs the CURRENT page-understanding logic (sheet_filter sheet-number read +
page_classifier content read) over every PDF in saas/data/jobs/ and prints,
per page: the sheet number/discipline read, whether sheet_filter calls it a
plan, the content classifier's type, and where the two DISAGREE — the blind
spots we need the fused classifier to resolve.

Purpose: see how today's Part 1 behaves across many engineers' drawing styles
(not one PDF), and produce the raw material for a page-selection benchmark.

Usage:
    python part1_page_diagnostic.py                 # all jobs, with OCR
    python part1_page_diagnostic.py --no-ocr        # faster, text-layer only
    python part1_page_diagnostic.py --no-content    # skip page_classifier
    python part1_page_diagnostic.py --job d52f773ae2b8
    python part1_page_diagnostic.py --glob "saas/data/jobs/*/inputs/*.pdf"
"""
import sys, io, argparse, glob, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'saas' / 'backend'))


def run_job(pdf_path: str, use_ocr: bool, use_content: bool) -> dict:
    from sheet_filter import survey_pdf, is_m_series
    survey = survey_pdf(pdf_path, use_ocr=use_ocr)
    content = {}
    if use_content:
        try:
            from page_classifier import classify_pdf, NON_PLAN_TYPES
            for c in classify_pdf(Path(pdf_path)):
                content[c.page] = c
        except Exception as e:
            print(f"   (page_classifier failed: {e})")

    rows = []
    for s in survey:
        pg = s.page_idx + 1
        c = content.get(pg)
        sf_plan = is_m_series(s.discipline) and s.is_plan
        # the two signals' plan verdicts, for disagreement detection
        cc_plan = (c.is_plan if c else None)
        cc_type = (c.type if c else '-')
        disagree = (c is not None and is_m_series(s.discipline) and (sf_plan != bool(cc_plan)))
        rows.append({
            'page': pg, 'num': s.sheet_number or '-', 'disc': s.discipline or '-',
            'sf_plan': sf_plan, 'm_series': is_m_series(s.discipline),
            'cc_type': cc_type, 'cc_plan': cc_plan, 'disagree': disagree,
            'reason': s.reason,
        })
    return {'rows': rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-ocr', action='store_true')
    ap.add_argument('--no-content', action='store_true')
    ap.add_argument('--job', default=None, help='single job id under saas/data/jobs')
    ap.add_argument('--glob', default='saas/data/jobs/*/inputs/*.pdf')
    args = ap.parse_args()

    if args.job:
        pdfs = sorted(glob.glob(f'saas/data/jobs/{args.job}/inputs/*.pdf'))
    else:
        pdfs = sorted(glob.glob(args.glob))
    if not pdfs:
        print('No PDFs found.'); return

    print(f"Part 1 diagnostic — {len(pdfs)} PDF(s) | ocr={not args.no_ocr} content={not args.no_content}\n")
    agg = {'pages': 0, 'kept': 0, 'disagree': 0, 'no_number': 0, 'non_m_disc': 0}
    for pdf in pdfs:
        name = Path(pdf).name
        print('=' * 78)
        print(f"{Path(pdf).parts[-3]}  ::  {name}")
        print('-' * 78)
        t = time.time()
        try:
            res = run_job(pdf, use_ocr=not args.no_ocr, use_content=not args.no_content)
        except Exception as e:
            print(f"  FAILED: {e}\n"); continue
        print(f"{'pg':>3} {'number':>10} {'disc':>5} {'sf_plan':>7} {'content':>10} {'cc_plan':>7} {'flag':>5}")
        for r in res['rows']:
            flag = '!!' if r['disagree'] else ''
            ccp = {True: 'plan', False: 'no', None: '-'}[r['cc_plan']]
            print(f"{r['page']:>3} {r['num']:>10} {r['disc']:>5} "
                  f"{('YES' if r['sf_plan'] else '.'):>7} {r['cc_type']:>10} {ccp:>7} {flag:>5}")
            agg['pages'] += 1
            if r['sf_plan']: agg['kept'] += 1
            if r['disagree']: agg['disagree'] += 1
            if r['num'] == '-': agg['no_number'] += 1
            if r['m_series'] is False and r['num'] != '-': agg['non_m_disc'] += 1
        kept = sum(1 for r in res['rows'] if r['sf_plan'])
        dis = sum(1 for r in res['rows'] if r['disagree'])
        print(f"  -> {kept} plan page(s) kept for detection; {dis} signal-disagreement(s)  ({time.time()-t:.0f}s)\n")

    print('=' * 78)
    print(f"TOTALS: {agg['pages']} pages | {agg['kept']} kept as plans | "
          f"{agg['disagree']} disagreements | {agg['no_number']} no-number reads | "
          f"{agg['non_m_disc']} non-M disciplines")


if __name__ == '__main__':
    main()
