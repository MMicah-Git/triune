"""Arq queue accessor — small helper to enqueue jobs from FastAPI handlers.

The FastAPI side never executes pipelines directly. It writes the job row
to jobs.json and pushes an Arq task that the warm worker picks up.

If Redis is unreachable, callers should fall back to FastAPI's
BackgroundTasks path (kept in routes.py for resilience).
"""

import os
from typing import Optional

from arq import create_pool
from arq.connections import RedisSettings

REDIS_URL = os.environ.get('HVAC_REDIS_URL', 'redis://localhost:6379')

_pool = None  # cached on first call


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(REDIS_URL)


async def get_pool():
    """Return a shared Arq pool, lazily created."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue(function_name: str, *args, **kwargs):
    """Enqueue a job by function name. Returns the Arq Job handle."""
    pool = await get_pool()
    return await pool.enqueue_job(function_name, *args, **kwargs)


async def ping() -> bool:
    """Quick health check: is Redis reachable?"""
    try:
        pool = await get_pool()
        await pool.ping()
        return True
    except Exception:
        return False
