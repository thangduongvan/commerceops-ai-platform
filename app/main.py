import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

# Import models so they register on Base's metadata before create_all runs.
from app.customer import models as customer_models  # noqa: F401
from app.customer.router import router as customer_router
from app.core import models as core_models  # noqa: F401
from app.core.cache import cache_ping
from app.core.config import settings
from app.core.database import Base, engine, get_db
from app.core.queue import queue_reachable
from app.payment import gateway_client
from app.order import models as order_models  # noqa: F401
from app.order.router import router as order_router
from app.payment.router import router as payment_router
from app.product import models as product_models  # noqa: F401
from app.product.router import router as product_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/health", tags=["health"])
def health_check():
    """Liveness, for the ALB target group. Deliberately shallow.

    V5 (Reliability): it is tempting to make the load balancer's health check
    verify the database, Redis, and the queue. That is a trap. The ALB uses
    this to decide whether to keep sending traffic to a task, so a check that
    fails when a shared dependency fails marks *every* task unhealthy
    simultaneously — the target group empties, the ALB starts returning 503 to
    everything, and ECS begins killing and replacing tasks that were working
    fine. A degraded system becomes a completely dead one, and the cause is
    the health check rather than the outage.

    So this answers exactly one question: is this process alive and serving
    HTTP? Product reads still work off cache, cancellations still work, and
    the failure stays scoped to the dependency that actually broke. Dependency
    state is reported by /health/ready below, which nothing automated acts on.
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def readiness_check(db: Session = Depends(get_db)):
    """Deep dependency probe, for humans and dashboards.

    Always returns HTTP 200, with `status` set to "ok" or "degraded" in the
    body. That's on purpose: this exists to tell an operator *what* is broken,
    and returning a failing status code would invite something automated to
    start restarting things — reintroducing exactly the cascade the shallow
    check above avoids.

    Reports circuit-breaker state alongside raw reachability, because "the
    gateway is reachable but the breaker is open" is a real and confusing
    situation (the breaker is waiting out its recovery window) that raw pings
    can't explain.
    """
    checks: dict[str, object] = {}

    # Probes through the same get_db session every request uses, rather than
    # opening its own connection: a probe that takes a different path can report
    # healthy while real traffic fails (or the reverse).
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": exc.__class__.__name__}

    # Redis being down is explicitly not a readiness failure — every cache
    # helper falls through to Postgres (V3's graceful degradation).
    checks["redis"] = {"ok": cache_ping(), "required": False}
    checks["queue"] = {"ok": queue_reachable(), "required": False}
    checks["payment_gateway"] = gateway_client.probe()

    degraded = (
        not checks["database"]["ok"]
        or not checks["redis"]["ok"]
        or not checks["queue"]["ok"]
        or not checks["payment_gateway"].get("reachable")
        or checks["payment_gateway"].get("circuit_state") != "CLOSED"
    )

    return {
        "status": "degraded" if degraded else "ok",
        "environment": settings.environment,
        "checks": checks,
    }


app.include_router(customer_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(payment_router)
