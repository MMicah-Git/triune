"""Wraps the existing CLI tools (takeoff_cli, addendum_diff, etc.) as
callable pipelines for the FastAPI service.

Two execution modes:

  1. SUBPROCESS (default; used by BackgroundTasks path)
     • Shells out to the CLI script
     • Cold-start cost per job (~5 s YOLO load)
     • Used when the API server doesn't have an Arq worker behind it

  2. IN-PROCESS (used by the Arq warm-model worker)
     • Imports takeoff_cli directly and calls run_with_args()
     • Model loaded once, cached at module level, reused per job
     • Per-job cost: ~30 s on the same hardware
"""

import os
import subprocess
import sys
from pathlib import Path

from core.jobs import update_job
from config import (
    TAKEOFF_CLI, ADDENDUM_DIFF, AUTO_SCALE, ROOM_GROUPER,
    DEFAULT_MODEL, DATA_DIR, REPO_ROOT,
)

# When this env var is set, prefer in-process calls (called from the warm
# worker). Default is subprocess for BackgroundTasks compatibility.
INPROCESS_MODE = os.environ.get('HVAC_INPROCESS') == '1'


def _run(cmd: list[str], job_id: str) -> tuple[int, str]:
    """Run a subprocess, capture stdout/stderr, update job log_tail live."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding='utf-8',
        errors='replace',
    )
    tail = []
    assert proc.stdout is not None
    for line in proc.stdout:
        tail.append(line)
        if len(tail) > 50:
            tail = tail[-50:]
        update_job(job_id, log_tail=''.join(tail))
    code = proc.wait()
    return code, ''.join(tail)


def _run_inprocess_takeoff(job_id: str, argv: list[str]) -> tuple[int, str]:
    """Run takeoff_cli in-process (used by warm worker). Captures stdout
    by redirecting it for the duration of the call, so the worker can still
    surface live log_tail updates."""
    import io
    import sys as _sys

    # Make sure the repo root is on sys.path so 'import takeoff_cli' works.
    if str(REPO_ROOT) not in _sys.path:
        _sys.path.insert(0, str(REPO_ROOT))
    import takeoff_cli  # noqa: E402

    captured = io.StringIO()
    old_stdout = _sys.stdout
    _sys.stdout = _Tee(old_stdout, captured, job_id=job_id)
    try:
        code = takeoff_cli.run_with_args(argv)
    finally:
        _sys.stdout = old_stdout
    return code, captured.getvalue()


class _Tee:
    """A stdout wrapper that fans writes to two streams AND updates the
    job's log_tail on each newline."""
    def __init__(self, real_stdout, capture, job_id: str):
        self._real = real_stdout
        self._cap = capture
        self._job_id = job_id
        self._buf = []

    def write(self, s: str):
        self._real.write(s)
        self._cap.write(s)
        self._buf.append(s)
        if '\n' in s:
            tail = ''.join(self._buf)[-3000:]
            update_job(self._job_id, log_tail=tail)
            if len(self._buf) > 200:
                self._buf = self._buf[-200:]
        return len(s)

    def flush(self):
        self._real.flush()
        self._cap.flush()


# ---------- TAKEOFF (main pipeline) ----------

def run_takeoff(job_id: str, pdf_path: Path, model: Path | None = None) -> dict:
    """Execute the full takeoff pipeline on `pdf_path`.

    Returns a dict of output file roles -> absolute paths.
    """
    model = model or DEFAULT_MODEL
    out_dir = DATA_DIR / 'jobs' / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if INPROCESS_MODE:
        argv = [
            str(pdf_path),
            '--model', str(model),
            '--output-dir', str(out_dir),
        ]
        code, _ = _run_inprocess_takeoff(job_id, argv)
    else:
        cmd = [
            sys.executable, '-u', str(TAKEOFF_CLI),
            str(pdf_path),
            '--model', str(model),
            '--output-dir', str(out_dir),
        ]
        code, _ = _run(cmd, job_id)
    if code != 0:
        raise RuntimeError(f'takeoff_cli exited {code}')

    # takeoff_cli writes <pdf-stem>_<artifact> into out_dir/<pdf-stem>_takeoff/
    stem = pdf_path.stem
    artifact_dir = out_dir / f'{stem}_takeoff'
    if not artifact_dir.exists():
        artifact_dir = out_dir

    outputs: dict[str, str] = {}
    for role, suffix in [
        ('excel', '_takeoff.xlsx'),
        ('annotated_pdf', '_annotated.pdf'),
        ('detections', '_detections.json'),
        ('variables', '_variables.json'),
        ('project_info', '_project_info.json'),
        # Trust layer — schedule↔detection reconciliation + agreement-gated QA.
        # Exposed so the web UI can render the trust score / under-over-missing
        # table instead of burying it in Excel sheet 3.
        ('reconciliation', '_reconciliation.json'),
        ('reconciliation_txt', '_reconciliation.txt'),
        ('line_items', '_line_items.json'),
    ]:
        candidate = artifact_dir / f'{stem}{suffix}'
        if candidate.exists():
            outputs[role] = str(candidate.relative_to(DATA_DIR))

    # Bonus artifacts: full post-takeoff pipeline.
    # All failures here are non-fatal — we never break a successful takeoff
    # because of a bonus stage.
    try:
        manifest = _maybe_run_post_pipeline(job_id, pdf_path, artifact_dir, stem)
        if manifest:
            for role, rel in (manifest.get('artifacts') or {}).items():
                full = artifact_dir / rel
                if full.exists():
                    outputs[role] = str(full.relative_to(DATA_DIR))
    except Exception as e:
        print(f'[pipeline] post-takeoff pipeline failed (non-fatal): {e}')

    return outputs


