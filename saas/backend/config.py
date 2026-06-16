"""Backend configuration — all paths and constants in one place."""

from pathlib import Path
import os

# Repo root (two levels up from saas/backend/)
REPO_ROOT = Path(__file__).resolve().parents[2]

# Where uploaded PDFs land + per-project outputs are written
DATA_DIR = Path(os.environ.get('HVAC_DATA_DIR', REPO_ROOT / 'saas' / 'data'))

# Pipeline scripts (the existing CLI tools)
TAKEOFF_CLI = REPO_ROOT / 'takeoff_cli.py'
ADDENDUM_DIFF = REPO_ROOT / 'addendum_diff.py'
AUTO_SCALE = REPO_ROOT / 'auto_scale.py'
ROOM_GROUPER = REPO_ROOT / 'room_grouper.py'

# Default YOLO weights (overridable via env)
DEFAULT_MODEL = Path(os.environ.get(
    'HVAC_MODEL',
    REPO_ROOT / 'models' / 'hvac_yolov8s_v10.pt',
))

# Job database (single JSON file for now; swap to Postgres in v2)
JOBS_DB = DATA_DIR / 'jobs.json'

# Self-learning storage — accumulated corrections + training queue
CORRECTIONS_DIR = DATA_DIR / 'corrections'
CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
TRAINING_QUEUE = DATA_DIR / 'training_queue.jsonl'

# CORS allowlist (frontend dev server)
CORS_ORIGINS = os.environ.get(
    'HVAC_CORS_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000',
).split(',')

DATA_DIR.mkdir(parents=True, exist_ok=True)
