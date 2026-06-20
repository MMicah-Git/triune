"""
benchmark_page_selection.py — score Part 1 page selection across drawing styles.

Confidence bar (see ARCHITECTURE_PARTS.md):
  - plan-page RECALL must be ~100%  (a dropped plan loses ALL its equipment)
  - precision should be high (>=~0.85) but over-keeping is the SAFE failure

Ground truth:
  Each page's "is this a mechanical plan we must run detection on?" label.
  We bootstrap a PROVISIONAL label from the sheet's own TITLE BLOCK (the sheet
  declares what it is) + discipline, and mark anything we can't read as
  'uncertain' (excluded from scoring, listed for you to label). Human
  corrections live in page_selection_gt.json and OVERRIDE the provisional label
  — that's what makes the number authoritative.

Usage:
    python benchmark_page_selection.py                 # all unique PDFs, OCR on
    python benchmark_page_selection.py --no-ocr
    python benchmark_page_selection.py --only "Union,MPAGES,Pacific"
    python benchmark_page_selection.py --write-template gt_template.json
"""
from __future__ import annotations
import sys, io, argparse, glob, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'saas' / 'backend'))

import fitz
import page_selector
from sheet_filter import detect_sheet, M_DISCIPLINES, KNOWN_DISCIPLINES

GT_FILE = ROOT / 'page_selection_gt.json'

# Title markers → ground-truth "this sheet is NOT a plan to detect on".
NON_PLAN_TITLE = ('SCHEDULE', 'LEGEND', 'DETAIL', 'NOTE', 'COVER', 'SYMBOL',
                  'ABBREVIATION', 'SPECIFICATION', 'RISER', 'DIAGRAM',
                  'SCHEMATIC', 'ISOMETRIC', 'INDEX', 'TITLE SHEET')


def gt_from_sheet(page, sf) -> tuple[object, str]:
    """Provisional ground-truth label from a page + its already-read sheet (sf).

    Discipline comes from sf (the single detect_sheet/OCR read). The title comes
    from doc_verification.read_title_block — a stronger title-block parser than
    sheet_filter's sheet_title (which is blank on many sheets). read_title_block
    is text-layer only (cheap, no OCR), so calling it here doesn't re-run OCR.
    Returns (True | False | None-uncertain, source-string)."""
    disc = (sf.discipline or '').upper()
    # Confident non-mechanical trade → not a mechanical plan, full stop.
    if disc in KNOWN_DISCIPLINES and disc not in M_DISCIPLINES:
        return False, f'discipline {disc} (non-mechanical)'
    title = ''
    try:
        from doc_verification import read_title_block
        title = (read_title_block(page) or {}).get('title', '') or ''
    except Exception:
        pass
    t = title.upper()
    if t:
        for m in NON_PLAN_TITLE:
            if m in t:
                return False, f'title "{title}" -> non-plan'
        if 'PLAN' in t:
            return True, f'title "{title}" -> plan'
    return None, f'uncertain (title="{title}")'


