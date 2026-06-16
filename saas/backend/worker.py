"""Arq worker — runs the takeoff/addendum/auto_scale pipelines in-process
with the YOLO model loaded ONCE at startup. Each subsequent job re-uses
the cached model, eliminating the ~5 s cold-start cost.

Run with:
    cd saas/backend
    arq worker.WorkerSettings

Prerequisites:
  • Redis running on localhost:6379  (or set HVAC_REDIS_URL)
  • All Python deps from requirements.txt installed
  • models/hvac_yolov8s_v10.pt on disk (or override via HVAC_MODEL)
"""

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

# Make sure repo root is importable for takeoff_cli, sheet_filter, etc.
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Force in-process pipeline mode for this worker
os.environ['HVAC_INPROCESS'] = '1'

from core import jobs as job_store  # noqa: E402
from core.pipeline import (  # noqa: E402
    run_takeoff, run_addendum, run_auto_scale,
)
from config import DATA_DIR, DEFAULT_MODEL  # noqa: E402
from task_queue import redis_settings  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execute(job_id: str, kind: str, fn, *args, **kwargs):
    """Shared body for all 3 job kinds — copied from api/routes.py so we
    don't depend on FastAPI request scope here."""
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


# ---------- Arq task functions ----------

async def task_takeoff(ctx, job_id: str, pdf_path_str: str):
    pdf = Path(pdf_path_str)
    _execute(job_id, 'takeoff', run_takeoff, pdf)


async def task_addendum(ctx, job_id: str, old_pdf_str: str, new_pdf_str: str):
    _execute(job_id, 'addendum', run_addendum, Path(old_pdf_str), Path(new_pdf_str))


async def task_auto_scale(ctx, job_id: str, pdf_path_str: str):
    _execute(job_id, 'auto_scale', run_auto_scale, Path(pdf_path_str))


# ---------- Startup / shutdown hooks ----------

async def on_startup(ctx):
    """Preload the YOLO model into takeoff_cli's module-level cache so the
    very first job's user doesn't pay the load cost. Optional but nice."""
    print('[worker] preloading YOLO model from', DEFAULT_MODEL)
    try:
        if Path(DEFAULT_MODEL).exists():
            import takeoff_cli
            takeoff_cli._get_yolo_model(DEFAULT_MODEL)
            print('[worker] model ready')
        else:
            print(f'[worker] skipped — model not found at {DEFAULT_MODEL}')
    except Exception as e:
        print(f'[worker] preload failed (will load on first job): {e}')


async def on_shutdown(ctx):
    print('[worker] shutdown')


# ---------- Arq settings ----------

class WorkerSettings:
    redis_settings = redis_settings()
    functions = [task_takeoff, task_addendum, task_auto_scale]
    on_startup = on_startup
    on_shutdown = on_shutdown
    keep_result = 3600        # keep job state for 1 hour
    max_jobs = 1              # one heavy job at a time per worker
    job_timeout = 60 * 30     # 30 min hard timeout
