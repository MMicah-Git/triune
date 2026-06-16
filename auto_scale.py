"""
auto_scale.py — SheetScan equivalent

Detect the drawing scale on every page of a PDF and emit pixel-to-real-world
conversion ratios. Required for ductwork measurement and any "real units"
output.

Strategy (text-first, vision later):
  1. Read every text span on the page (PyMuPDF `page.get_text("dict")`).
  2. Match against a library of common architectural scale patterns:
        "1/4\\"=1'-0\\""        "1/4 IN = 1 FT"
        "1/8\\"=1'-0\\""
        "1/2\\"=1'-0\\""
        "3/8\\"=1'-0\\""
        "3/4\\"=1'-0\\""
        "1\\"=1'-0\\""
        "SCALE: ..."           prefix
        "1:50", "1:100", "1:200"   (metric, less common in US)
        "AS NOTED", "NTS", "NONE"  (no scale)
  3. Convert the matched scale into image_px_per_real_inch given a render DPI.

Output:
  scales.json keyed by page number:
    {
      "page": 4,
      "scale_text": "1/4\" = 1'-0\"",
      "drawing_inches_per_real_foot": 0.25,
      "image_px_per_real_inch": 4.167,
      "image_px_per_real_foot": 50.0,
      "source": "title_block_text",
      "confidence": 0.95
    }

Usage:
    python auto_scale.py --pdf "<plan.pdf>"
    python auto_scale.py --pdf "<plan.pdf>" --dpi 300
"""

import argparse
import json
import re
from collections import Counter
from fractions import Fraction
from pathlib import Path

import fitz


# Render DPI used by the rest of the pipeline
DEFAULT_DPI = 200

