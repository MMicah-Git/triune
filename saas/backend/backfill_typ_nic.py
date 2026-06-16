"""
backfill_typ_nic.py — apply the new TYP/NIC plan-note counting to jobs that
were processed before that stage existed.

Surgical (does NOT replay the whole post-takeoff pipeline): for each done
takeoff job it
  1. loads the saved detections.json + input PDF + variables,
  2. runs typ_uno_nic.apply_typ_uno_nic (marks nic / typ_of_count),
  3. saves the marked detections back + writes {stem}_typ_uno_nic.json,
  4. regenerates the tag report (now with nic_excluded / typ_of_extra_units),
  5. registers the new artifact roles on the job record.

Run from saas/backend:  python backfill_typ_nic.py [--job ID ...] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import DATA_DIR  # noqa: E402
from core import jobs as job_store  # noqa: E402
from typ_uno_nic import apply_typ_uno_nic  # noqa: E402
from tag_report import write_tag_report  # noqa: E402


def _resolve_input_pdf(job: dict) -> Path | None:
    """Find the job's input PDF, walking the retry_of chain if needed."""
    seen = set()
    cursor = job.get('id')
    while cursor and cursor not in seen:
        seen.add(cursor)
        d = DATA_DIR / 'jobs' / cursor / 'inputs'
        if d.is_dir():
            pdfs = sorted(d.glob('*.pdf'))
            if pdfs:
                return pdfs[0]
        cur_job = job_store.get_job(cursor) or {}
        cursor = cur_job.get('retry_of')
    return None


def _load_variables(artifact_dir: Path, stem: str) -> list[dict]:
    for name in (f'{stem}_variables_enriched.json', f'{stem}_variables.json'):
        p = artifact_dir / name
        if p.exists():
            data = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                data = data.get('variables', []) or []
            return data or []
    return []


def _plan_pages(artifact_dir: Path, stem: str, detections: dict) -> list[int]:
    p = artifact_dir / f'{stem}_page_classifications.json'
    if p.exists():
        cls = json.loads(p.read_text(encoding='utf-8'))
        pages = [c['page'] for c in cls if c.get('is_plan')]
        if pages:
            return pages
    # Fallback: every page that has detections
    return [int(k) for k in detections.get('pages', {})]


def backfill_job(job: dict, dry_run: bool = False) -> dict:
    job_id = job['id']
    job_dir = DATA_DIR / 'jobs' / job_id
    dets = sorted(job_dir.glob('*_detections.json'))
    if not dets:
        return {'job_id': job_id, 'status': 'skip', 'reason': 'no detections.json'}
    dets_json = dets[0]
    artifact_dir = dets_json.parent
    stem = dets_json.name[:-len('_detections.json')]

    input_pdf = _resolve_input_pdf(job)
    if input_pdf is None:
        return {'job_id': job_id, 'status': 'skip', 'reason': 'input PDF not on disk'}

    detections = json.loads(dets_json.read_text(encoding='utf-8'))
    variables = _load_variables(artifact_dir, stem)
    plan_pages = _plan_pages(artifact_dir, stem, detections)

    summary = apply_typ_uno_nic(input_pdf, detections, plan_pages)

    if dry_run:
        return {'job_id': job_id, 'status': 'dry-run',
                'nic_detections': summary['n_nic_detections'],
                'typ_of_detections': summary['n_typ_of_detections'],
                'typ_of_extra_units': summary['typ_of_extra_units']}

    # Persist marked detections + summary artifact
    dets_json.write_text(json.dumps(detections, indent=2, default=str), encoding='utf-8')
    tn_path = artifact_dir / f'{stem}_typ_uno_nic.json'
    tn_path.write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')

    # Regenerate the tag report with the new counts
    tag_result = write_tag_report(
        pdf_path=input_pdf, detections=detections, variables=variables,
        plan_pages_1based=plan_pages, output_dir=artifact_dir, stem=stem,
    )

    # Register new/updated artifact roles on the job
    outputs = dict(job.get('outputs') or {})
    outputs['typ_uno_nic'] = str(tn_path.relative_to(DATA_DIR))
    for role, rel in tag_result['artifacts'].items():
        full = artifact_dir / rel
        if full.exists():
            outputs[role] = str(full.relative_to(DATA_DIR))
    job_store.update_job(job_id, outputs=outputs)

    return {'job_id': job_id, 'status': 'done',
            'nic_excluded': tag_result['totals'].get('nic_excluded', 0),
            'typ_of_extra_units': tag_result['totals'].get('typ_of_extra_units', 0),
            'nic_labels': summary['n_nic_labels'],
            'typ_of_labels': summary['n_typ_of_labels']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', nargs='*', help='specific job id(s); default = all done takeoffs')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    all_jobs = job_store.list_jobs()
    if args.job:
        targets = [j for j in all_jobs if j['id'] in set(args.job)]
    else:
        targets = [j for j in all_jobs
                   if j.get('kind') == 'takeoff' and j.get('status') == 'done']

    print(f'Backfilling {len(targets)} job(s){" (dry-run)" if args.dry_run else ""}\n')
    results = []
    for j in targets:
        try:
            r = backfill_job(j, dry_run=args.dry_run)
        except Exception as e:
            r = {'job_id': j['id'], 'status': 'error', 'reason': str(e)}
        results.append(r)
        print(f"  {r['job_id']}  {r['status']:8}  " +
              ('  '.join(f'{k}={v}' for k, v in r.items()
                        if k not in ('job_id', 'status'))))
    done = sum(1 for r in results if r['status'] == 'done')
    print(f'\n{done}/{len(targets)} backfilled; '
          f'{sum(1 for r in results if r["status"] in ("skip","error"))} skipped/errored')


if __name__ == '__main__':
    main()
