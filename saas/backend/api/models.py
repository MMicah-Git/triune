"""Pydantic schemas — request/response shapes for the API."""

from typing import Literal, Optional
from pydantic import BaseModel


class JobOut(BaseModel):
    id: str
    kind: Literal['takeoff', 'addendum', 'auto_scale']
    status: Literal['queued', 'running', 'done', 'error']
    input_files: list[str]
    params: dict
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    output_dir: Optional[str] = None
    outputs: dict[str, str] = {}
    error: Optional[str] = None
    log_tail: str = ''


class JobCreated(BaseModel):
    id: str
    status: str
