"""V5 (Reliability): retry/backoff, circuit breaker, and bulkhead primitives.

Every version up to V4 assumed its dependencies answer. V5 assumes they
don't: a payment gateway times out, a third-party API fails half the time,
a consumer crashes mid-message. These three primitives are what the rest of
the codebase composes to survive that -- see docs/adr/ADR-006-reliability.md.

They're hand-written rather than pulled from tenacity/pybreaker on purpose:
the point of this version is understanding the state machines, and ~200
lines with an injectable clock is far easier to unit-test deterministically
than a library's internal scheduling. Every failure path here logs a
structured line, because the CloudWatch log metric filters in
infra/modules/cloudwatch turn those lines into alarms -- application-level
reliability signals, not just infrastructure metrics.
"""

import logging
import random
import threading
import time
from typing import Callable, Iterable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ReliabilityError(Exception):
    """Base for every failure this module raises itself (as opposed to
    re-raising a dependency's own exception)."""


class RetriesExhausted(ReliabilityError):
    """Raised when the caller asked for a bounded retry budget and every
    attempt failed. The last underlying exception is chained as __cause__."""


class CircuitOpenError(ReliabilityError):
    """Raised instead of calling a dependency that is currently considered
    dead. The whole point: fail in microseconds rather than waiting out
    another timeout on something already known to be broken."""


class BulkheadFullError(ReliabilityError):
    """Raised when a dependency's concurrency budget is already fully in
    use. Shedding this call protects the rest of the process (see Bulkhead)."""


def backoff_delays(
    attempts: int,
    base_delay: float,
    multiplier: float,
    max_delay: float,
) -> list[float]:
    """The un-jittered delay ladder between `attempts` attempts.

    With the defaults (attempts=4, base=1.0, multiplier=2, max=8.0) this is
    the exact sequence the V5 spec asks for -- 1s, 2s, 4s -- capped at
    max_delay so a longer budget degrades into a steady poll rather than
    growing without bound. There are attempts-1 delays, because the first
    attempt doesn't wait.
    """
    delays = []
    delay = base_delay
    for _ in range(max(0, attempts - 1)):
        delays.append(min(delay, max_delay))
        delay *= multiplier
    return delays


def _jittered(delay: float) -> float:
    """Full jitter: sleep somewhere in [0, delay).

    Without this, N tasks that all failed against the same dependency at the
    same instant retry at the same instant, then again 2s later, then again
    4s later -- a retry storm that keeps the dependency down exactly when
    it's trying to recover. Same reasoning as the TTL jitter in
    app/core/cache.py, applied to time instead of expiry.
    """
    return random.uniform(0, delay)


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    multiplier: float = 2.0,
    max_delay: float = 8.0,
    jitter: bool = True,
    retry_on: Iterable[type[BaseException]] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    name: str = "call",
) -> T:
    """Call fn(), retrying transient failures with exponential backoff.

    Only exceptions in `retry_on` are retried. Everything else propagates
    immediately and untouched -- retrying a deterministic failure (a 400
    from a gateway, a malformed payload) just multiplies load and delays the
    error the caller needs to see. This allowlist is the whole difference
    between a retry policy and a busy-wait.

    Raises the last exception once the budget is exhausted, wrapped in
    RetriesExhausted so callers can distinguish "failed once" from "failed
    every time we tried".
    """
    retry_on = tuple(retry_on)
    delays = backoff_delays(attempts, base_delay, multiplier, max_delay)
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = delays[attempt - 1]
            if jitter:
                delay = _jittered(delay)
            logger.warning(
                "retry name=%s attempt=%d/%d sleeping=%.3fs error=%s",
                name,
                attempt,
                attempts,
                delay,
                exc.__class__.__name__,
            )
            sleep(delay)

    logger.error("retries_exhausted name=%s attempts=%d", name, attempts)
    raise RetriesExhausted(f"{name} failed after {attempts} attempts") from last_error


