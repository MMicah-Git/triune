"""Part 3 diagnostic — schedule parse + tag inference + reconciliation health.

Reuses on-disk job outputs (no re-detection): for every job under
saas/data/jobs/* that has both *_variables.json and *_detections.json, it
re-runs validation_engine.reconcile() and buckets the job by Part-3 failure
mode so we can see what's actually left to fix:

  NO_SCHEDULE   vars == 0          -> schedule parse absent/failed (Part-3 #3)
  LOW_TAG_RATE  tagged/total < .5  -> tag inference gap
  ORPHANS       orphan_tags > 0    -> detected tag not in schedule (parse/normalize)
  MISSING       missing_on_plan>0  -> scheduled tag never detected (detect or tag gap)
  OK            none of the above

Run:  python -X utf8 part3_diagnostic.py
"""
import json, glob, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(HERE, 'saas', 'data', 'jobs')
sys.path.insert(0, HERE)
from validation_engine import reconcile


def load(job):
    vf = glob.glob(os.path.join(job, '*_variables.json'))
    df = glob.glob(os.path.join(job, '*_detections.json'))
    if not vf or not df:
        return None
    variables = json.load(open(vf[0], encoding='utf-8'))
    det = json.load(open(df[0], encoding='utf-8'))
    pages = det.get('pages', {})
    name = os.path.basename(vf[0]).replace('_variables.json', '')
    return name, variables, pages


def diagnose(variables, pages):
    total = sum(len(v) for v in pages.values())
    tagged = sum(1 for v in pages.values() for d in v if d.get('tag'))
    r = reconcile(variables, pages)
    s = r['summary']
    tag_rate = tagged / total if total else 0.0
    buckets = []
    if s['scheduled_tags'] == 0:
        buckets.append('NO_SCHEDULE')
    if total and tag_rate < 0.5:
        buckets.append('LOW_TAG_RATE')
    if s['orphan_tags'] > 0:
        buckets.append('ORPHANS')
    if s['missing_on_plan'] > 0:
        buckets.append('MISSING')
    if not buckets:
        buckets.append('OK')
    return {
        'total': total, 'tagged': tagged, 'tag_rate': tag_rate,
        'sched_tags': s['scheduled_tags'], 'found': s['scheduled_tags_found'],
        'missing': s['missing_on_plan'], 'orphans': s['orphan_tags'],
        'under': s['classes_under'], 'over': s['classes_over'],
        'tier': r['tier'], 'conf': r['project_confidence'],
        'buckets': buckets,
        'missing_tags': r['missing_on_plan'][:8],
        'orphan_list': r['orphan_tags'][:8],
    }


def main():
    rows = []
    for job in sorted(glob.glob(os.path.join(JOBS, '*'))):
        if not os.path.isdir(job):
            continue
        got = load(job)
        if not got:
            continue
        name, variables, pages = got
        try:
            d = diagnose(variables, pages)
        except Exception as e:
            print(f'  ! {name[:40]:40} reconcile failed: {e}')
            continue
        d['name'] = name
        rows.append(d)

    rows.sort(key=lambda r: (r['tag_rate'], -r['missing']))
    print('=' * 110)
    print(f'PART 3 DIAGNOSTIC  —  {len(rows)} jobs with detections + schedule')
    print('=' * 110)
    h = f"{'project':38} {'det':>4} {'tag%':>5} {'sched':>5} {'find':>4} {'miss':>4} {'orph':>4} {'tier':>6}  flags"
    print(h)
    print('-' * 110)
    for r in rows:
        flags = ','.join(b for b in r['buckets'] if b != 'OK') or 'OK'
        print(f"{r['name'][:38]:38} {r['total']:>4} {r['tag_rate']*100:>4.0f}% "
              f"{r['sched_tags']:>5} {r['found']:>4} {r['missing']:>4} {r['orphans']:>4} "
              f"{r['tier']:>6}  {flags}")

    # aggregate
    from collections import Counter
    agg = Counter()
    for r in rows:
        for b in r['buckets']:
            agg[b] += 1
    print('-' * 110)
    print('BUCKETS:', ', '.join(f'{k}={v}' for k, v in agg.most_common()))
    print(f"jobs with orphan tags (parse/normalize): {sum(1 for r in rows if r['orphans'])}")
    print(f"jobs with no schedule parsed:            {sum(1 for r in rows if r['sched_tags']==0)}")
    print(f"jobs with missing-on-plan (detect gap):  {sum(1 for r in rows if r['missing'])}")

    # show the worst orphan/missing offenders (the Part-3 targets)
    print('\nTOP ORPHAN OFFENDERS (detected tag not in schedule):')
    for r in sorted(rows, key=lambda x: -x['orphans'])[:6]:
        if r['orphans']:
            print(f"  {r['name'][:36]:36} orphans={r['orphans']:>3}  e.g. {r['orphan_list']}")
    print('\nTOP MISSING OFFENDERS (scheduled tag never detected):')
    for r in sorted(rows, key=lambda x: -x['missing'])[:6]:
        if r['missing']:
            print(f"  {r['name'][:36]:36} missing={r['missing']:>3}  e.g. {r['missing_tags']}")


if __name__ == '__main__':
    main()
