"""Shared FastAPI helpers for the V7 per-service entrypoints."""

import logging
from contextlib import asynccontextmanager
from typing import Callable, Sequence

from fastapi import Depends, FastAPI
from sqlalchemy import Table, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base, engine, get_db, replica_lag_seconds
from app.core.ensure_database import ensure_database

logger = logging.getLogger(__name__)


def create_service_lifespan(tables: Sequence[Table]) -> Callable:
    """Lifespan that ensures the target DB exists, then creates only these tables."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ensure_database(settings.database_url)
        Base.metadata.create_all(bind=engine, tables=list(tables))
        yield

    return lifespan


def shallow_health() -> dict:
    """ALB/liveness probe — process alive only. See V5 ADR for why."""
    return {"status": "ok"}


def register_health_routes(
    app: FastAPI,
    *,
    check_redis: bool = False,
    check_queue: bool = False,
    check_replica: bool = False,
    check_payment_gateway: bool = False,
    extra_checks: Callable[[dict], None] | None = None,
) -> None:
    @app.get("/health", tags=["health"])
    def health_check():
        return shallow_health()

    @app.get("/health/ready", tags=["health"])
    def readiness_check(db: Session = Depends(get_db)):
        checks: dict[str, object] = {}

        try:
            db.execute(text("SELECT 1"))
            checks["database"] = {"ok": True}
        except Exception as exc:
            checks["database"] = {"ok": False, "error": exc.__class__.__name__}

        if check_redis:
            from app.core.cache import cache_ping

            checks["redis"] = {"ok": cache_ping(), "required": False}

        if check_queue:
            from app.core.queue import queue_reachable

            checks["queue"] = {"ok": queue_reachable(), "required": False}

        if check_payment_gateway:
            from app.payment import gateway_client

            checks["payment_gateway"] = gateway_client.probe()

        if check_replica:
            if settings.read_replica_enabled and settings.database_read_url:
                lag = replica_lag_seconds()
                checks["database_replica"] = {
                    "ok": lag is not None,
                    "required": False,
                    "lag_seconds": lag,
                    "max_lag_seconds": settings.db_max_replica_lag_seconds,
                    "lagging": (
                        lag is not None and lag > settings.db_max_replica_lag_seconds
                    ),
                }
            else:
                checks["database_replica"] = {
                    "ok": True,
                    "required": False,
                    "enabled": False,
                    "lag_seconds": None,
                }

        if extra_checks is not None:
            extra_checks(checks)

        degraded = not checks["database"]["ok"]
        if check_redis and not checks.get("redis", {}).get("ok", True):
            degraded = True
        if check_queue and not checks.get("queue", {}).get("ok", True):
            degraded = True
        if check_payment_gateway:
            gw = checks.get("payment_gateway", {})
            if not gw.get("reachable") or gw.get("circuit_state") != "CLOSED":
                degraded = True

        return {
            "status": "degraded" if degraded else "ok",
            "environment": settings.environment,
            "service": settings.service_name,
            "checks": checks,
        }
