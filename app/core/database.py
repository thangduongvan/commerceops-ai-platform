import logging
import time
from typing import Callable, Optional, TypeVar

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Base(DeclarativeBase):
    """Shared declarative base for all domain modules.

    All modules mapping to tables (customer, product, order, ...) register
    on this single metadata object, since V0 is one process with one
    database (database-per-service is V7 — see ADR-008).
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


def _build_engine(url: str):
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # V6: recycle pooled connections so a Multi-AZ failover can't leave
        # the pool holding sockets to a primary that no longer exists.
        # pool_pre_ping catches dead ones on checkout; recycle is the bound
        # on how long a still-"alive" connection may linger.
        pool_recycle=settings.db_pool_recycle_seconds,
        connect_args=_connect_args() if url.startswith("postgresql") else {},
    )


engine = _build_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _resolve_read_engine():
    """V6: separate engine for the asynchronous read replica when configured.

    When read_replica_enabled is false or no read URL is set, the read engine
    *is* the write engine — so tests and single-DB local runs need no
    configuration, and get_read_db behaves identically to get_db.
    """
    if (
        settings.read_replica_enabled
        and settings.database_read_url
        and settings.database_read_url != settings.database_url
    ):
        return _build_engine(settings.database_read_url)
    return engine


read_engine = _resolve_read_engine()
ReadSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=read_engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db() -> Session:
    """Session bound to the read replica (or the primary when none is configured).

    Product GET endpoints use this. Order/customer stay on get_db — a customer
    reading their own order immediately after placing it cannot tolerate
    asynchronous replica lag. See docs/adr/ADR-007-database-ha.md.
    """
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


def with_read_retry(fn: Callable[[], T]) -> T:
    """Short transient-error retry for *reads only* around a Multi-AZ failover.

    A connection broken mid-request still raises even with pool_pre_ping
    (pre_ping only runs on checkout). Two quick retries (~0.2s apart) cover
    the brief DNS/connection flap of an RDS failover without holding a
    request thread through the whole 60–120s window.

    Deliberately NOT used for writes: retrying a write whose commit outcome
    is unknown is the database version of V5's PAYMENT_PENDING problem —
    the client must decide, not the server silently double-apply.
    """
    attempts = max(1, settings.db_transient_retry_attempts)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (OperationalError, DBAPIError) as exc:
            # Only retry connection-level failures, not integrity/statement errors.
            if isinstance(exc, DBAPIError) and not exc.connection_invalidated:
                if not isinstance(exc, OperationalError):
                    raise
            last_exc = exc
            if attempt >= attempts:
                break
            logger.warning(
                "db_read_transient_retry attempt=%s/%s error=%s",
                attempt,
                attempts,
                exc.__class__.__name__,
            )
            time.sleep(0.2 * attempt)
    assert last_exc is not None
    raise last_exc


def replica_lag_seconds(db: Optional[Session] = None) -> Optional[float]:
    """Seconds behind the primary, via pg_last_xact_replay_timestamp().

    Returns None when not connected to a standby (or not PostgreSQL), so
    /health/ready can report "n/a" rather than inventing a number. Own
    short-lived session when none is passed, so the health probe doesn't
    depend on a FastAPI dependency that may be bound to the primary.
    """
    if read_engine is engine and not settings.read_replica_enabled:
        return None

    owns_session = db is None
    if owns_session:
        db = ReadSessionLocal()
    try:
        in_recovery = db.execute(text("SELECT pg_is_in_recovery()")).scalar()
        if not in_recovery:
            return 0.0
        lag = db.execute(
            text(
                "SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))"
            )
        ).scalar()
        if lag is None:
            return 0.0
        return float(lag)
    except Exception as exc:
        logger.warning("replica_lag_probe_failed error=%s", exc.__class__.__name__)
        return None
    finally:
        if owns_session and db is not None:
            db.close()
