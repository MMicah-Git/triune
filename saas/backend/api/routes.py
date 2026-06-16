"""FastAPI routes for the HVAC Takeoff SaaS scaffold.

Endpoints:
    POST   /api/jobs/takeoff      upload one PDF, kick off takeoff
    POST   /api/jobs/addendum     upload old + new PDFs, kick off diff
    POST   /api/jobs/scale        upload PDF, just detect scale (fast)
    GET    /api/jobs              list all jobs
    GET    /api/jobs/{id}         status + outputs for a single job
    GET    /api/jobs/{id}/file    download an output file by role
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter, BackgroundTasks, Body, File, HTTPException, Query, UploadFile,
)
from fastapi.responses import FileResponse, Response

from api.models import JobOut, JobCreated
from core import jobs as job_store
from core.pipeline import run_takeoff, run_addendum, run_auto_scale
from config import DATA_DIR, CORRECTIONS_DIR, TRAINING_QUEUE, REPO_ROOT, DEFAULT_MODEL

# Cached model class names — used by the class-list endpoint and to map
# corrected boxes to canonical YOLO class indices.
_MODEL_CLASS_NAMES: list[str] | None = None


def _model_class_names() -> list[str]:
    global _MODEL_CLASS_NAMES
    if _MODEL_CLASS_NAMES is None:
        from ultralytics import YOLO
        m = YOLO(str(DEFAULT_MODEL))
        _MODEL_CLASS_NAMES = [m.names[i] for i in sorted(m.names)]
    return _MODEL_CLASS_NAMES

# Lazy import — the worker queue is optional. If Redis isn't available
# we fall back to FastAPI BackgroundTasks.
try:
    from task_queue import enqueue as arq_enqueue, ping as arq_ping
    ARQ_AVAILABLE = True
except Exception:
    ARQ_AVAILABLE = False

router = APIRouter(prefix='/api', tags=['jobs'])


def _save_upload(file: UploadFile, dest_dir: Path) -> Path:
    if not file.filename:
        raise HTTPException(400, 'filename required')
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'only PDF uploads supported')
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file.filename
    with dest.open('wb') as f:
        while chunk := file.file.read(1024 * 1024):
            f.write(chunk)
    return dest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute(job_id: str, kind: str, fn, *args, **kwargs):
    """Background task body. Updates job status throughout."""
    job_store.update_job(job_id, status='running', started_at=_now())
    try:
        outputs = fn(job_id, *args, **kwargs)
        job_store.update_job(
            job_id,
            status='done',
            finished_at=_now(),
            output_dir=str((DATA_DIR / 'jobs' / job_id).relative_to(DATA_DIR)),
            outputs=outputs,
        )
    except Exception as e:
        job_store.update_job(
            job_id,
            status='error',
            finished_at=_now(),
            error=str(e),
        )


async def _enqueue_or_fallback(background: BackgroundTasks, task_name: str,
                               job_id: str, fn, *args):
    """Push the job to Arq when Redis is reachable; otherwise run in
    FastAPI BackgroundTasks (original behavior). Either way, the user
    sees the same /api/jobs/{id} response."""
    if ARQ_AVAILABLE:
        try:
            await arq_enqueue(task_name, job_id, *[str(a) for a in args])
            return
        except Exception as e:
            # Redis down or transient — fall through to BackgroundTasks.
            print(f'[routes] Arq enqueue failed ({e}); falling back to BackgroundTasks')
    kind = task_name.replace('task_', '')
    background.add_task(_execute, job_id, kind, fn, *args)


# ---------- POST endpoints ----------

@router.post('/jobs/takeoff', response_model=JobCreated)
async def post_takeoff(
    background: BackgroundTasks,
    pdf: UploadFile = File(...),
):
    job_id = job_store.create_job(kind='takeoff', input_files=[pdf.filename or 'upload.pdf'])
    saved = _save_upload(pdf, DATA_DIR / 'jobs' / job_id / 'inputs')
    await _enqueue_or_fallback(background, 'task_takeoff', job_id, run_takeoff, saved)
    return JobCreated(id=job_id, status='queued')


@router.post('/jobs/addendum', response_model=JobCreated)
async def post_addendum(
    background: BackgroundTasks,
    old: UploadFile = File(...),
    new: UploadFile = File(...),
):
    job_id = job_store.create_job(
        kind='addendum',
        input_files=[old.filename or 'old.pdf', new.filename or 'new.pdf'],
    )
    old_path = _save_upload(old, DATA_DIR / 'jobs' / job_id / 'inputs')
    new_path = _save_upload(new, DATA_DIR / 'jobs' / job_id / 'inputs')
    await _enqueue_or_fallback(background, 'task_addendum', job_id, run_addendum, old_path, new_path)
    return JobCreated(id=job_id, status='queued')


@router.post('/jobs/scale', response_model=JobCreated)
async def post_scale(
    background: BackgroundTasks,
    pdf: UploadFile = File(...),
):
    job_id = job_store.create_job(kind='auto_scale', input_files=[pdf.filename or 'upload.pdf'])
    saved = _save_upload(pdf, DATA_DIR / 'jobs' / job_id / 'inputs')
    await _enqueue_or_fallback(background, 'task_auto_scale', job_id, run_auto_scale, saved)
    return JobCreated(id=job_id, status='queued')


# ---------- GET endpoints ----------

@router.get('/jobs', response_model=list[JobOut])
def list_jobs():
    return job_store.list_jobs()


@router.get('/jobs/{job_id}', response_model=JobOut)
def get_job(job_id: str):
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')
    return job


def _resolve_inputs_dir(job_id: str) -> Path:
    """Walk back the retry_of chain until we find a job whose inputs dir
    actually exists on disk. Returns the directory containing the PDFs.

    Raises HTTPException(409) if no ancestor has reachable inputs.
    """
    seen: set[str] = set()
    cursor = job_id
    while cursor and cursor not in seen:
        seen.add(cursor)
        d = DATA_DIR / 'jobs' / cursor / 'inputs'
        if d.is_dir() and any(d.glob('*.pdf')):
            return d
        node = job_store.get_job(cursor)
        if node is None:
            break
        cursor = node.get('params', {}).get('retry_of') or ''
    raise HTTPException(
        409,
        f'no reachable input files found by walking retry_of chain from {job_id}',
    )


@router.post('/jobs/{job_id}/retry', response_model=JobCreated)
async def post_retry(job_id: str, background: BackgroundTasks):
    """Re-run an existing job (typically one in 'error' status) with the
    same inputs. Creates a NEW job; the original stays in history.

    Walks the retry_of chain to find the original upload's inputs directory,
    in case intermediate retries' inputs were never materialized on disk.
    """
    old = job_store.get_job(job_id)
    if old is None:
        raise HTTPException(404, 'job not found')

    kind = old['kind']
    old_inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(old_inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(409, 'no PDFs found in original inputs')

    new_job_id = job_store.create_job(
        kind=kind,
        input_files=old.get('input_files', [p.name for p in pdfs]),
        params={**old.get('params', {}), 'retry_of': job_id},
    )

    if kind == 'takeoff':
        await _enqueue_or_fallback(background, 'task_takeoff', new_job_id, run_takeoff, pdfs[0])
    elif kind == 'addendum':
        if len(pdfs) < 2:
            raise HTTPException(409, 'expected 2 PDFs for addendum retry')
        by_name = {p.name: p for p in pdfs}
        names = old.get('input_files') or []
        old_pdf = by_name.get(names[0]) if names else pdfs[0]
        new_pdf = by_name.get(names[1]) if len(names) > 1 else pdfs[1]
        if not old_pdf or not new_pdf:
            raise HTTPException(409, 'could not match input files by name')
        await _enqueue_or_fallback(background, 'task_addendum', new_job_id, run_addendum, old_pdf, new_pdf)
    elif kind == 'auto_scale':
        await _enqueue_or_fallback(background, 'task_auto_scale', new_job_id, run_auto_scale, pdfs[0])
    else:
        raise HTTPException(409, f'unknown job kind: {kind}')

    return JobCreated(id=new_job_id, status='queued')


@router.post('/jobs/{job_id}/stamp', response_model=dict)
def post_stamp(job_id: str):
    """Backfill: produce the Bluebeam-stamped PDF for an existing job
    whose original takeoff completed before stamping was wired in.

    Looks up the job's input PDF and *_detections.json on disk, runs the
    toolbox-mapping + Deck-2 enrichment, writes <stem>_bluebeam_stamped.pdf
    into the job's output dir, and registers it as the 'bluebeam_stamped_pdf'
    role on the job record.
    """
    import sys

    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')
    if job.get('kind') != 'takeoff':
        raise HTTPException(409, 'stamp is only meaningful for takeoff jobs')
    if job.get('status') != 'done':
        raise HTTPException(409, f'job status is {job.get("status")!r}, not done')

    job_dir = DATA_DIR / 'jobs' / job_id
    inputs_dir = job_dir / 'inputs'
    pdfs = sorted(inputs_dir.glob('*.pdf')) if inputs_dir.is_dir() else []
    if not pdfs:
        raise HTTPException(409, 'no input PDF on disk for this job')
    input_pdf = pdfs[0]

    # detections.json might live in either job_dir directly or in <stem>_takeoff/
    candidates = list(job_dir.rglob('*_detections.json'))
    if not candidates:
        raise HTTPException(409, 'no *_detections.json found for this job')
    dets_json = candidates[0]
    artifact_dir = dets_json.parent
    stem = input_pdf.stem
    output_pdf = artifact_dir / f'{stem}_bluebeam_stamped.pdf'

    # Run the full post-takeoff pipeline (page-class, OCR, keynotes,
    # cross-discipline, fill, quality, stamps, report)
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from post_takeoff import run_post_pipeline  # noqa: E402

    vars_json = next(iter(artifact_dir.glob(f'{stem}_variables.json')), None)

    try:
        manifest = run_post_pipeline(
            job_id=job_id,
            input_pdf=input_pdf,
            detections_json=dets_json,
            variables_json=vars_json,
            output_dir=artifact_dir,
        )
    except Exception as e:
        raise HTTPException(500, f'post-takeoff pipeline failed: {e}')

    # Register every produced artifact on the job
    outputs = dict(job.get('outputs') or {})
    for role, rel in (manifest.get('artifacts') or {}).items():
        full = artifact_dir / rel
        if full.exists():
            outputs[role] = str(full.relative_to(DATA_DIR))
    job_store.update_job(job_id, outputs=outputs)

    return {
        'ok': True,
        'job_id': job_id,
        'manifest': manifest,
        'new_roles': list((manifest.get('artifacts') or {}).keys()),
    }


@router.post('/jobs/{job_id}/correction', response_model=dict)
async def post_correction(
    job_id: str,
    pdf: UploadFile = File(...),
):
    """Accept a Bluebeam-marked corrected PDF for a completed job.

    The corrected PDF is the estimator's ground-truth version — they
    opened the original drawing in Bluebeam and stamped every piece of
    equipment using the same polygon-tool workflow used for the training
    data. The system extracts those polygons (via bluebeam_to_yolo) and
    appends them to the training queue for the next retraining cycle.
    """
    import json
    import sys
    from datetime import datetime, timezone

    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')
    if not pdf.filename or not pdf.filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'corrected file must be a PDF')

    proj_dir = CORRECTIONS_DIR / job_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    dest = proj_dir / pdf.filename
    with dest.open('wb') as f:
        while chunk := pdf.file.read(1024 * 1024):
            f.write(chunk)

    # Extract polygons immediately so the user knows whether the correction
    # was usable (vs returning success and silently dropping it).
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from bluebeam_to_yolo import process_project, slugify  # noqa: E402

    project_slug = f'correction-{job_id}-' + slugify(dest.stem)
    yolo_out = proj_dir / 'yolo'
    try:
        summary = process_project(
            markup_pdf=dest,
            project_name=project_slug,
            output_dir=yolo_out,
        )
    except Exception as e:
        raise HTTPException(500, f'failed to extract polygons: {e}')

    n_boxes = summary.get('boxes', 0)
    n_pages = summary.get('pages', 0)
    classes = summary.get('classes', {})

    # Append a record to the global training queue (one line per project)
    record = {
        'job_id': job_id,
        'project_slug': project_slug,
        'pdf': str(dest.relative_to(DATA_DIR)),
        'yolo_dir': str(yolo_out.relative_to(DATA_DIR)),
        'pages': n_pages,
        'boxes': n_boxes,
        'classes': classes,
        'submitted_at': datetime.now(timezone.utc).isoformat(),
    }
    with TRAINING_QUEUE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record) + '\n')

    return {
        'ok': True,
        'job_id': job_id,
        'pages_with_markups': n_pages,
        'boxes_extracted': n_boxes,
        'classes_seen': classes,
        'message': (
            f'Correction accepted. {n_boxes} polygons extracted across {n_pages} pages. '
            f'Run `python learn_from_corrections.py` to bundle this with future retraining.'
        ),
    }


@router.get('/jobs/{job_id}/file')
def download_file(job_id: str, role: Annotated[str, Query()]):
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')
    rel = job.get('outputs', {}).get(role)
    if not rel:
        raise HTTPException(404, f'no output with role={role}')
    abs_path = (DATA_DIR / rel).resolve()
    if not abs_path.exists():
        raise HTTPException(404, 'file missing on disk')
    # Path-traversal guard
    if not str(abs_path).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(403, 'forbidden')
    return FileResponse(abs_path, filename=abs_path.name)


@router.get('/jobs/{job_id}/page/{page_no}')
def render_page(job_id: str, page_no: int, dpi: int = 200):
    """Render one page of the job's input PDF to a PNG, for the interactive
    detection-overlay viewer in the UI.

    ``page_no`` is 0-indexed and matches the page keys in *_detections.json.
    ``dpi`` defaults to 200 so the rendered pixels line up 1:1 with the
    detection box coordinates (which are stored at 200 DPI), letting the
    front-end place boxes as a simple fraction of the image's natural size.
    """
    import fitz  # PyMuPDF — already a backend dependency

    inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(404, 'no input PDF for this job')
    dpi = max(12, min(dpi, 300))  # clamp — low floor allows light gallery thumbnails
    doc = fitz.open(str(pdfs[0]))
    try:
        if page_no < 0 or page_no >= doc.page_count:
            raise HTTPException(404, f'page {page_no} out of range (0..{doc.page_count - 1})')
        pix = doc[page_no].get_pixmap(dpi=dpi, annots=False)
        png = pix.tobytes('png')
    finally:
        doc.close()
    return Response(content=png, media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=3600'})


@router.get('/classes')
def list_classes():
    """The model's equipment class names — for the in-UI relabel dropdown."""
    return {'classes': _model_class_names()}


