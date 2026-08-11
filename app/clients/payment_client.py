"""Order → Payment HTTP client (V7).

Replaces the in-process `app.payment.service.charge` call. Timeouts /
retries / breaker around the *payment service* are separate from the
gateway client's own stack inside the Payment service — each hop owns its
reliability budget.
"""

from __future__ import annotations

import logging

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
from app.payment.schemas import PaymentRequest, PaymentResult

logger = logging.getLogger("commerceops.clients.payment")

payment_service_breaker = CircuitBreaker(
    "payment_service",
    failure_threshold=settings.circuit_breaker_failure_threshold,
    recovery_seconds=settings.circuit_breaker_recovery_seconds,
)

payment_service_bulkhead = Bulkhead(
    "payment_service",
    max_concurrency=settings.payment_bulkhead_max_concurrency,
    acquire_timeout=settings.payment_bulkhead_acquire_timeout_seconds,
)

_timeout = httpx.Timeout(
    connect=settings.payment_connect_timeout_seconds,
    read=settings.payment_read_timeout_seconds,
    write=settings.payment_read_timeout_seconds,
    pool=settings.payment_connect_timeout_seconds,
)
_client = httpx.Client(base_url=settings.payment_service_url, timeout=_timeout)


class PaymentServiceUnavailable(Exception):
    """Transport / 5xx talking to the Payment service."""


def _charge_once(payload: PaymentRequest) -> PaymentResult:
    try:
        response = _client.post(
            "/payments",
            json={"order_id": payload.order_id, "amount": payload.amount},
        )
    except httpx.TimeoutException as exc:
        raise PaymentServiceUnavailable("timeout") from exc
    except httpx.HTTPError as exc:
        raise PaymentServiceUnavailable(exc.__class__.__name__) from exc

    if response.status_code >= 500:
        raise PaymentServiceUnavailable(f"http_{response.status_code}")
    if response.status_code >= 400:
        raise PaymentServiceUnavailable(f"http_{response.status_code}")
    return PaymentResult.model_validate(response.json())


def charge(payload: PaymentRequest) -> PaymentResult:
    def _with_retries() -> PaymentResult:
        return retry_with_backoff(
            lambda: _charge_once(payload),
            attempts=settings.payment_retry_attempts,
            base_delay=settings.retry_base_delay_seconds,
            multiplier=settings.retry_multiplier,
            max_delay=settings.retry_max_delay_seconds,
            retry_on=(PaymentServiceUnavailable,),
            name=f"payment_service_charge:order-{payload.order_id}",
        )

    try:
        return payment_service_bulkhead.call(
            lambda: payment_service_breaker.call(_with_retries)
        )
    except CircuitOpenError:
        return PaymentResult(
            order_id=payload.order_id,
            status="UNKNOWN",
            transaction_id="",
            amount=payload.amount,
            reason="circuit_open",
        )
    except BulkheadFullError:
        return PaymentResult(
            order_id=payload.order_id,
            status="UNKNOWN",
            transaction_id="",
            amount=payload.amount,
            reason="bulkhead_full",
        )
    except RetriesExhausted as exc:
        logger.error(
            "payment_service_unavailable order_id=%s reason=retries_exhausted cause=%s",
            payload.order_id,
            exc.__cause__,
        )
        return PaymentResult(
            order_id=payload.order_id,
            status="UNKNOWN",
            transaction_id="",
            amount=payload.amount,
            reason="retries_exhausted",
        )


def probe() -> dict:
    try:
        response = _client.get("/health", timeout=1.0)
        return {
            "reachable": response.status_code == 200,
            "required": False,
            "circuit_state": payment_service_breaker.state,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "required": False,
            "error": exc.__class__.__name__,
            "circuit_state": payment_service_breaker.state,
        }