class CircuitBreaker:
    """Three-state breaker guarding one dependency.

        CLOSED  --failure_threshold consecutive failures-->  OPEN
        OPEN    --recovery_seconds elapsed-->                HALF_OPEN
        HALF_OPEN --trial call succeeds-->                   CLOSED
        HALF_OPEN --trial call fails-->                      OPEN

    Retry (above) and this are complementary, not redundant: retry handles a
    blip in one request, the breaker handles a dependency that is simply
    down. Without it, every request pays the full retry budget (1+2+4s of
    timeouts) before failing, so a dead dependency turns into an
    application-wide latency collapse -- exactly the cascade V5 exists to
    prevent.

    HALF_OPEN admits exactly one trial call, so recovery is probed with a
    single request rather than the full backlog slamming a dependency that
    has only just come back.

    The clock is injectable so tests can advance time instead of sleeping.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False

    @property
    def state(self) -> str:
        """Current state, resolving an elapsed OPEN window to HALF_OPEN."""
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state == self.OPEN and self._clock() - self._opened_at >= self.recovery_seconds:
            self._state = self.HALF_OPEN
            self._half_open_in_flight = False
            logger.warning("circuit_breaker state=HALF_OPEN name=%s", self.name)

    def _before_call(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state == self.OPEN:
                raise CircuitOpenError(f"circuit {self.name} is open")
            if self._state == self.HALF_OPEN:
                if self._half_open_in_flight:
                    raise CircuitOpenError(f"circuit {self.name} is half-open, trial already in flight")
                self._half_open_in_flight = True

    def _on_success(self) -> None:
        with self._lock:
            if self._state != self.CLOSED:
                logger.info("circuit_breaker state=CLOSED name=%s", self.name)
            self._state = self.CLOSED
            self._consecutive_failures = 0
            self._half_open_in_flight = False

    def _on_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._half_open_in_flight = False
            should_open = (
                self._state == self.HALF_OPEN
                or self._consecutive_failures >= self.failure_threshold
            )
            if should_open and self._state != self.OPEN:
                self._state = self.OPEN
                self._opened_at = self._clock()
                # Matched by the circuit-breaker log metric filter in
                # infra/modules/cloudwatch -- keep the shape stable.
                logger.error(
                    "circuit_breaker state=OPEN name=%s consecutive_failures=%d",
                    self.name,
                    self._consecutive_failures,
                )
            elif should_open:
                self._opened_at = self._clock()

    def call(self, fn: Callable[[], T]) -> T:
        """Run fn() under the breaker, or raise CircuitOpenError immediately."""
        self._before_call()
        try:
            result = fn()
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def reset(self) -> None:
        """Force back to CLOSED. Only for tests and the chaos harness."""
        with self._lock:
            self._state = self.CLOSED
            self._consecutive_failures = 0
            self._half_open_in_flight = False


class Bulkhead:
    """A bounded concurrency budget for one dependency.

    FastAPI runs `def` (non-async) endpoints in a thread pool of ~40
    threads. If the payment gateway hangs, every in-flight order request
    holds a thread waiting on it, and once all 40 are held, requests that
    touch nothing but Redis and Postgres -- product reads, health checks --
    start queueing behind a dependency they don't even use. That is a
    cascading failure caused entirely by a lack of isolation.

    Capping payment calls at max_concurrency means the (max_concurrency+1)th
    concurrent order fails fast with BulkheadFullError while the rest of the
    application stays responsive: shedding some load in one compartment
    instead of sinking the whole ship. Hence the naval metaphor.
    """

    def __init__(self, name: str, *, max_concurrency: int = 10, acquire_timeout: float = 0.5) -> None:
        self.name = name
        self.max_concurrency = max_concurrency
        self.acquire_timeout = acquire_timeout
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._in_use = 0
        self._lock = threading.Lock()

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._in_use

    def call(self, fn: Callable[[], T]) -> T:
        acquired = self._semaphore.acquire(timeout=self.acquire_timeout)
        if not acquired:
            logger.error(
                "bulkhead_rejected name=%s max_concurrency=%d",
                self.name,
                self.max_concurrency,
            )
            raise BulkheadFullError(
                f"bulkhead {self.name} full ({self.max_concurrency} concurrent calls)"
            )
        with self._lock:
            self._in_use += 1
        try:
            return fn()
        finally:
            with self._lock:
                self._in_use -= 1
            self._semaphore.release()