# Match "1/4\" = 1'-0\"" and variations
SCALE_INCH_FOOT_RE = re.compile(
    r"""
    (?P<num>\d+(?:[./]\d+)?)          # 1/4 or 0.25 or 1
    \s*[\"'`′″]*\s*         # quote/apostrophe (optional)
    (?:IN(?:CH(?:ES)?)?\.?)?          # 'IN' or 'INCHES'
    \s*=\s*
    (?P<denom>\d+)                    # 1 (foot value)
    \s*[′'`-]?\s*                # foot mark / dash
    0?                                # optional 0 for inches
    \s*[\"`″]?                   # closing quote
    \s*(?:FT|FOOT|FEET|FT\.)?         # optional unit word
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Match "1:100" metric form
SCALE_METRIC_RE = re.compile(r'\b1\s*:\s*(\d{2,4})\b')

# Phrases meaning "no scale"
NO_SCALE = (
    re.compile(r'\bAS\s*NOTED\b', re.IGNORECASE),
    re.compile(r'\bNTS\b', re.IGNORECASE),
    re.compile(r'\bN\.T\.S\.', re.IGNORECASE),
    re.compile(r'\bSCALE\s*:\s*(NONE|NO|N/A)\b', re.IGNORECASE),
)

# Look only at spans containing one of these keywords first, to avoid
# matching ratios that aren't scales
SCALE_HINT = re.compile(r'\bSCALE\b', re.IGNORECASE)


def page_text_spans(page):
    """Yield (text, bbox) for every text span on the page."""
    d = page.get_text('dict')
    for block in d.get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                t = span.get('text', '').strip()
                if t:
                    yield t, span.get('bbox')


def parse_scale_from_text(text: str):
    """Parse a single text span; return (key, value) tuple or None."""
    # No-scale phrases override
    for r in NO_SCALE:
        if r.search(text):
            return ('no_scale', text)

    m = SCALE_INCH_FOOT_RE.search(text)
    if m:
        num = m.group('num')
        denom = int(m.group('denom'))
        try:
            drawing_in = float(Fraction(num)) if '/' in num else float(num)
            return ('imperial', (drawing_in, denom, m.group(0)))
        except (ValueError, ZeroDivisionError):
            pass

    m = SCALE_METRIC_RE.search(text)
    if m:
        return ('metric', (int(m.group(1)), m.group(0)))

    return None


def best_scale_for_page(page, dpi: int):
    """Return the dominant scale on a page.

    Confidence boosted when the span explicitly contains 'SCALE'.
    Multiple unique scales = lower confidence (drawing might use mixed scales).
    """
    candidates = []
    for text, bbox in page_text_spans(page):
        parsed = parse_scale_from_text(text)
        if parsed is None:
            continue
        has_keyword = bool(SCALE_HINT.search(text))
        candidates.append({
            'kind': parsed[0],
            'value': parsed[1],
            'text': text,
            'bbox': bbox,
            'keyword': has_keyword,
        })

    if not candidates:
        return None

    # Prefer those with the SCALE keyword
    kw = [c for c in candidates if c['keyword']]
    pool = kw if kw else candidates

    # Vote: the (kind, value) seen most often wins
    votes = Counter()
    for c in pool:
        k = c['kind']
        if k == 'imperial':
            di, fl, _ = c['value']
            votes[('imperial', di, fl)] += 1
        elif k == 'metric':
            ratio, _ = c['value']
            votes[('metric', ratio)] += 1
        else:
            votes[('no_scale',)] += 1

    winner = votes.most_common(1)[0][0]
    n_unique = len(votes)
    n_votes = votes[winner]

    # Confidence: keyword + many votes + few alternatives
    base_conf = 0.6 if not kw else 0.85
    if n_unique == 1:
        base_conf += 0.1
    if n_votes >= 3:
        base_conf += 0.05
    confidence = min(0.99, base_conf)

    # Compute conversions
    result = {
        'scale_text': next((c['text'] for c in pool if _matches_winner(c, winner)), pool[0]['text']),
        'source': 'title_block_text' if kw else 'page_text',
        'confidence': round(confidence, 3),
        'votes': n_votes,
        'unique_scales_seen': n_unique,
    }

    if winner[0] == 'imperial':
        _, drawing_in, real_ft = winner
        # On the printed page, drawing_in inches == real_ft feet.
        # In our rendered raster (at `dpi`), 1 inch == dpi pixels.
        result.update({
            'kind': 'imperial',
            'drawing_inches_per_real_foot': drawing_in / real_ft,
            'image_px_per_real_inch': (drawing_in * dpi) / (real_ft * 12.0),
            'image_px_per_real_foot': drawing_in * dpi / real_ft,
        })
    elif winner[0] == 'metric':
        _, ratio = winner
        # 1:ratio meaning 1mm drawn = ratio mm in reality.
        # 1mm = 1/25.4 inch; image px per real mm:
        # drawing_mm_per_real_mm = 1/ratio
        # 1 inch in rasterspace = dpi pixels, 1 inch = 25.4 mm.
        # So image_px_per_real_mm = (1/ratio) * (dpi / 25.4)
        result.update({
            'kind': 'metric',
            'metric_ratio': ratio,
            'image_px_per_real_mm': (1.0 / ratio) * (dpi / 25.4),
            'image_px_per_real_m': (1.0 / ratio) * (dpi / 25.4) * 1000.0,
        })
    else:
        result.update({'kind': 'no_scale'})

    return result


def _matches_winner(c, winner):
    if winner[0] == 'imperial' and c['kind'] == 'imperial':
        return (c['value'][0], c['value'][1]) == winner[1:]
    if winner[0] == 'metric' and c['kind'] == 'metric':
        return c['value'][0] == winner[1]
    if winner[0] == 'no_scale' and c['kind'] == 'no_scale':
        return True
    return False


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--dpi', type=int, default=DEFAULT_DPI)
    ap.add_argument('--output', help='Output scales.json path (default: alongside PDF)')
    args = ap.parse_args()

    pdf = Path(args.pdf)
    out = Path(args.output) if args.output else pdf.parent / f'{pdf.stem}_scales.json'

    doc = fitz.open(str(pdf))
    per_page = {}
    print(f'{"page":>5s} | {"scale":40s} | conf | source')
    print('-' * 80)
    for pno in range(doc.page_count):
        page = doc[pno]
        res = best_scale_for_page(page, dpi=args.dpi)
        if res is None:
            per_page[pno + 1] = {'page': pno + 1, 'scale_text': None, 'kind': 'not_found'}
            print(f'{pno+1:>5d} | (no scale found)')
        else:
            res['page'] = pno + 1
            per_page[pno + 1] = res
            print(f'{pno+1:>5d} | {res["scale_text"][:40]:40s} | {res["confidence"]:.2f} | {res["source"]}')
    doc.close()

    out.write_text(json.dumps(per_page, indent=2), encoding='utf-8')
    print(f'\nWrote: {out}')


if __name__ == '__main__':
    main()