@router.get('/jobs/{job_id}/pages')
def list_pages(job_id: str):
    """Every page of the job's input PDF, with its classification when the
    pipeline has produced one. Powers the in-UI 'document scan' / all-pages
    gallery — available as soon as the upload is on disk (before the takeoff
    finishes), so the UI can show 'scanned N pages' immediately.

    Returns ``{count, classified, pages:[{index, type, is_plan, sheet}]}``
    where ``index`` is 0-based (matches the /page/{n} renderer).
    """
    import json as _json
    import fitz  # PyMuPDF

    inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(404, 'no input PDF for this job')
    doc = fitz.open(str(pdfs[0]))
    try:
        count = doc.page_count
    finally:
        doc.close()

    # Merge page classifications if the post-pipeline already wrote them.
    # The artifact is 1-indexed; convert to the 0-based render index here.
    cls: dict[int, dict] = {}
    job_dir = DATA_DIR / 'jobs' / job_id
    for pc in job_dir.glob('*_page_classifications.json'):
        try:
            for row in _json.loads(pc.read_text(encoding='utf-8')):
                idx = int(row.get('page', 0)) - 1
                sheet = ''
                for ev in row.get('evidence', []) or []:
                    if isinstance(ev, str) and ev.startswith('sheet='):
                        sheet = ev.split('=', 1)[1]
                        break
                cls[idx] = {
                    'type': row.get('type'),
                    'is_plan': row.get('is_plan'),
                    'sheet': sheet,
                }
        except Exception:
            pass
        break  # only one such file per job

    pages = []
    for i in range(count):
        c = cls.get(i, {})
        pages.append({
            'index': i,
            'type': c.get('type'),
            'is_plan': c.get('is_plan'),
            'sheet': c.get('sheet') or '',
        })
    return {'count': count, 'classified': bool(cls), 'pages': pages}


