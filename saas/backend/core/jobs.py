"""Simple file-based job tracker.

A real production system would use Postgres + a queue (Redis/Arq). This
scaffold keeps it dead-simple: one jobs.json file, atomic rewrites,
no concurrency control beyond a process-local lock.

Swap this module for a DB-backed implementation later — the rest of the
backend only depends on the JobStore interface below.
"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import JOBS_DB

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict:
    if not JOBS_DB.exists():
        return {'jobs': {}}
    try:
        return json.loads(JOBS_DB.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'jobs': {}}


def _write(data: dict) -> None:
    tmp = JOBS_DB.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(JOBS_DB)


def create_job(kind: str, input_files: list[str], params: dict | None = None) -> str:
    job_id = uuid4().hex[:12]
    with _lock:
        data = _read()
        data['jobs'][job_id] = {
            'id': job_id,
            'kind': kind,                # 'takeoff' | 'addendum' | etc.
            'status': 'queued',          # queued | running | done | error
            'input_files': input_files,
            'params': params or {},
            'created_at': _now(),
            'started_at': None,
            'finished_at': None,
            'output_dir': None,
            'outputs': {},               # role -> relative path
            'error': None,
            'log_tail': '',
        }
        _write(data)
    return job_id


def update_job(job_id: str, **fields: Any) -> dict | None:
    with _lock:
        data = _read()
        job = data['jobs'].get(job_id)
        if job is None:
            return None
        job.update(fields)
        _write(data)
        return job


def get_job(job_id: str) -> dict | None:
    return _read()['jobs'].get(job_id)


def list_jobs() -> list[dict]:
    jobs = list(_read()['jobs'].values())
    jobs.sort(key=lambda j: j.get('created_at', ''), reverse=True)
    return jobs
