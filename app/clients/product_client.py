"""Order → Product HTTP client (V7).

Stock reserve/release used to be in-process mutations of Product rows in
the shared DB. After database-per-service those rows are unreachable, so
Order talks to Product over the network with the same reliability stack
as the payment gateway (timeout / retry / breaker / bulkhead).
"""

from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger("commerceops.clients.product")

product_breaker = CircuitBreaker(
    "product_service",
    failure_threshold=settings.circuit_breaker_failure_threshold,
    recovery_seconds=settings.circuit_breaker_recovery_seconds,
)

product_bulkhead = Bulkhead(
    "product_service",
    max_concurrency=settings.product_bulkhead_max_concurrency,
    acquire_timeout=settings.product_bulkhead_acquire_timeout_seconds,
)

_timeout = httpx.Timeout(
    connect=settings.product_connect_timeout_seconds,
    read=settings.product_read_timeout_seconds,
    write=settings.product_read_timeout_seconds,
    pool=settings.product_connect_timeout_seconds,
)
_client = httpx.Client(base_url=settings.product_service_url, timeout=_timeout)


class ProductServiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class ProductServiceUnavailable(Exception):
    """Transport / 5xx — retryable."""


def _reserve_once(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        response = _client.post("/internal/stock/reserve", json={"items": items})
    except httpx.TimeoutException as exc:
        raise ProductServiceUnavailable("timeout") from exc
    except httpx.HTTPError as exc:
        raise ProductServiceUnavailable(exc.__class__.__name__) from exc

    if response.status_code >= 500:
        raise ProductServiceUnavailable(f"http_{response.status_code}")
    if response.status_code >= 400:
        detail = (
            response.json().get("detail", response.text) if response.content else response.text
        )
        raise ProductServiceError(response.status_code, str(detail))
    return response.json()["items"]


def reserve(items: list[dict[str, int]]) -> list[dict[str, Any]]:
    """Reserve stock; returns [{product_id, quantity, unit_price}, ...]."""

    def _with_retries() -> list[dict[str, Any]]:
        return retry_with_backoff(
            lambda: _reserve_once(items),
            attempts=settings.product_retry_attempts,
            base_delay=settings.retry_base_delay_seconds,
            multiplier=settings.retry_multiplier,
            max_delay=settings.retry_max_delay_seconds,
            retry_on=(ProductServiceUnavailable,),
            name="product_reserve",
        )

    try:
        return product_bulkhead.call(lambda: product_breaker.call(_with_retries))
    except CircuitOpenError as exc:
        raise ProductServiceUnavailable("circuit_open") from exc
    except BulkheadFullError as exc:
        raise ProductServiceUnavailable("bulkhead_full") from exc
    except RetriesExhausted as exc:
        raise ProductServiceUnavailable("retries_exhausted") from exc


def _release_once(items: list[dict[str, Any]]) -> None:
    try:
        response = _client.post("/internal/stock/release", json={"items": items})
    except httpx.TimeoutException as exc:
        raise ProductServiceUnavailable("timeout") from exc
    except httpx.HTTPError as exc:
        raise ProductServiceUnavailable(exc.__class__.__name__) from exc

    if response.status_code >= 500:
        raise ProductServiceUnavailable(f"http_{response.status_code}")
    if response.status_code >= 400 and response.status_code != 204:
        detail = (
            response.json().get("detail", response.text) if response.content else response.text
        )
        raise ProductServiceError(response.status_code, str(detail))


def release(items: list[dict[str, int]]) -> None:
    def _with_retries() -> None:
        return retry_with_backoff(
            lambda: _release_once(items),
            attempts=settings.product_retry_attempts,
            base_delay=settings.retry_base_delay_seconds,
            multiplier=settings.retry_multiplier,
            max_delay=settings.retry_max_delay_seconds,
            retry_on=(ProductServiceUnavailable,),
            name="product_release",
        )

    try:
        product_bulkhead.call(lambda: product_breaker.call(_with_retries))
    except CircuitOpenError as exc:
        raise ProductServiceUnavailable("circuit_open") from exc
    except BulkheadFullError as exc:
        raise ProductServiceUnavailable("bulkhead_full") from exc
    except RetriesExhausted as exc:
        raise ProductServiceUnavailable("retries_exhausted") from exc


def probe() -> dict:
    try:
        response = _client.get("/health", timeout=1.0)
        return {
            "reachable": response.status_code == 200,
            "required": False,
            "circuit_state": product_breaker.state,
        }
    except Exception as exc:
        return {
            "reachable": False,
            "required": False,
            "error": exc.__class__.__name__,
            "circuit_state": product_breaker.state,
        }