@router.get('/jobs/{job_id}/verification')
def get_verification(job_id: str):
    """Step-1 document-set verification: sheet index cross-check + red flags.
    On-demand (reads the input PDF + page classifications), so it works on
    existing jobs without re-running the pipeline.
    """
    import json as _json

    inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(404, 'no input PDF for this job')

    classifications: list = []
    job_dir = DATA_DIR / 'jobs' / job_id
    for pc in job_dir.glob('*_page_classifications.json'):
        try:
            classifications = _json.loads(pc.read_text(encoding='utf-8'))
        except Exception:
            classifications = []
        break

    # A separately-uploaded cover/index PDF (if any) enables the completeness check.
    uploaded_index = None
    idx_sidecar = job_dir / 'uploaded_index.json'
    if idx_sidecar.exists():
        try:
            uploaded_index = (_json.loads(idx_sidecar.read_text(encoding='utf-8')) or {}).get('entries')
        except Exception:
            uploaded_index = None

    from doc_verification import build_verification
    return build_verification(pdfs[0], classifications, uploaded_index=uploaded_index)


@router.get('/jobs/{job_id}/legend')
def get_legend(job_id: str, refresh: int = 0):
    """Step-2 legend reader: OCR the legend sheet into a symbol→meaning
    dictionary (label + cropped symbol + mapped YOLO class). On-demand and
    cached (OCR at 300 DPI is slow), keyed off the legend page classification.
    """
    import json as _json

    inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(404, 'no input PDF for this job')

    job_dir = DATA_DIR / 'jobs' / job_id
    legend_dir = job_dir / 'legend'
    cache = legend_dir / 'legend.json'
    if cache.exists() and not refresh:
        try:
            return _json.loads(cache.read_text(encoding='utf-8'))
        except Exception:
            pass

    classifications: list = []
    for pc in job_dir.glob('*_page_classifications.json'):
        try:
            classifications = _json.loads(pc.read_text(encoding='utf-8'))
        except Exception:
            classifications = []
        break

    from legend_reader import extract_legend
    result = extract_legend(pdfs[0], classifications=classifications)
    legend_dir.mkdir(parents=True, exist_ok=True)

    # Attach a cropped symbol image to each equipment symbol (display only).
    if result.get('symbols'):
        try:
            from legend_match import save_symbol_crops
            crops = save_symbol_crops(pdfs[0], classifications, legend_dir, dpi=200)
            for s in result['symbols']:
                if not s.get('crop'):
                    s['crop'] = crops.get(s.get('class'))
        except Exception as e:
            print(f'[legend] symbol crop failed (non-fatal): {e}')

    cache.write_text(_json.dumps(result, default=str), encoding='utf-8')
    return result


