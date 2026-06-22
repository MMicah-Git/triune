"""
neck_size_reader.py — attach per-instance neck/duct sizes read off the PLAN to
each air-device detection, with a confidence tier.

This is the validated approach from NECK_SIZE_READER_SCOPE.md (Slice 1/2):
  1. Size callouts (8"Ø, 12"X10") live in the page TEXT LAYER, not the schedule.
  2. Detection boxes are in DISPLAY (rotated) space; text words are in MEDIABOX
     space — bridge them with the page's derotation matrix (same fix the Bluebeam
     stamp writer uses).
  3. Attach the nearest callout, preferring the SHAPE (round vs rect) that matches
     the device type from the schedule, as a margin-bounded tiebreaker.
  4. Tier the result HIGH/MED/LOW so low-confidence sizes are flagged "verify",
     never silently shipped as fact.

Mutates each AD-* detection in place, adding:
    neck_size_plan : str   (e.g. '8"' or '12X6')   — '' if none found
    neck_tier      : 'HIGH'|'MED'|'LOW'|''         — confidence
    neck_source    : 'plan-callout'|''             — provenance
    neck_dist_pt   : float                          — distance to the callout
"""
from __future__ import annotations
import re
import math
from pathlib import Path

import fitz

DPI_TO_PT = 72.0 / 200
PROX_PT = 150.0          # callouts sit ~20-120pt from the symbol center
HIGH_DIST = 70.0         # within this + shape match → HIGH confidence
MED_DIST = 130.0

ROUND = re.compile(r'^(\d+(?:\.\d+)?)\s*["″′]\s*[øØ⌀]?$')
RECT = re.compile(r'^(\d+)\s*["″]?\s*[xX]\s*(\d+)\s*["″]?$')


def _parse_size(t):
    t = (t or '').strip().upper()
    m = RECT.match(t)
    if m:
        return f'{m.group(1)}X{m.group(2)}'
    m = ROUND.match(t)
    if m:
        return f'{m.group(1)}"'
    return None


def _shape_of(size_str):
    return 'rect' if 'X' in size_str.upper() else 'round'


def _expected_shape(type_str):
    u = (type_str or '').upper()
    if any(k in u for k in ('SIDEWALL', 'SIDE WALL', 'DUCT', 'GRILLE', 'REGISTER', 'LINEAR')):
        return 'rect'
    if any(k in u for k in ('PLAQUE', 'DIFFUSER', 'CEILING', 'ROUND')):
        return 'round'
    return None


def _canon(t):
    return re.sub(r'[\s\-_.]+', '', str(t or '').upper())


def _shape_by_tag(variables):
    out = {}
    for v in variables or []:
        t = v.get('tag')
        if not t:
            continue
        props = v.get('properties') or {}
        typ = ''
        for k, val in props.items():
            if any(w in str(k).upper() for w in ('TYPE', 'DESCRIPTION', 'SERVICE')):
                typ = str(val)
                break
        out[_canon(t)] = _expected_shape(typ)
    return out


def annotate_neck_sizes(detections: dict, input_pdf: Path, variables: list | None = None) -> dict:
    """Mutate `detections` in place; return a small summary dict."""
    shape_by_tag = _shape_by_tag(variables)
    pages = detections.get('pages', {})
    n_high = n_med = n_low = n_none = 0
    try:
        doc = fitz.open(str(input_pdf))
    except Exception:
        return {'high': 0, 'med': 0, 'low': 0, 'none': 0, 'error': 'pdf open failed'}
    try:
        for pkey, dets in pages.items():
            try:
                pidx = int(pkey)
            except (TypeError, ValueError):
                continue
            if pidx < 0 or pidx >= doc.page_count:
                continue
            page = doc[pidx]
            mat = fitz.Matrix(page.derotation_matrix)
            toks = []
            for w in page.get_text('words'):
                s = _parse_size(w[4])
                if s:
                    toks.append((( (w[0] + w[2]) / 2, (w[1] + w[3]) / 2), s))
            for det in dets:
                if not str(det.get('cls', '')).startswith('AD'):
                    continue
                c = fitz.Point((det['x1'] + det['x2']) / 2 * DPI_TO_PT,
                               (det['y1'] + det['y2']) / 2 * DPI_TO_PT) * mat
                want = shape_by_tag.get(_canon(det.get('tag')))
                cands = sorted(
                    ((math.hypot(sx - c.x, sy - c.y), s) for (sx, sy), s in toks
                     if math.hypot(sx - c.x, sy - c.y) <= PROX_PT),
                    key=lambda z: z[0])
                if not cands:
                    n_none += 1
                    continue
                nd, ns = cands[0]
                best, bd = ns, nd
                if want and _shape_of(ns) != want:
                    margin = 1.6 * nd + 25
                    for dd, s in cands:
                        if _shape_of(s) == want and dd <= margin:
                            best, bd = s, dd
                            break
                shape_ok = (want is None) or (_shape_of(best) == want)
                if shape_ok and bd <= HIGH_DIST:
                    tier = 'HIGH'; n_high += 1
                elif bd <= MED_DIST and shape_ok:
                    tier = 'MED'; n_med += 1
                else:
                    tier = 'LOW'; n_low += 1
                det['neck_size_plan'] = best
                det['neck_tier'] = tier
                det['neck_source'] = 'plan-callout'
                det['neck_dist_pt'] = round(bd, 1)
    finally:
        doc.close()
    return {'high': n_high, 'med': n_med, 'low': n_low, 'none': n_none}


if __name__ == '__main__':
    import argparse, json, glob
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--detections', required=True)
    ap.add_argument('--variables')
    args = ap.parse_args()
    dets = json.loads(Path(args.detections).read_text(encoding='utf-8'))
    vars_ = json.loads(Path(args.variables).read_text(encoding='utf-8')) if args.variables else []
    summ = annotate_neck_sizes(dets, Path(args.pdf), vars_)
    print('summary:', summ)
    from collections import Counter
    by = Counter()
    for v in dets.get('pages', {}).values():
        for d in v:
            if d.get('neck_size_plan'):
                by[(d.get('tag'), d['neck_size_plan'], d['neck_tier'])] += 1
    for k, n in sorted(by.items(), key=lambda x: str(x[0])):
        print(f'  {n}× {k[0]} → {k[1]} [{k[2]}]')