def unique_pdfs(only=None, exclude=None) -> list[str]:
    seen, out = {}, []
    for p in sorted(glob.glob('saas/data/jobs/*/inputs/*.pdf')):
        name = Path(p).name
        if name in seen:
            continue
        if only and not any(tok.lower() in name.lower() for tok in only):
            continue
        if exclude and any(tok.lower() in name.lower() for tok in exclude):
            continue
        seen[name] = p
        out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-ocr', action='store_true')
    ap.add_argument('--only', default=None, help='comma-separated filename substrings')
    ap.add_argument('--exclude', default=None, help='comma-separated filename substrings to skip')
    ap.add_argument('--write-template', default=None)
    args = ap.parse_args()
    only = [s.strip() for s in args.only.split(',')] if args.only else None
    exclude = [s.strip() for s in args.exclude.split(',')] if args.exclude else None

    overrides = {}
    if GT_FILE.exists():
        try:
            overrides = json.loads(GT_FILE.read_text(encoding='utf-8'))
            print(f"(loaded human ground-truth overrides for {len(overrides)} PDF(s))\n")
        except Exception as e:
            print(f"(could not read {GT_FILE.name}: {e})\n")

    pdfs = unique_pdfs(only, exclude)
    print(f"Benchmark — {len(pdfs)} unique PDF(s) | ocr={not args.no_ocr}\n")

    template = {}
    agg = {'tp': 0, 'fp': 0, 'fn': 0, 'uncertain': 0, 'scored_pdfs': 0}
    for pdf in pdfs:
        name = Path(pdf).name
        ov = overrides.get(name, {})
        print('=' * 78)
        print(name)
        t0 = time.time()
        try:
            # Read each page ONCE: detect_sheet (the OCR step) + content
            # classify, then derive BOTH the picker verdict and the answer-key
            # label from that single read.
            doc = fitz.open(pdf)
            n = doc.page_count
            verdicts, gt = [], {}
            for i in range(n):
                sf = detect_sheet(doc[i], i, use_ocr=not args.no_ocr)
                cc = page_selector._classify_content(pdf, i)
                verdicts.append(page_selector.decide_page(i, sf, cc))
                lbl, src = gt_from_sheet(doc[i], sf)
                if str(i + 1) in ov:            # human override wins
                    lbl, src = bool(ov[str(i + 1)]), 'human override'
                gt[i + 1] = (lbl, src)
            doc.close()
        except Exception as e:
            print(f"  FAILED: {e}\n"); continue

        template[name] = {}
        misses, extras, unc = [], [], []
        tp = fp = fn = 0
        for v in verdicts:
            pg = v['page']
            kept = v['is_plan']
            lbl, src = gt.get(pg, (None, ''))
            template[name][str(pg)] = (None if lbl is None else bool(lbl))
            if lbl is None:
                unc.append(pg); continue
            if lbl and kept: tp += 1
            elif lbl and not kept: fn += 1; misses.append(pg)
            elif (not lbl) and kept: fp += 1; extras.append(pg)
        recall = tp / (tp + fn) if (tp + fn) else float('nan')
        prec = tp / (tp + fp) if (tp + fp) else float('nan')
        agg['tp'] += tp; agg['fp'] += fp; agg['fn'] += fn; agg['uncertain'] += len(unc)
        if (tp + fn) > 0: agg['scored_pdfs'] += 1
        print(f"  recall={recall:.2f}  precision={prec:.2f}  "
              f"(tp={tp} fp={fp} fn={fn}, uncertain={len(unc)})  ({time.time()-t0:.0f}s)")
        if misses:
            print(f"  ⚠ DROPPED REAL PLANS (pages): {misses}")
        if extras:
            print(f"    over-kept non-plans (pages): {extras}")
        if unc:
            print(f"    uncertain (label these in {GT_FILE.name}): {unc}")
        print()

    if args.write_template:
        Path(args.write_template).write_text(json.dumps(template, indent=2), encoding='utf-8')
        print(f"Wrote GT template → {args.write_template} "
              f"(edit, set true/false, save as {GT_FILE.name})")

    R = agg['tp'] / (agg['tp'] + agg['fn']) if (agg['tp'] + agg['fn']) else float('nan')
    P = agg['tp'] / (agg['tp'] + agg['fp']) if (agg['tp'] + agg['fp']) else float('nan')
    print('=' * 78)
    print(f"AGGREGATE over {agg['scored_pdfs']} scored PDF(s): "
          f"plan-page RECALL={R:.3f}  PRECISION={P:.3f}")
    print(f"  tp={agg['tp']} fp={agg['fp']} fn={agg['fn']} uncertain={agg['uncertain']}")
    print(f"  CONFIDENCE BAR: recall ~1.00 (zero dropped plans) + precision >= ~0.85")
    if agg['fn'] == 0:
        print("  ✓ zero real plans dropped across the scored corpus")
    else:
        print(f"  ✗ {agg['fn']} real plan page(s) dropped — investigate the misses above")


if __name__ == '__main__':
    main()
