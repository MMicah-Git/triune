"""
page_selector.py — Part 1's fused page decision.

Replaces the two stacked filters (sheet_filter sheet-number read THEN
page_classifier content read, with brittle subordination) with ONE per-page
verdict that fuses both signals and is robust across engineers' drawing styles.

Why fuse (evidence from the cross-corpus diagnostic):
  - The sheet-number SERIES rule ("M5xx+ = details, M0xx = cover") silently
    DROPS real plans for engineers who number plans M5.x / M8.x / M0.0.
  - Pages whose title block won't OCR get NO number and were dropped even when
    the content is obviously a floor plan (Union 888: 6 real plans lost).
  - Meanwhile a schedule numbered in the plan range (Pacific M-400) was KEPT
    and produced phantoms.

The rule that resolves all of those:
  1. DISCIPLINE GATE — only mechanical (M-family) sheets are detection
     candidates. If the discipline is unknown (number unread), defer to content.
  2. KEEP-UNLESS-DOC — keep a mechanical candidate for detection UNLESS the
     content classifier CONFIDENTLY says it is a non-drawing sheet
     (schedule / legend / notes / cover / details). Cost-asymmetry: dropping a
     real plan loses ALL its equipment; keeping a borderline page only risks a
     few phantoms that reconciliation/QA already catch.
  The number SERIES becomes evidence only — never an authoritative drop.

Output per page: {page, sheet_number, discipline, type, is_plan, confidence,
                  evidence[], reason}. is_plan == True ⇒ run YOLO on this page.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'saas' / 'backend') not in sys.path:
    sys.path.insert(0, str(ROOT / 'saas' / 'backend'))

import fitz
from sheet_filter import detect_sheet, M_DISCIPLINES, KNOWN_DISCIPLINES, plan_by_sheet_number

PLAN_CONTENT_TYPES = {'mechanical_plan', 'roof_plan'}
# Non-drawing sheet types — confidently one of these ⇒ no equipment to count.
DOC_CONTENT_TYPES = {'schedule', 'legend', 'notes', 'cover', 'details'}
# Below this, a content "doc" verdict isn't trusted enough to drop a page.
DOC_DROP_CONFIDENCE = 0.70


def _classify_content(pdf_path, page_index, detection_count=None):
    """page_classifier.classify_page, or None if unavailable."""
    try:
        from page_classifier import classify_page
        return classify_page(Path(pdf_path), page_index, detection_count=detection_count)
    except Exception:
        return None


def classify_page_fused(pdf_path, page_index, use_ocr=True, detection_count=None) -> dict:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        sf = detect_sheet(page, page_index, use_ocr=use_ocr)
    finally:
        doc.close()
    cc = _classify_content(pdf_path, page_index, detection_count=detection_count)
    return decide_page(page_index, sf, cc)


def decide_page(page_index, sf, cc) -> dict:
    """Pure fusion decision from a pre-read sheet (sf) + content (cc).

    Split out so callers (e.g. the benchmark) can read each page ONCE and reuse
    the result, instead of re-running the OCR'd sheet read.
    """
    disc = (sf.discipline or '').upper()
    num = sf.sheet_number
    cc_type = cc.type if cc else None
    cc_conf = cc.confidence if cc else 0.0
    series_plan, series_reason = plan_by_sheet_number(num)
    evidence = []

    # ── 1. Discipline gate ────────────────────────────────────────────────
    if disc in M_DISCIPLINES:
        mechanical = True
        evidence.append(f'discipline={disc} (mechanical)')
    elif disc in KNOWN_DISCIPLINES:
        mechanical = False
        evidence.append(f'discipline={disc} (other trade)')
    else:
        # No / unrecognized sheet number → defer to content. Keep as a
        # mechanical candidate if the content looks like a plan OR the
        # non-plan verdict is only weakly held. A schedule-SATURATED floor
        # plan (e.g. PNC page 2, Busy Bees M101) often misreads as a
        # low-confidence 'schedule', and its title block won't OCR so the
        # number reads as an equipment tag (AHU-1, RTU-2). Dropping it loses
        # the ENTIRE plan's equipment — so only a CONFIDENT doc verdict
        # (>= DOC_DROP_CONFIDENCE) may disqualify an unreadable-number page;
        # otherwise cost-asymmetry says keep and let reconciliation/QA catch
        # any phantoms. Real schedule/legend sheets still read as high-conf
        # docs and are dropped below.
        confident_doc = cc_type in DOC_CONTENT_TYPES and cc_conf >= DOC_DROP_CONFIDENCE
        mechanical = (cc_type in PLAN_CONTENT_TYPES) or (not confident_doc)
        evidence.append(f'no-readable-number; content={cc_type or "?"}')

    if cc_type:
        evidence.append(f'content={cc_type} (conf {cc_conf:.2f})')
    if num:
        evidence.append(f'number={num}')
    if series_plan is not None:
        evidence.append(series_reason + ' [advisory]')

    if not mechanical:
        return _verdict(page_index, sf, cc_type or 'non_plan', False, 0.85,
                        evidence, 'not a mechanical sheet')

    # ── 2. Keep-unless-confident-non-drawing ──────────────────────────────
    if cc_type in DOC_CONTENT_TYPES and cc_conf >= DOC_DROP_CONFIDENCE:
        return _verdict(page_index, sf, cc_type, False, cc_conf, evidence,
                        f'mechanical but content is a {cc_type} sheet (conf {cc_conf:.2f})')

    if cc_type in PLAN_CONTENT_TYPES:
        return _verdict(page_index, sf, cc_type, True, max(0.8, cc_conf), evidence,
                        f'mechanical {cc_type}')

    # 'other', low-confidence doc, or no content read → cost-asymmetry KEEP.
    conf = 0.5
    return _verdict(page_index, sf, cc_type or 'plan', True, conf, evidence,
                    'mechanical, no confident non-drawing signal — kept (cost-asymmetry)')


def _verdict(page_index, sf, ptype, is_plan, confidence, evidence, reason) -> dict:
    return {
        'page': page_index + 1,
        'sheet_number': sf.sheet_number,
        'discipline': sf.discipline,
        'type': ptype,
        'is_plan': bool(is_plan),
        'confidence': round(float(confidence), 2),
        'evidence': evidence,
        'reason': reason,
    }


def classify_pages(pdf_path, use_ocr=True) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    n = doc.page_count
    doc.close()
    return [classify_page_fused(pdf_path, i, use_ocr=use_ocr) for i in range(n)]


def plan_page_indices(pdf_path, use_ocr=True) -> list[int]:
    """0-based page indices to run detection on."""
    return [v['page'] - 1 for v in classify_pages(pdf_path, use_ocr=use_ocr) if v['is_plan']]


if __name__ == '__main__':
    import argparse, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--no-ocr', action='store_true')
    args = ap.parse_args()
    verdicts = classify_pages(args.pdf, use_ocr=not args.no_ocr)
    print(f"{'pg':>3} {'number':>10} {'disc':>5} {'type':>16} {'keep':>5} {'conf':>5}  reason")
    for v in verdicts:
        print(f"{v['page']:>3} {str(v['sheet_number']):>10} {str(v['discipline']):>5} "
              f"{v['type']:>16} {('YES' if v['is_plan'] else '.'):>5} {v['confidence']:>5.2f}  {v['reason']}")
    kept = [v['page'] for v in verdicts if v['is_plan']]
    print(f"\n-> {len(kept)} plan page(s) kept: {kept}")