@router.get('/jobs/{job_id}/legend/crop/{name}')
def get_legend_crop(job_id: str, name: str):
    """Serve a cropped legend symbol image."""
    base = (DATA_DIR / 'jobs' / job_id / 'legend').resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.exists():
        raise HTTPException(404, 'crop not found')
    return FileResponse(target)


@router.post('/jobs/{job_id}/index_pdf', response_model=dict)
def upload_index_pdf(job_id: str, pdf: UploadFile = File(...)):
    """Upload a cover/index PDF (or the full bid set) for a job. We extract its
    drawing index and store it, so the verification cross-check can flag sheets
    that are listed in the index but missing from the takeoff PDF.
    """
    import json as _json
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')

    job_dir = DATA_DIR / 'jobs' / job_id
    saved = _save_upload(pdf, job_dir / 'index')

    from doc_verification import extract_index_from_pdf
    entries = extract_index_from_pdf(saved)
    if not entries:
        raise HTTPException(
            422,
            'no drawing index could be read from that PDF — make sure it contains '
            'the sheet-index page (cover) or the full set with title blocks.',
        )
    (job_dir / 'uploaded_index.json').write_text(
        _json.dumps({'source': saved.name, 'entries': entries}, indent=2), encoding='utf-8'
    )
    return {'ok': True, 'source': saved.name, 'entries': len(entries)}


