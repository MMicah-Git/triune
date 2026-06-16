"""
run_benchmark_suite.py

One-command v10-vs-v11 evaluation over a FIXED set of benchmark PDFs
(benchmark_manifest.json). Loads both models once and reuses the inference
helpers from benchmark_v10_vs_v11.py.

Why this exists: after training v11 on Kaggle and dropping the weights into
models/hvac_yolov8s_v11.pt, run this to decide whether v11 actually beats v10
BEFORE promoting it to the default model.

Scoring is COUNT-BASED (per-class quantities), not IoU. A takeoff is a Bill of
Materials — what matters is "how many of each equipment class", and that is the
same metric benchmark_samples.py uses. (IoU matching is unreliable here because
Bluebeam truth stamps are ~80px while YOLO boxes are ~240px — different box-size
conventions across model versions would tank IoU even when counts are right.)

Per class: match = min(truth_count, pred_count)
    recall    = sum(match) / sum(truth)     (fraction of real equipment found)
    precision = sum(match) / sum(pred)      (fraction of detections that are real)
    f1        = harmonic mean

Honesty model:
  - 'held-out' projects are NOT in v13 training -> real generalization signal.
  - 'in-sample' projects ARE v13 training data -> measures whether v11 learned
    the corrections (esp. the AD-GRD vs AD-T-BAR SUPPLY confusion, CLAUDE.md 19.5).
  The verdict is driven by held-out F1; in-sample is reported for context only.

Only pages that carry truth annotations are scored, so v10 and v11 are compared
on identical pages.

Usage:
    python run_benchmark_suite.py
    python run_benchmark_suite.py --projects "Barings" "St. Francis"   # subset
    python run_benchmark_suite.py --conf 0.4

Outputs in benchmark_output/_suite/:
    leaderboard.csv     per-project count P/R/F1 for v10 and v11 (+ split)
    per_class.csv       aggregated truth vs v10 vs v11 counts per class
    suite_summary.txt   split-aggregated P/R/F1 + AD-GRD shift + verdict (printed)
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from ultralytics import YOLO

from benchmark_v10_vs_v11 import render_page_image, tiled_detect, gather_truth, DEFAULT_CONF
from bluebeam_to_yolo import DEFAULT_DPI

# Diffuser classes at the heart of the AD-GRD collapse (CLAUDE.md 19.5)
TBAR_SURF = {'AD-T-BAR SUPPLY', 'AD-T-BAR RETURN', 'AD-SURF SUPPLY', 'AD-SURF RETURN'}


def count_prf(truth_counts, pred_counts):
    """Micro count-based precision / recall / f1 across classes."""
    classes = set(truth_counts) | set(pred_counts)
    match = sum(min(truth_counts.get(c, 0), pred_counts.get(c, 0)) for c in classes)
    t = sum(truth_counts.values())
    p = sum(pred_counts.values())
    recall = match / t if t else 0.0
    prec = match / p if p else 0.0
    f1 = 2 * prec * recall / (prec + recall) if prec + recall else 0.0
    return match, t, p, prec, recall, f1


def run_project(item, m10, m11, conf, dpi):
    pdf = Path(item['pdf'])
    truth_pdf = Path(item['truth'])
    if not pdf.exists():
        return {'name': item['name'], 'error': f'missing pdf: {pdf}'}

    truth_per_page = gather_truth(truth_pdf, dpi)
    pages = sorted(truth_per_page.keys())
    if not pages:
        return {'name': item['name'], 'error': 'no truth annotations found'}

    ct, c10, c11 = Counter(), Counter(), Counter()
    t10 = t11 = 0.0
    for pno in pages:
        img, _, _ = render_page_image(pdf, pno, dpi)
        t0 = time.time(); d10 = tiled_detect(m10, img, conf=conf); t10 += time.time() - t0
        t0 = time.time(); d11 = tiled_detect(m11, img, conf=conf); t11 += time.time() - t0
        ct.update(t['class'] for t in truth_per_page[pno])
        c10.update(d['norm_class'] for d in d10)
        c11.update(d['norm_class'] for d in d11)
    return {
        'name': item['name'], 'split': item['split'], 'pages': len(pages),
        'ct': ct, 'c10': c10, 'c11': c11, 't10': t10, 't11': t11,
    }


def main():
    ap = argparse.ArgumentParser(description='v10-vs-v11 count-based benchmark suite')
    ap.add_argument('--manifest', default='benchmark_manifest.json')
    ap.add_argument('--v10', default='models/hvac_yolov8s_v10.pt')
    ap.add_argument('--v11', default='models/hvac_yolov8s_v11.pt')
    ap.add_argument('--conf', type=float, default=DEFAULT_CONF)
    ap.add_argument('--dpi', type=int, default=DEFAULT_DPI)
    ap.add_argument('--projects', nargs='+', help='Substring filter on project names')
    ap.add_argument('--output-dir', default='benchmark_output/_suite')
    args = ap.parse_args()

    if not Path(args.v10).exists():
        raise SystemExit(f'Missing model: {args.v10}')
    if not Path(args.v11).exists():
        raise SystemExit(
            f'Missing model: {args.v11}\n'
            'Train v11 on Kaggle first (kaggle_bundle/), then drop the weights here.')

    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    projects = manifest['projects']
    if args.projects:
        wanted = [w.lower() for w in args.projects]
        projects = [p for p in projects if any(w in p['name'].lower() for w in wanted)]
    if not projects:
        raise SystemExit('No projects matched the filter.')

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'Loading models...\n  v10: {args.v10}\n  v11: {args.v11}')
    m10 = YOLO(args.v10)
    m11 = YOLO(args.v11)

    results = []
    for item in projects:
        print(f'\n=== {item["name"]} [{item["split"]}] ===')
        res = run_project(item, m10, m11, args.conf, args.dpi)
        if 'error' in res:
            print(f'  SKIP: {res["error"]}')
            results.append(res)
            continue
        _, _, _, p10, r10, f10 = count_prf(res['ct'], res['c10'])
        _, _, _, p11, r11, f11 = count_prf(res['ct'], res['c11'])
        res['prf10'] = (p10, r10, f10)
        res['prf11'] = (p11, r11, f11)
        flag = 'v11+' if f11 > f10 + 1e-9 else 'v11-' if f11 < f10 - 1e-9 else 'tie '
        print(f'  pages={res["pages"]}  truth={sum(res["ct"].values())}  '
              f'v10={sum(res["c10"].values())} v11={sum(res["c11"].values())}')
        print(f'  v10 P={p10:.3f} R={r10:.3f} F1={f10:.3f}   '
              f'v11 P={p11:.3f} R={r11:.3f} F1={f11:.3f}   {flag} dF1={f11 - f10:+.3f}')
        results.append(res)

    ok = [r for r in results if 'error' not in r]
    if not ok:
        raise SystemExit('No projects produced scores (all skipped).')

    # ---- leaderboard.csv ----
    with (out_dir / 'leaderboard.csv').open('w', encoding='utf-8') as f:
        f.write('project,split,pages,truth,model,dets,precision,recall,f1\n')
        for r in ok:
            for tag, prf, cc in (('v10', r['prf10'], r['c10']), ('v11', r['prf11'], r['c11'])):
                p, rc, f1 = prf
                f.write(f'"{r["name"]}",{r["split"]},{r["pages"]},{sum(r["ct"].values())},'
                        f'{tag},{sum(cc.values())},{p:.4f},{rc:.4f},{f1:.4f}\n')

    # ---- per_class.csv (aggregated counts) ----
    agg_t, agg_10, agg_11 = Counter(), Counter(), Counter()
    for r in ok:
        agg_t += r['ct']; agg_10 += r['c10']; agg_11 += r['c11']
    with (out_dir / 'per_class.csv').open('w', encoding='utf-8') as f:
        f.write('class,truth,v10,v11\n')
        for c in sorted(set(agg_t) | set(agg_10) | set(agg_11)):
            f.write(f'"{c}",{agg_t.get(c,0)},{agg_10.get(c,0)},{agg_11.get(c,0)}\n')

    # ---- split-aggregated scores + verdict ----
    def agg_split(split_name):
        ct, c10, c11 = Counter(), Counter(), Counter()
        for r in ok:
            if r['split'] == split_name:
                ct += r['ct']; c10 += r['c10']; c11 += r['c11']
        return count_prf(ct, c10), count_prf(ct, c11)

    lines = ['v10-vs-v11 BENCHMARK SUITE (count-based)', '=' * 42,
             f'models: {args.v10}  vs  {args.v11}',
             f'projects scored: {len(ok)}/{len(results)}  conf={args.conf} dpi={args.dpi}', '']

    verdict_dF1 = None
    for split_name in ('held-out', 'in-sample'):
        n = sum(1 for r in ok if r['split'] == split_name)
        if not n:
            continue
        (m10c, t10c, p10c, p10, r10, f10), (m11c, _, p11c, p11, r11, f11) = agg_split(split_name)
        lines += [f'== {split_name.upper()} ({n} project{"s" if n != 1 else ""}, {t10c} truth items) ==',
                  f'{"":5s} {"match":>6s} {"dets":>6s} {"prec":>7s} {"rec":>7s} {"f1":>7s}',
                  f'{"v10":5s} {m10c:>6d} {p10c:>6d} {p10:>7.3f} {r10:>7.3f} {f10:>7.3f}',
                  f'{"v11":5s} {m11c:>6d} {p11c:>6d} {p11:>7.3f} {r11:>7.3f} {f11:>7.3f}',
                  f'{"dF1":5s} {"":>6s} {"":>6s} {"":>7s} {"":>7s} {f11 - f10:>+7.3f}', '']
        if split_name == 'held-out':
            verdict_dF1 = f11 - f10

    # AD-GRD shift headline (counts; truth has ~0 AD-GRD, v10 over-predicts it)
    def grp(counts):
        return counts.get('AD-GRD', 0), sum(counts.get(c, 0) for c in TBAR_SURF)
    gt, tt = grp(agg_t); g10, t10 = grp(agg_10); g11, t11 = grp(agg_11)
    lines += ['== AD-GRD vs T-BAR/SURF COUNTS (the collapse v11 should fix) ==',
              f'{"":6s} {"AD-GRD":>8s} {"T-BAR/SURF":>11s}',
              f'{"truth":6s} {gt:>8d} {tt:>11d}',
              f'{"v10":6s} {g10:>8d} {t10:>11d}',
              f'{"v11":6s} {g11:>8d} {t11:>11d}',
              '  (Good v11 = AD-GRD count drops toward truth, T-BAR/SURF rises toward truth.)', '']

    if verdict_dF1 is None:
        verdict = 'NO HELD-OUT PROJECTS SCORED — cannot judge generalization.'
    elif verdict_dF1 > 0.01:
        verdict = f'PROMOTE v11: held-out count-F1 improved by {verdict_dF1:+.3f}.'
    elif verdict_dF1 < -0.01:
        verdict = f'KEEP v10: held-out count-F1 regressed by {verdict_dF1:+.3f}.'
    else:
        verdict = f'TOSS-UP: held-out count-F1 changed {verdict_dF1:+.3f} (within noise). Inspect per_class.csv.'
    lines += ['== VERDICT ==', '  ' + verdict,
              '  (Held-out drives the call. In-sample only shows whether v11 fit the corrections.)']

    summary = '\n'.join(lines) + '\n'
    (out_dir / 'suite_summary.txt').write_text(summary, encoding='utf-8')
    print('\n' + summary)
    print(f'Outputs in: {out_dir.resolve()}')


if __name__ == '__main__':
    main()
