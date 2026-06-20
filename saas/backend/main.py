"""Entrypoint for the HVAC Takeoff SaaS API.

Run from the repo root with:
    python -m uvicorn saas.backend.main:app --reload --port 8000
Or from saas/backend/ with:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router as jobs_router
from config import CORS_ORIGINS
from core.jobs import reap_stale_jobs

app = FastAPI(
    title='HVAC Takeoff API',
    version='0.1.0',
    description='AI-powered HVAC blueprint takeoff service',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(jobs_router)


@app.on_event('startup')
def _reap_stale_jobs_on_startup():
    """Fail-fast any job left 'running' by a previous crashed/killed process."""
    reaped = reap_stale_jobs()
    if reaped:
        print(f"[startup] Reaped {len(reaped)} stale job(s) stuck in running: {reaped}")


@app.get('/health')
def health():
    return {'ok': True, 'version': app.version}


@app.get('/')
def root():
    return {
        'service': 'HVAC Takeoff API',
        'docs': '/docs',
        'health': '/health',
    }
