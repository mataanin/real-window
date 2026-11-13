"""Pydantic models for the API."""
from __future__ import annotations
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field


class LookupRequest(BaseModel):
    """Request to create a new lookup job."""
    address: str = Field(..., description="Property address to lookup")
    sites: Optional[list[str]] = Field(None, description="List of sites to search (default: all)")
    force_refresh: bool = Field(False, description="Force refresh ignoring cache")


class LookupStatus(str, Enum):
    """Status of a lookup job."""
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class SiteStatus(str, Enum):
    """Status of a site lookup."""
    PENDING = "pending"
    RUNNING = "running"
    FOUND = "found"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    LOGIN_REQUIRED = "login_required"
    ERROR = "error"


class SiteProgress(BaseModel):
    """Progress information for a site lookup."""
    site: str
    status: SiteStatus
    url: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class LookupResponse(BaseModel):
    """Response for a lookup job."""
    job_id: str
    status: LookupStatus
    address: Optional[str] = None
    progress: list[SiteProgress] = Field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    report_md: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class SiteInfo(BaseModel):
    """Information about a supported site."""
    name: str
    label: str
    description: str


class SessionInfo(BaseModel):
    """Session/login status for a site."""
    site: str
    state: str
    cookies: int
    detail: Optional[str] = None
    hint: Optional[str] = None


class SessionsResponse(BaseModel):
    """Response for session check."""
    sessions: list[SessionInfo]