@router.post('/jobs/{job_id}/correction_boxes', response_model=dict)
def post_correction_boxes(job_id: str, payload: dict = Body(...)):
    """Accept in-UI corrections: the verified set of boxes per page.

    Body: ``{"dpi": 200, "pages": {"<pageNo>": [{"cls","x1","y1","x2","y2"}]}}``
    where coordinates are pixels at ``dpi`` (the viewer renders at 200 DPI).

    Writes YOLO training data (images + labels + classes.txt) into the same
    corrections/ store the Bluebeam flow uses, and appends a training-queue
    record so ``learn_from_corrections.py`` picks it up for retraining. Label
    indices use the canonical model class order so they line up with the base
    dataset (which is what learn_from_corrections merges into).
    """
    import json
    import fitz  # PyMuPDF
    from datetime import datetime, timezone

    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'job not found')

    pages = payload.get('pages') or {}
    dpi = max(50, min(int(payload.get('dpi', 200)), 300))
    class_names = _model_class_names()
    cls_to_idx = {c: i for i, c in enumerate(class_names)}

    inputs_dir = _resolve_inputs_dir(job_id)
    pdfs = sorted(inputs_dir.glob('*.pdf'))
    if not pdfs:
        raise HTTPException(404, 'no input PDF for this job')

    out = CORRECTIONS_DIR / job_id / 'ui_yolo'
    (out / 'images').mkdir(parents=True, exist_ok=True)
    (out / 'labels').mkdir(parents=True, exist_ok=True)
    stem = f'correction-{job_id}-ui'

    n_boxes = 0
    n_pages = 0
    classes_seen: dict[str, int] = {}
    skipped_unknown = 0

    doc = fitz.open(str(pdfs[0]))
    try:
        for page_key, boxes in pages.items():
            if not boxes:
                continue
            try:
                pno = int(page_key)
            except (TypeError, ValueError):
                continue
            if pno < 0 or pno >= doc.page_count:
                continue
            pix = doc[pno].get_pixmap(dpi=dpi, annots=False)
            W, H = pix.width, pix.height
            name = f'{stem}__p{pno:03d}'
            (out / 'images' / f'{name}.png').write_bytes(pix.tobytes('png'))
            lines = []
            for b in boxes:
                cls = b.get('cls')
                if cls not in cls_to_idx:
                    skipped_unknown += 1
                    continue
                x1, y1, x2, y2 = float(b['x1']), float(b['y1']), float(b['x2']), float(b['y2'])
                cx = ((x1 + x2) / 2) / W
                cy = ((y1 + y2) / 2) / H
                w = abs(x2 - x1) / W
                h = abs(y2 - y1) / H
                lines.append(f'{cls_to_idx[cls]} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}')
                classes_seen[cls] = classes_seen.get(cls, 0) + 1
                n_boxes += 1
            (out / 'labels' / f'{name}.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
            n_pages += 1
    finally:
        doc.close()

    (out / 'classes.txt').write_text('\n'.join(class_names) + '\n', encoding='utf-8')

    record = {
        'job_id': job_id,
        'project_slug': stem,
        'yolo_dir': str(out.relative_to(DATA_DIR)),
        'pages': n_pages,
        'boxes': n_boxes,
        'classes': classes_seen,
        'source': 'in_ui',
        'submitted_at': datetime.now(timezone.utc).isoformat(),
    }
    with TRAINING_QUEUE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record) + '\n')

    return {
        'ok': True,
        'pages': n_pages,
        'boxes': n_boxes,
        'classes_seen': classes_seen,
        'skipped_unknown': skipped_unknown,
    }
