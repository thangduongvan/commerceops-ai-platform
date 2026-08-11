"""Ensure a logical PostgreSQL database exists (V7 database-per-service).

RDS creates a single bootstrap database (`commerceops`). Product / Order /
Payment each need their own DB name on that instance. ECS tasks run inside
the VPC and can CREATE DATABASE; local Compose uses one Postgres container
per service so this is a no-op when the DB already exists.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)


def _maintenance_url(database_url: str) -> tuple[str, str]:
    """Return (url pointing at 'postgres', target database name)."""
    parsed = urlparse(database_url)
    # path is "/dbname"
    db_name = parsed.path.lstrip("/") or "postgres"
    if not db_name or db_name == "postgres":
        return database_url, db_name
    maint = parsed._replace(path="/postgres")
    return urlunparse(maint), db_name


def ensure_database(database_url: str) -> None:
    """CREATE DATABASE if missing. Safe to call on every startup.

    Skips for SQLite (tests) and when already connected to `postgres`.
    """
    if database_url.startswith("sqlite"):
        return

    maint_url, db_name = _maintenance_url(database_url)
    if db_name == "postgres":
        return

    engine = create_engine(maint_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": db_name},
            ).scalar()
            if exists:
                return
            # Identifiers cannot be parameterized; db_name comes from our config.
            if not db_name.replace("_", "").isalnum():
                raise ValueError(f"Refusing to create unsafe database name: {db_name!r}")
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            logger.info("created_database name=%s", db_name)
    except Exception as exc:
        # Another task may have created it concurrently, or we lack CREATEDB.
        # The real engine connect below will surface a hard failure if needed.
        logger.warning(
            "ensure_database_failed name=%s error=%s",
            db_name,
            exc.__class__.__name__,
        )
    finally:
        engine.dispose()