def _maybe_write_bluebeam_stamps(input_pdf: Path, detections_json: Path,
                                 output_pdf: Path) -> Path | None:
    """Best-effort: produce a Bluebeam-ready stamped PDF from raw detections.

    Returns the output path on success, None if skipped (e.g. no detections.json).
    Raises on actual write failure (caller decides whether to swallow).
    """
    if not detections_json.exists():
        return None
    # Local import — keeps the stamping deps optional for environments that
    # only run the CLI subprocess path.
    import sys
    backend_dir = Path(__file__).resolve().parent.parent  # core/.. = backend
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from write_bluebeam_stamps import write_stamps  # noqa: E402

    write_stamps(input_pdf, detections_json, output_pdf,
                 do_enrich=True, do_page_filter=True)
    return output_pdf


def _maybe_run_post_pipeline(job_id: str, input_pdf: Path, output_dir: Path,
                            stem: str) -> dict | None:
    """Run the full post-takeoff pipeline (page-class, keynotes, cross-disc,
    fill, quality, stamps, report). Returns the manifest or None on failure."""
    import sys
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from post_takeoff import run_post_pipeline  # noqa: E402

    dets_json = output_dir / f'{stem}_detections.json'
    vars_json = output_dir / f'{stem}_variables.json'
    if not dets_json.exists():
        return None
    return run_post_pipeline(
        job_id=job_id,
        input_pdf=input_pdf,
        detections_json=dets_json,
        variables_json=vars_json if vars_json.exists() else None,
        output_dir=output_dir,
    )


# ---------- ADDENDUM DIFF ----------

def run_addendum(job_id: str, old_pdf: Path, new_pdf: Path,
                 model: Path | None = None) -> dict:
    model = model or DEFAULT_MODEL
    out_dir = DATA_DIR / 'jobs' / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, '-u', str(ADDENDUM_DIFF),
        '--old', str(old_pdf),
        '--new', str(new_pdf),
        '--model', str(model),
        '--output-dir', str(out_dir),
        '--render',
    ]
    code, _ = _run(cmd, job_id)
    if code != 0:
        raise RuntimeError(f'addendum_diff exited {code}')

    new_stem = new_pdf.stem
    sub = out_dir / new_stem
    outputs = {}
    for role, name in [
        ('diff_csv', 'diff.csv'),
        ('diff_summary', 'diff_summary.txt'),
        ('annotated_pdf', 'diff_annotated.pdf'),
    ]:
        p = sub / name
        if p.exists():
            outputs[role] = str(p.relative_to(DATA_DIR))
    return outputs


# ---------- AUTO SCALE (standalone, fast) ----------

def run_auto_scale(job_id: str, pdf_path: Path) -> dict:
    out_dir = DATA_DIR / 'jobs' / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f'{pdf_path.stem}_scales.json'

    cmd = [
        sys.executable, '-u', str(AUTO_SCALE),
        '--pdf', str(pdf_path),
        '--output', str(out_json),
    ]
    code, _ = _run(cmd, job_id)
    if code != 0:
        raise RuntimeError(f'auto_scale exited {code}')

    return {'scales': str(out_json.relative_to(DATA_DIR))} if out_json.exists() else {}
