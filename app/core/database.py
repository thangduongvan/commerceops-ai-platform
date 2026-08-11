from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Shared declarative base for all domain modules.

    All modules mapping to tables (customer, product, order, ...) register
    on this single metadata object, since V0 is one process with one
    database (database-per-service comes later, at V7).
    """


def _connect_args() -> dict:
    """V5 (Reliability): bound how long a connection or query can hang.

    Both options are PostgreSQL/psycopg-specific, so they're only applied to
    a postgresql URL — the in-memory SQLite engine the tests build has no
    notion of either and would reject them.

    statement_timeout is the important half: pool_pre_ping already detects a
    *dead* connection, but says nothing about a query that connected fine and
    then ran forever. Without a server-side cap, that query keeps holding one
    of the pool's few connections, and enough of them starve every other
    request of the database entirely.
    """
    if not settings.database_url.startswith("postgresql"):
        return {}
    return {
        "connect_timeout": settings.db_connect_timeout_seconds,
        "options": f"-c statement_timeout={settings.db_statement_timeout_seconds * 1000}",
    }


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    connect_args=_connect_args(),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
