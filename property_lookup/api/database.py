"""SQLite database for job tracking and result caching."""
from __future__ import annotations
import datetime as dt
import os
from typing import Optional
import aiosqlite


DEFAULT_CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "24"))


class Database:
    """Async SQLite database for jobs and cache."""
    
    def __init__(self, db_path: str = "property_lookup.db"):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def init(self):
        """Initialize database connection and create tables."""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                address TEXT NOT NULL,
                sites TEXT,
                status TEXT NOT NULL,
                progress TEXT,
                result TEXT,
                report_md TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                address_slug TEXT PRIMARY KEY,
                result TEXT NOT NULL,
                report_md TEXT,
                progress TEXT,
                cached_at TEXT NOT NULL
            )
        """)
        
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cache_cached_at ON cache(cached_at)
        """)
        
        await self.conn.commit()
    
    async def close(self):
        """Close database connection."""
        if self.conn:
            await self.conn.close()
    
    async def create_job(
        self,
        job_id: str,
        address: str,
        sites: Optional[list[str]],
        status: str
    ):
        """Create a new job record."""
        await self.conn.execute(
            """
            INSERT INTO jobs (job_id, address, sites, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                job_id,
                address,
                ",".join(sites) if sites else None,
                status,
                dt.datetime.utcnow().isoformat()
            )
        )
        await self.conn.commit()
    
    async def update_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        progress: Optional[str] = None,
        result: Optional[str] = None,
        report_md: Optional[str] = None,
        error: Optional[str] = None
    ):
        """Update a job record."""
        updates = []
        values = []
        
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if progress is not None:
            updates.append("progress = ?")
            values.append(progress)
        if result is not None:
            updates.append("result = ?")
            values.append(result)
        if report_md is not None:
            updates.append("report_md = ?")
            values.append(report_md)
        if error is not None:
            updates.append("error = ?")
            values.append(error)
        
        if status in ("done", "failed"):
            updates.append("completed_at = ?")
            values.append(dt.datetime.utcnow().isoformat())
        
        if not updates:
            return
        
        values.append(job_id)
        
        await self.conn.execute(
            f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?",
            values
        )
        await self.conn.commit()
    
    async def get_job(self, job_id: str) -> Optional[dict]:
        """Get a job by ID."""
        async with self.conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def cache_result(
        self,
        address_slug: str,
        result: str,
        report_md: Optional[str] = None,
        progress: Optional[str] = None
    ):
        """Cache a lookup result."""
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO cache (address_slug, result, report_md, progress, cached_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                address_slug,
                result,
                report_md,
                progress,
                dt.datetime.utcnow().isoformat()
            )
        )
        await self.conn.commit()
    
    async def get_cached_result(
        self,
        address_slug: str,
        ttl_hours: int = DEFAULT_CACHE_TTL_HOURS
    ) -> Optional[dict]:
        """Get a cached result if it's still fresh."""
        cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=ttl_hours)).isoformat()
        
        async with self.conn.execute(
            """
            SELECT * FROM cache
            WHERE address_slug = ? AND cached_at > ?
            """,
            (address_slug, cutoff)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def cleanup_old_cache(self, ttl_hours: int = DEFAULT_CACHE_TTL_HOURS):
        """Remove expired cache entries."""
        cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=ttl_hours)).isoformat()
        await self.conn.execute(
            "DELETE FROM cache WHERE cached_at <= ?",
            (cutoff,)
        )
        await self.conn.commit()
