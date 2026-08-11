"""V5 (Reliability): a stand-in third-party payment gateway, over real HTTP.

Through V4, app/payment/service.py was an in-process `random.random()` call.
That made V5's requirements literally unimplementable: there is no way to
time out a function call that never touches the network, and no way to
inject "payment timeout" or "50% API failure" into it. So the stub moves out
of the process and behind an HTTP boundary, where a timeout, a connection
refusal, and a 503 are all real things that can happen.

This is deliberately a separate application, not a route on the main app: it
must be able to hang, fail, and be killed without the app being able to
"cheat" by sharing its process. Locally it's a Docker Compose service; in
AWS it's a sidecar container in the same ECS task (see infra/modules/ecs).
A real integration would simply point payment_gateway_url at the provider's
public endpoint and delete this package -- see
docs/adr/ADR-006-reliability.md.

Run directly:

    uvicorn fake_gateway.main:app --host 0.0.0.0 --port 9000
"""

import logging
import os
import random
import threading
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fake_gateway")


class ChaosConfig(BaseModel):
    """The knobs the V5 experiments turn.

    All four spec-mandated failure modes are expressible here:
      * "payment timeout"    -> hang_rate + hang_ms above the client's read timeout
      * "50% API failure"    -> error_rate = 0.5
      * slow-but-alive       -> latency_ms below the client's read timeout
      * ordinary decline     -> success_rate (a business answer, not a fault)
    """

    # Fraction of requests answered with HTTP 503. Retryable: the gateway is
    # telling us it couldn't process the request at all.
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    # Fraction of requests deliberately held longer than the client will wait.
    hang_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    hang_ms: int = Field(default=10_000, ge=0)

    # Baseline added latency on every request (still answered).
    latency_ms: int = Field(default=0, ge=0)

    # Of the requests that *are* processed, the fraction that the card issuer
    # approves. A decline is a successful API call with a negative business
    # outcome -- never retried, unlike the failures above.
    success_rate: float = Field(default=0.8, ge=0.0, le=1.0)


def _chaos_from_env() -> ChaosConfig:
    return ChaosConfig(
        error_rate=float(os.getenv("GATEWAY_ERROR_RATE", "0")),
        hang_rate=float(os.getenv("GATEWAY_HANG_RATE", "0")),
        hang_ms=int(os.getenv("GATEWAY_HANG_MS", "10000")),
        latency_ms=int(os.getenv("GATEWAY_LATENCY_MS", "0")),
        success_rate=float(os.getenv("GATEWAY_SUCCESS_RATE", "0.8")),
    )


class ChargeRequest(BaseModel):
    order_id: int
    amount: float


class ChargeResponse(BaseModel):
    order_id: int
    status: str
    transaction_id: str
    amount: float
    # True when this response was replayed from the idempotency store rather
    # than being a fresh charge -- the observable proof that the retry did
    # not charge the card twice.
    replayed: bool = False


_chaos = _chaos_from_env()

# Idempotency-Key -> the response first returned for that key. In-memory on
# purpose: a real provider persists this, but the lesson (a retried charge
# with a stable key returns the original result instead of charging again) is
# identical, and losing it on restart is fine for a stand-in.
_charges: dict[str, ChargeResponse] = {}
_charges_lock = threading.Lock()

# Every charge actually executed, including ones whose response the client
# never saw because it timed out first. The chaos harness reads this to prove
# that N orders produced exactly N charges, not N + retries.
_charge_attempts: list[str] = []

app = FastAPI(title="Fake Payment Gateway", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "charges_stored": len(_charges)}


@app.post("/admin/chaos")
def set_chaos(config: ChaosConfig) -> ChaosConfig:
    """Change failure behaviour at runtime.

    Without this, every experiment step would need a container restart, which
    would also wipe the idempotency store mid-test. An admin endpoint on a
    fake dependency is fine; the real lesson is that you want to be able to
    change fault injection faster than you can redeploy.
    """
    global _chaos
    _chaos = config
    logger.info("chaos updated to %s", config.model_dump())
    return _chaos


@app.get("/admin/chaos")
def get_chaos() -> ChaosConfig:
    return _chaos


@app.post("/admin/reset")
def reset() -> dict:
    """Clear the idempotency store, the attempt log, and all chaos settings."""
    global _chaos
    with _charges_lock:
        _charges.clear()
        _charge_attempts.clear()
    _chaos = ChaosConfig()
    return {"status": "reset"}


@app.get("/admin/charges")
def list_charges() -> dict:
    """Counts the client can assert against: distinct keys vs. real charges."""
    with _charges_lock:
        return {
            "distinct_idempotency_keys": len(_charges),
            "charges_executed": len(_charge_attempts),
        }


@app.post("/charge", response_model=ChargeResponse, status_code=200)
def charge(
    request: ChargeRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    key = idempotency_key or f"anonymous:{uuid.uuid4()}"

    # Replay comes first, before any chaos: a provider that already has a
    # result for this key must return it even while it's otherwise failing.
    # That ordering is exactly what makes a retry safe -- if chaos could
    # reject a known key, the client could never learn the original outcome.
    with _charges_lock:
        existing = _charges.get(key)
    if existing is not None:
        logger.info("replaying stored charge for idempotency_key=%s", key)
        return existing.model_copy(update={"replayed": True})

    if _chaos.latency_ms:
        time.sleep(_chaos.latency_ms / 1000)

    if _chaos.hang_rate and random.random() < _chaos.hang_rate:
        # The nastiest failure mode, and the reason PAYMENT_PENDING exists:
        # the client gives up first, so it never learns whether the charge
        # below happened. Nothing is recorded here on purpose -- the request
        # is still "in flight" from the gateway's point of view.
        logger.warning("hanging for %dms on idempotency_key=%s", _chaos.hang_ms, key)
        time.sleep(_chaos.hang_ms / 1000)

    if _chaos.error_rate and random.random() < _chaos.error_rate:
        logger.warning("injecting 503 for idempotency_key=%s", key)
        raise HTTPException(status_code=503, detail="gateway temporarily unavailable")

    approved = random.random() < _chaos.success_rate
    response = ChargeResponse(
        order_id=request.order_id,
        status="SUCCESS" if approved else "FAILED",
        transaction_id=str(uuid.uuid4()),
        amount=request.amount,
    )
    with _charges_lock:
        _charges[key] = response
        _charge_attempts.append(key)
    logger.info("charged order_id=%s status=%s key=%s", request.order_id, response.status, key)
    return response
