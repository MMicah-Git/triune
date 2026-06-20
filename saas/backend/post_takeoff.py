"""
post_takeoff.py — orchestrates all the new-pipeline stages after the
core YOLO + tag inference run produces detections.json + variables.json.

Called by:
  - core/pipeline.py run_takeoff (auto, after each successful takeoff)
  - the backfill endpoint POST /api/jobs/{id}/stamp (manual replay)

Stages run, in order:
  Stage 2  — classify pages
  Stage 4  — OCR schedule fallback (if variables.json empty AND we found
             at least one 'schedule' page)
  Stage 5  — extract keynotes + link to detections
  Stage 6  — cross-discipline tag scan
  Stage 10 — context enrichment (FSD/CRD/merge/TYP)  [via write_bluebeam_stamps]
  Stage 11 — fill missing data (neck size, slot width)
  Stage 12 — quality checks
  Stage 13 — per-room counts + discrepancy report + Bluebeam stamp PDF
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from page_classifier import classify_pdf, NON_PLAN_TYPES  # noqa: E402
from keynote_extractor import extract_all_keynotes, link_detections_to_keynotes  # noqa: E402
from cross_discipline import find_orphan_tags  # noqa: E402
from data_filler import fill_missing_data  # noqa: E402
from quality_checks import run_all_checks  # noqa: E402
from discrepancy_report import build_report  # noqa: E402
from write_bluebeam_stamps import write_stamps  # noqa: E402
from project_info import extract_project_info  # noqa: E402
from tag_report import write_tag_report  # noqa: E402


def _load_json(p: Path):
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


def _save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, default=str), encoding='utf-8')


# Page types (in priority order) where a schedule is likely to live. Schedules
# on dense drawing sets routinely get misclassified, so we cast a wide net —
# OCR is only attempted when the text-layer parser already returned nothing.
_OCR_PAGE_TYPE_PRIORITY = [
    'schedule', 'air_balance', 'legend', 'details', 'cover', 'notes',
]
# Cap how many pages we OCR — each big sheet is ~30-60s on CPU.
_OCR_MAX_PAGES = 8


def _ocr_candidate_pages(classifications: list) -> list[int]:
    """Ordered, deduped list of pages worth OCRing for a schedule.
    Prioritized by classified type, then any remaining plan pages (some sets
    embed the schedule on the floor-plan sheet)."""
    seen: set[int] = set()
    ordered: list[int] = []
    by_type: dict[str, list[int]] = {}
    for c in classifications:
        by_type.setdefault(c.type, []).append(c.page)
    for t in _OCR_PAGE_TYPE_PRIORITY:
        for p in by_type.get(t, []):
            if p not in seen:
                seen.add(p); ordered.append(p)
    for c in classifications:           # plan pages last
        if c.is_plan and c.page not in seen:
            seen.add(c.page); ordered.append(c.page)
    return ordered[:_OCR_MAX_PAGES]


import re as _re
# Plausible HVAC tag: 1-4 letters, optional dash, 1-3 digits, optional suffix.
_OCR_TAG_RE = _re.compile(r'^[A-Z]{1,4}-?\d{1,3}[A-Z]?$')
_OCR_TAG_STOP = {'AND', 'FOR', 'NOT', 'THE', 'OF', 'OR', 'TO', 'IN', 'ON', 'AT',
                 'PER', 'SEE', 'ALL', 'NTS', 'TYP', 'REF', 'REV', 'NEW', 'EXIST',
                 'ARIZ', 'ZONA', 'US', 'USA', 'IBC', 'IMC', 'IECC'}


def _filter_ocr_noise(ocr_vars: list) -> tuple[list, int]:
    """Drop OCR-recovered 'variables' whose tag isn't a real HVAC tag.

    The raster OCR fallback scrapes note prose and the NOT-FOR-CONSTRUCTION
    watermark into fake tags (e.g. 'AND', 'JIXZ', 'P'->{0:ARIZ}). Mirrors
    takeoff_cli.filter_ocr_variables so the web pipeline doesn't re-introduce the
    garbage that the CLI already filters. Returns (kept, dropped_count)."""
    kept, dropped = [], 0
    for v in ocr_vars or []:
        tag = (v.get('tag') or '').strip().upper()
        if not tag or tag in _OCR_TAG_STOP or not _OCR_TAG_RE.match(tag):
            dropped += 1
            continue
        kept.append(v)
    return kept, dropped


def _maybe_schedule_ocr(pdf_path: Path, classifications: list, variables: list) -> list:
    """Run OCR fallback when the text-layer parser returned no variables.

    Casts a wide net over likely schedule pages (schedule / air_balance /
    legend / details / cover / notes, then plans) because schedules on dense
    or broken-font drawing sets are frequently misclassified. OCR only runs
    when ``variables`` is empty, so it never overrides good text-layer data.
    """
    if variables:
        return variables
    candidate_pages = _ocr_candidate_pages(classifications)
    if not candidate_pages:
        return variables
    print(f'[post_takeoff] variables empty; running OCR fallback on pages {candidate_pages}')
    try:
        from schedule_ocr import extract_all_schedules
        ocr_vars = extract_all_schedules(pdf_path, candidate_pages, dpi=200)
        ocr_vars, dropped = _filter_ocr_noise(ocr_vars)
        if dropped:
            print(f'[post_takeoff] OCR guard discarded {dropped} noise "variable(s)" '
                  '(non-tag text from notes/watermark)')
        if ocr_vars:
            print(f'[post_takeoff] OCR fallback recovered {len(ocr_vars)} usable variables')
        else:
            print('[post_takeoff] OCR fallback recovered 0 usable variables '
                  '(no real schedule tables on this PDF)')
        return ocr_vars
    except Exception as e:
        print(f'[post_takeoff] OCR fallback failed: {e}')
        return variables


def run_post_pipeline(
    job_id: str,
    input_pdf: Path,
    detections_json: Path,
    variables_json: Path | None,
    output_dir: Path,
    do_schedule_ocr: bool = True,
    do_room_counts: bool = True,
    do_project_info: bool = True,
) -> dict:
    """Run all post-takeoff stages. Returns a manifest with all artifacts."""

    pdf_name = input_pdf.name
    stem = input_pdf.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        'job_id': job_id,
        'pdf_name': pdf_name,
        'artifacts': {},
        'stats': {},
    }

    # Load inputs
    detections = _load_json(detections_json) or {'pages': {}, 'dpi': 200}
    variables = _load_json(variables_json) if variables_json else []
    if isinstance(variables, dict):  # variables.json might be a dict in some shapes
        variables = variables.get('variables', []) if 'variables' in variables else []

    # --- Stage 2: classify pages ---
    dets_per_page = {int(k): len(v) for k, v in detections.get('pages', {}).items()}
    classifications = classify_pdf(input_pdf, detections_per_page=dets_per_page)
    classifications_dict = [c.to_dict() for c in classifications]
    _save_json(output_dir / f'{stem}_page_classifications.json', classifications_dict)
    manifest['artifacts']['page_classifications'] = f'{stem}_page_classifications.json'
    manifest['stats']['n_plan_pages'] = sum(1 for c in classifications if c.is_plan)
    manifest['stats']['n_non_plan_pages'] = sum(1 for c in classifications if c.type in NON_PLAN_TYPES)

    # --- Stage 3: project info (fixed extractor) ---
    if do_project_info:
        try:
            info = extract_project_info(input_pdf)
            _save_json(output_dir / f'{stem}_project_info_v2.json', info)
            manifest['artifacts']['project_info_v2'] = f'{stem}_project_info_v2.json'
            manifest['stats']['project_info_fields'] = list(info.keys())
        except Exception as e:
            print(f'[post_takeoff] project_info failed: {e}')

    # --- Stage 4: OCR fallback if needed ---
    if do_schedule_ocr:
        variables = _maybe_schedule_ocr(input_pdf, classifications, variables)
        manifest['stats']['n_variables'] = len(variables)

    # --- Stage 5: keynotes (text-layer first, OCR fallback if empty) ---
    try:
        keynotes = extract_all_keynotes(input_pdf)
        # OCR fallback: if no text-layer keynotes found, run OCR on
        # pages classified as 'legend' (where keynotes typically live).
        if keynotes['total_notes'] == 0:
            legend_pages = [c.page for c in classifications if c.type == 'legend']
            # Also try 'cover' since some projects put keynotes there
            cover_pages = [c.page for c in classifications if c.type == 'cover']
            candidate_pages = legend_pages + cover_pages
            if candidate_pages:
                print(f'[post_takeoff] no text-layer keynotes; trying OCR on pages {candidate_pages}')
                try:
                    from keynote_ocr import extract_all_keynotes_ocr
                    ocr_notes = extract_all_keynotes_ocr(input_pdf, candidate_pages)
                    for n in ocr_notes:
                        keynotes['notes'][n['number']] = n
                    keynotes['total_notes'] = len(keynotes['notes'])
                except Exception as e:
                    print(f'[post_takeoff] keynote OCR fallback failed: {e}')

        keynotes_json = {
            'notes': {str(k): v for k, v in keynotes['notes'].items()},
            'callouts_by_page': {str(k): v for k, v in keynotes['callouts_by_page'].items()},
            'unreferenced_notes': keynotes['unreferenced_notes'],
            'undefined_callouts': keynotes['undefined_callouts'],
            'total_notes': keynotes['total_notes'],
            'total_callouts': keynotes['total_callouts'],
        }
        _save_json(output_dir / f'{stem}_keynotes.json', keynotes_json)
        manifest['artifacts']['keynotes'] = f'{stem}_keynotes.json'
        manifest['stats']['n_keynotes'] = keynotes['total_notes']
        link_detections_to_keynotes(detections, keynotes)
    except Exception as e:
        print(f'[post_takeoff] keynotes failed: {e}')
        keynotes_json = None

    # --- Stage 6: cross-discipline ---
    schedule_tags = sorted({v.get('tag', '').upper() for v in variables if v.get('tag')})
    detected_tags_by_page: dict[int, set] = {}
    for pkey, det_list in detections.get('pages', {}).items():
        s = set()
        for det in det_list:
            if det.get('tag'):
                s.add(det['tag'].upper())
        if s:
            detected_tags_by_page[int(pkey)] = s
    try:
        orphans = find_orphan_tags(input_pdf, schedule_tags, detected_tags_by_page)
        if orphans:
            _save_json(output_dir / f'{stem}_orphan_tags.json', orphans)
            manifest['artifacts']['orphan_tags'] = f'{stem}_orphan_tags.json'
        manifest['stats']['n_orphan_tags'] = len(orphans)
    except Exception as e:
        print(f'[post_takeoff] cross-discipline failed: {e}')
        orphans = []

    # --- Stage 6.5: TYP / NIC plan-note semantics ---
    # Marks detections in-place (nic / typ_of_count) so downstream counts
    # (tag_report) exclude NIC items and apply explicit "(TYP OF N)" multipliers.
    try:
        from typ_uno_nic import apply_typ_uno_nic
        plan_pages_tn = [c.page for c in classifications if c.is_plan]
        tn_summary = apply_typ_uno_nic(input_pdf, detections, plan_pages_tn)
        # Persist marks back to detections.json so Bluebeam stamps + UI see them
        _save_json(detections_json, detections)
        # Write the human-facing summary artifact
        tn_path = output_dir / f'{stem}_typ_uno_nic.json'
        _save_json(tn_path, tn_summary)
        manifest['artifacts']['typ_uno_nic'] = f'{stem}_typ_uno_nic.json'
        manifest['stats']['typ_uno_nic'] = {
            'nic_detections': tn_summary['n_nic_detections'],
            'typ_of_detections': tn_summary['n_typ_of_detections'],
            'typ_of_extra_units': tn_summary['typ_of_extra_units'],
        }
        print(f"[post_takeoff] typ/nic: {manifest['stats']['typ_uno_nic']}")
    except Exception as e:
        print(f'[post_takeoff] typ/nic failed: {e}')

    # --- Stage 11: fill missing data ---
    fill_stats = fill_missing_data(variables) if variables else {}
    manifest['stats']['fill'] = fill_stats

    # Save (possibly OCR-extracted + filled) variables
    if variables:
        _save_json(output_dir / f'{stem}_variables_enriched.json', variables)
        manifest['artifacts']['variables_enriched'] = f'{stem}_variables_enriched.json'

    # --- Stage 12: quality checks ---
    quality = run_all_checks(variables, detections, classifications=classifications_dict)
    _save_json(output_dir / f'{stem}_qa.json', quality)
    manifest['artifacts']['qa'] = f'{stem}_qa.json'
    manifest['stats']['warnings'] = quality.get('by_severity', {})

    # --- Stage 13: Bluebeam stamps (uses Stage 10 enrichment + Stage 2 page filter) ---
    output_bluebeam = output_dir / f'{stem}_bluebeam_stamped.pdf'
    try:
        stamp_summary = write_stamps(input_pdf, detections_json, output_bluebeam,
                                     do_enrich=True, do_page_filter=True)
        manifest['artifacts']['bluebeam_stamped_pdf'] = f'{stem}_bluebeam_stamped.pdf'
        manifest['stats']['stamps'] = {
            'written': stamp_summary['stamps_written'],
            'by_subject': stamp_summary['by_subject'],
            'skipped_by_page_filter': stamp_summary.get('skipped_by_page_filter', 0),
            'fsd_op_count': stamp_summary.get('fsd_op_count', 0),
            'crd_count': stamp_summary.get('crd_count', 0),
            'merged_runs': stamp_summary.get('merged_runs', 0),
        }
        # Pull enrichment stats out for the report
        enrich_stats = {
            'fsd_op_count': stamp_summary.get('fsd_op_count', 0),
            'crd_count': stamp_summary.get('crd_count', 0),
            'merged_runs': stamp_summary.get('merged_runs', 0),
        }
    except Exception as e:
        print(f'[post_takeoff] stamping failed: {e}')
        enrich_stats = {}

    # --- Stage 12.5: Neck-size waterfall ---
    # Per-detection neck size + confidence + source. Mutates detections
    # in-place: every detection gets neck_size, neck_confidence, neck_source.
    try:
        from neck_size_waterfall_runner import enrich_detections_with_neck_size
        neck_stats = enrich_detections_with_neck_size(
            detections=detections,
            variables=variables,
            input_pdf=input_pdf,
            plan_pages_1based=[c.page for c in classifications if c.is_plan],
        )
        manifest['stats']['neck_size'] = neck_stats
        print(f"[post_takeoff] neck-size waterfall: {neck_stats}")
    except Exception as e:
        print(f'[post_takeoff] neck-size waterfall failed: {e}')

    # --- Stage 13: tag-by-tag breakdown report (per-instance + YOLO + schedule) ---
    try:
        plan_pages = [c.page for c in classifications if c.is_plan]
        tag_rpt = write_tag_report(
            pdf_path=input_pdf,
            detections=detections,
            variables=variables,
            plan_pages_1based=plan_pages,
            output_dir=output_dir,
            stem=stem,
        )
        for role, rel in tag_rpt['artifacts'].items():
            manifest['artifacts'][role] = rel
        manifest['stats']['tag_report'] = tag_rpt['totals']
    except Exception as e:
        print(f'[post_takeoff] tag_report failed: {e}')

    # --- Stage 13: per-room counts (OCR-heavy, default on) ---
    room_data = None
    if do_room_counts:
        try:
            from room_counter import per_room_counts  # heavy import
            plan_pages = [c.page for c in classifications if c.is_plan]
            room_data = per_room_counts(
                input_pdf, detections,
                plan_page_nums=plan_pages,
                ocr_dpi=150,
                detections_dpi=detections.get('dpi', 200),
            )
            # Strip rooms_by_page (heavy) before saving
            to_save = {
                'breakdown': room_data['breakdown'],
                'n_rooms_found': room_data['n_rooms_found'],
            }
            _save_json(output_dir / f'{stem}_room_counts.json', to_save)
            manifest['artifacts']['room_counts'] = f'{stem}_room_counts.json'
            manifest['stats']['n_rooms'] = room_data['n_rooms_found']
        except Exception as e:
            print(f'[post_takeoff] room counts failed: {e}')

    # Load project_info if we wrote it
    project_info_dict = None
    pi_path = output_dir / f'{stem}_project_info_v2.json'
    if pi_path.exists():
        project_info_dict = _load_json(pi_path)

    # --- Stage 13: discrepancy report (Markdown + JSON) ---
    report = build_report(
        job_id=job_id,
        pdf_name=pdf_name,
        classifications=classifications_dict,
        quality_warnings=quality,
        keynotes=keynotes_json,
        cross_discipline_orphans=orphans,
        variables=variables,
        detections=detections,
        fill_stats=fill_stats,
        enrich_stats=enrich_stats,
        project_info=project_info_dict,
        room_data=room_data,
    )
    (output_dir / f'{stem}_qa_report.md').write_text(report['markdown'], encoding='utf-8')
    _save_json(output_dir / f'{stem}_qa_report.json', report['json_structured'])
    manifest['artifacts']['qa_report_md'] = f'{stem}_qa_report.md'
    manifest['artifacts']['qa_report_json'] = f'{stem}_qa_report.json'

    # Save manifest
    _save_json(output_dir / f'{stem}_post_manifest.json', manifest)

    return manifest


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--job-id', required=True)
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--detections', required=True)
    ap.add_argument('--variables')
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args()

    manifest = run_post_pipeline(
        job_id=args.job_id,
        input_pdf=Path(args.pdf),
        detections_json=Path(args.detections),
        variables_json=Path(args.variables) if args.variables else None,
        output_dir=Path(args.out_dir),
    )
    print(json.dumps(manifest, indent=2, default=str))
