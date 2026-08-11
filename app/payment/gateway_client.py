"""V5 (Reliability): the HTTP client for the third-party payment gateway.

This is the one place in the codebase where all four reliability primitives
compose, and the order they compose in matters:

    bulkhead -> circuit breaker -> retry -> HTTP call (with timeouts)

Read outermost-in:

* **Bulkhead first.** It is the only layer that protects resources *other
  than* the payment path. It must reject before a thread is committed to a
  call that might hang, otherwise the retry ladder below is exactly what
  consumes the thread pool.
* **Breaker second.** Once a dependency is known to be down, skip the retry
  ladder entirely -- there's no point spending 1+2+4 seconds of timeouts to
  rediscover that. Placing it outside the retry also means one open circuit
  counts as one failure, not four.
* **Retry innermost.** Retries only make sense around a single attempt, and
  each attempt's failure is what the breaker should be counting.

If retry sat outside the breaker instead, every request would trip the
breaker on its own (4 failures against a threshold of 5) and reopen it
immediately after each recovery probe.

See docs/adr/ADR-006-reliability.md.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.core.reliability import (
    Bulkhead,
    BulkheadFullError,
    CircuitBreaker,
    CircuitOpenError,
    RetriesExhausted,
    retry_with_backoff,
)

logger = logging.getLogger("commerceops.payment.gateway")

# Outcome constants. UNKNOWN is the one V5 adds, and the whole reason
# app/order/service.py needed a new order status: it does not mean "the
# charge failed", it means "we don't know whether the charge happened."
SUCCESS = "SUCCESS"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"


@dataclass
class GatewayOutcome:
    status: str
    transaction_id: Optional[str] = None
    # Why we ended up here, for logs and the deep health probe: "approved",
    # "declined", "timeout", "circuit_open", "bulkhead_full", "http_500", ...
    reason: str = ""


class GatewayUnavailable(Exception):
    """A transport-level failure or a 5xx: the gateway did not process the
    request, so retrying is safe and correct."""


class GatewayDeclined(Exception):
    """The gateway processed the request and said no. Never retried -- the
    answer will not change, and hammering it turns one decline into five."""


payment_breaker = CircuitBreaker(
    "payment_gateway",
    failure_threshold=settings.circuit_breaker_failure_threshold,
    recovery_seconds=settings.circuit_breaker_recovery_seconds,
)

payment_bulkhead = Bulkhead(
    "payment_gateway",
    max_concurrency=settings.payment_bulkhead_max_concurrency,
    acquire_timeout=settings.payment_bulkhead_acquire_timeout_seconds,
)

# One shared client so connections are pooled, with an explicit timeout on
# every phase. httpx's default is 5s across the board; leaving it implicit is
# how "every external call must have a timeout" quietly becomes "every
# external call has whatever timeout the library shipped with."
_timeout = httpx.Timeout(
    connect=settings.payment_connect_timeout_seconds,
    read=settings.payment_read_timeout_seconds,
    write=settings.payment_read_timeout_seconds,
    pool=settings.payment_connect_timeout_seconds,
)
_client = httpx.Client(base_url=settings.payment_gateway_url, timeout=_timeout)


def _post_charge(order_id: int, amount: float, idempotency_key: str) -> GatewayOutcome:
    """A single attempt. Raises GatewayUnavailable for anything retryable."""
    try:
        response = _client.post(
            "/charge",
            json={"order_id": order_id, "amount": amount},
            headers={"Idempotency-Key": idempotency_key},
        )
    except httpx.TimeoutException as exc:
        raise GatewayUnavailable(f"timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        raise GatewayUnavailable(f"transport error: {exc}") from exc

    if response.status_code >= 500:
        raise GatewayUnavailable(f"http_{response.status_code}")
    if response.status_code >= 400:
        # A 4xx is our own bug (bad payload, bad auth). Retrying it is pure
        # waste, and the breaker shouldn't count it against the dependency.
        raise GatewayDeclined(f"http_{response.status_code}")

    body = response.json()
    if body.get("status") == SUCCESS:
        return GatewayOutcome(SUCCESS, body.get("transaction_id"), reason="approved")
    return GatewayOutcome(FAILED, body.get("transaction_id"), reason="declined")


def charge(order_id: int, amount: float) -> GatewayOutcome:
    """Charge an order, returning SUCCESS, FAILED, or UNKNOWN. Never raises.

    The idempotency key is derived from the order id, so it is identical
    across every retry of every attempt for this order. That is what makes
    retrying a possibly-succeeded charge safe: the gateway recognises the key
    and replays the original result instead of charging the card again. A
    random key per attempt would turn this retry policy into a
    double-charging machine.
    """
    idempotency_key = f"order-{order_id}"

    def _attempt() -> GatewayOutcome:
        return _post_charge(order_id, amount, idempotency_key)

    def _with_retries() -> GatewayOutcome:
        return retry_with_backoff(
            _attempt,
            attempts=settings.payment_retry_attempts,
            base_delay=settings.retry_base_delay_seconds,
            multiplier=settings.retry_multiplier,
            max_delay=settings.retry_max_delay_seconds,
            retry_on=(GatewayUnavailable,),
            name=f"payment_charge:order-{order_id}",
        )

    try:
        return payment_bulkhead.call(lambda: payment_breaker.call(_with_retries))
    except BulkheadFullError:
        logger.error(
            "payment_gateway_unavailable order_id=%s reason=bulkhead_full in_use=%d",
            order_id,
            payment_bulkhead.in_use,
        )
        return GatewayOutcome(UNKNOWN, reason="bulkhead_full")
    except CircuitOpenError:
        logger.error("payment_gateway_unavailable order_id=%s reason=circuit_open", order_id)
        return GatewayOutcome(UNKNOWN, reason="circuit_open")
    except GatewayDeclined as exc:
        return GatewayOutcome(FAILED, reason=str(exc))
    except RetriesExhausted as exc:
        # Every attempt failed at the transport level. The last one may well
        # have reached the gateway and been charged -- we simply timed out
        # before hearing back. Reporting FAILED here would be a lie that
        # leads to refunding a charge that never happened, or restocking
        # goods the customer already paid for. Hence UNKNOWN.
        logger.error(
            "payment_gateway_unavailable order_id=%s reason=retries_exhausted cause=%s",
            order_id,
            exc.__cause__,
        )
        return GatewayOutcome(UNKNOWN, reason="retries_exhausted")


def probe() -> dict:
    """V5: is the gateway reachable, and what does the breaker think?

    Used by the deep /health/ready probe in app/main.py. Deliberately does
    not go through the breaker or bulkhead: a health probe should report the
    state, not consume the budget or influence it.
    """
    state = {"circuit_state": payment_breaker.state, "bulkhead_in_use": payment_bulkhead.in_use}
    try:
        response = _client.get("/health", timeout=settings.payment_connect_timeout_seconds)
        state["reachable"] = response.status_code == 200
    except httpx.HTTPError as exc:
        state["reachable"] = False
        state["error"] = exc.__class__.__name__
    return state
