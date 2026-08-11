"""V5 (Reliability): app/core/reliability.py's three primitives.

All three are tested with injected time -- a recording `sleep` for the retry
ladder and a manually-advanced clock for the breaker -- so the suite stays
instant and deterministic. Sleeping for real would make the 1s/2s/4s ladder
alone take 7 seconds and turn timing into a source of flakiness.
"""

import threading
import time

import pytest

from app.core.reliability import (
    Bulkhead,
    BulkheadFullError,
    CircuitBreaker,
    CircuitOpenError,
    RetriesExhausted,
    backoff_delays,
    retry_with_backoff,
)


class Boom(Exception):
    pass


class NotRetryable(Exception):
    pass


class RecordingSleep:
    """Stands in for time.sleep, capturing what the retry policy asked for."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


# --- backoff ladder ---------------------------------------------------------


def test_default_ladder_matches_the_v5_spec_sequence():
    # The spec asks for 1s / 2s / 4s / 8s. With attempts=4 there are three
    # waits, and a fourth attempt would wait 8s -- capped by max_delay.
    assert backoff_delays(attempts=4, base_delay=1.0, multiplier=2.0, max_delay=8.0) == [1.0, 2.0, 4.0]
    assert backoff_delays(attempts=5, base_delay=1.0, multiplier=2.0, max_delay=8.0) == [1.0, 2.0, 4.0, 8.0]


def test_ladder_is_capped_at_max_delay():
    # A longer budget must flatten into a steady poll rather than growing
    # without bound -- otherwise attempt 10 would wait 512 seconds.
    delays = backoff_delays(attempts=8, base_delay=1.0, multiplier=2.0, max_delay=8.0)
    assert delays == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]


def test_single_attempt_never_sleeps():
    assert backoff_delays(attempts=1, base_delay=1.0, multiplier=2.0, max_delay=8.0) == []


# --- retry ------------------------------------------------------------------


def test_returns_immediately_on_success_without_sleeping():
    sleep = RecordingSleep()
    calls = []

    result = retry_with_backoff(lambda: calls.append(1) or "ok", sleep=sleep)

    assert result == "ok"
    assert len(calls) == 1
    assert sleep.delays == []


def test_retries_until_success_and_sleeps_between_attempts():
    sleep = RecordingSleep()
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Boom()
        return "recovered"

    result = retry_with_backoff(
        flaky, attempts=4, base_delay=1.0, jitter=False, retry_on=(Boom,), sleep=sleep
    )

    assert result == "recovered"
    assert attempts["n"] == 3
    assert sleep.delays == [1.0, 2.0]


def test_exhausting_the_budget_raises_and_chains_the_last_error():
    sleep = RecordingSleep()
    attempts = {"n": 0}

    def always_fails():
        attempts["n"] += 1
        raise Boom("still broken")

    with pytest.raises(RetriesExhausted) as excinfo:
        retry_with_backoff(
            always_fails, attempts=3, base_delay=1.0, jitter=False, retry_on=(Boom,), sleep=sleep
        )

    assert attempts["n"] == 3
    assert sleep.delays == [1.0, 2.0]
    # The original cause has to survive, or the caller can't tell a timeout
    # from a 503 and can't decide between FAILED and UNKNOWN.
    assert isinstance(excinfo.value.__cause__, Boom)


def test_exceptions_outside_retry_on_are_not_retried():
    sleep = RecordingSleep()
    attempts = {"n": 0}

    def raises_non_retryable():
        attempts["n"] += 1
        raise NotRetryable()

    # A deterministic failure (bad payload, 400, bad credentials) will fail
    # identically every time. Retrying it multiplies load and delays the error
    # the caller needs to see, so it must propagate on the first attempt.
    with pytest.raises(NotRetryable):
        retry_with_backoff(raises_non_retryable, attempts=4, retry_on=(Boom,), sleep=sleep)

    assert attempts["n"] == 1
    assert sleep.delays == []


def test_jitter_keeps_every_delay_within_zero_and_the_ladder_value():
    sleep = RecordingSleep()

    def always_fails():
        raise Boom()

    with pytest.raises(RetriesExhausted):
        retry_with_backoff(
            always_fails,
            attempts=4,
            base_delay=1.0,
            multiplier=2.0,
            jitter=True,
            retry_on=(Boom,),
            sleep=sleep,
        )

    ladder = [1.0, 2.0, 4.0]
    assert len(sleep.delays) == 3
    for actual, ceiling in zip(sleep.delays, ladder):
        assert 0 <= actual <= ceiling


def test_jitter_actually_varies_between_runs():
    # Without variation, every caller that failed at the same instant retries
    # at the same instant -- the retry storm jitter exists to break up.
    observed = set()
    for _ in range(30):
        sleep = RecordingSleep()
        with pytest.raises(RetriesExhausted):
            retry_with_backoff(
                lambda: (_ for _ in ()).throw(Boom()),
                attempts=2,
                base_delay=1.0,
                jitter=True,
                retry_on=(Boom,),
                sleep=sleep,
            )
        observed.add(sleep.delays[0])

    assert len(observed) > 1


# --- circuit breaker --------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def _breaker(clock, threshold=3, recovery=30.0):
    return CircuitBreaker("test", failure_threshold=threshold, recovery_seconds=recovery, clock=clock)


def test_breaker_starts_closed_and_passes_calls_through(clock):
    breaker = _breaker(clock)
    assert breaker.state == CircuitBreaker.CLOSED
    assert breaker.call(lambda: "ok") == "ok"


def test_breaker_opens_after_threshold_consecutive_failures(clock):
    breaker = _breaker(clock, threshold=3)

    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(lambda: (_ for _ in ()).throw(Boom()))
        assert breaker.state == CircuitBreaker.CLOSED

    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    assert breaker.state == CircuitBreaker.OPEN


def test_open_breaker_fails_fast_without_invoking_the_dependency(clock):
    breaker = _breaker(clock, threshold=1)
    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))

    calls = []
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: calls.append(1))

    # Not calling the dependency is the entire value: no thread is held, no
    # timeout is waited out, the caller gets an answer immediately.
    assert calls == []


def test_a_success_resets_the_failure_count_before_the_threshold(clock):
    breaker = _breaker(clock, threshold=3)

    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(lambda: (_ for _ in ()).throw(Boom()))

    breaker.call(lambda: "ok")

    # Consecutive, not cumulative: occasional isolated failures over a long
    # period are normal and must not eventually trip the breaker.
    for _ in range(2):
        with pytest.raises(Boom):
            breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    assert breaker.state == CircuitBreaker.CLOSED


def test_breaker_moves_to_half_open_after_the_recovery_window(clock):
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    assert breaker.state == CircuitBreaker.OPEN

    clock.advance(29)
    assert breaker.state == CircuitBreaker.OPEN

    clock.advance(2)
    assert breaker.state == CircuitBreaker.HALF_OPEN


def test_half_open_success_closes_the_breaker(clock):
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    clock.advance(31)

    assert breaker.call(lambda: "recovered") == "recovered"
    assert breaker.state == CircuitBreaker.CLOSED


def test_half_open_failure_reopens_immediately(clock):
    breaker = _breaker(clock, threshold=5, recovery=30.0)
    for _ in range(5):
        with pytest.raises(Boom):
            breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    clock.advance(31)
    assert breaker.state == CircuitBreaker.HALF_OPEN

    # One failed probe is enough -- the dependency has already proven itself
    # broken, so there's no reason to spend another full threshold rediscovering
    # that.
    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    assert breaker.state == CircuitBreaker.OPEN


def test_half_open_admits_only_one_trial_call(clock):
    breaker = _breaker(clock, threshold=1, recovery=30.0)
    with pytest.raises(Boom):
        breaker.call(lambda: (_ for _ in ()).throw(Boom()))
    clock.advance(31)

    started = threading.Event()
    release = threading.Event()

    def slow_trial():
        started.set()
        release.wait(timeout=2)
        return "ok"

    trial = threading.Thread(target=lambda: breaker.call(slow_trial))
    trial.start()
    started.wait(timeout=2)

    # The recovering dependency must get one probe, not the whole backlog the
    # open circuit has been accumulating.
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "second")

    release.set()
    trial.join(timeout=2)


# --- bulkhead ---------------------------------------------------------------


def test_bulkhead_allows_calls_up_to_capacity():
    bulkhead = Bulkhead("test", max_concurrency=2, acquire_timeout=0.05)
    assert bulkhead.call(lambda: "ok") == "ok"
    assert bulkhead.in_use == 0


def test_bulkhead_rejects_once_capacity_is_held():
    bulkhead = Bulkhead("test", max_concurrency=1, acquire_timeout=0.05)
    holding = threading.Event()
    release = threading.Event()

    def occupy():
        bulkhead.call(lambda: (holding.set(), release.wait(timeout=2)))

    worker = threading.Thread(target=occupy)
    worker.start()
    holding.wait(timeout=2)

    # This is the point of the bulkhead: the caller is refused quickly instead
    # of queueing behind a dependency that may never answer, so the thread
    # stays available for requests that don't touch it at all.
    with pytest.raises(BulkheadFullError):
        bulkhead.call(lambda: "should not run")

    release.set()
    worker.join(timeout=2)
    assert bulkhead.in_use == 0


def test_bulkhead_rejection_is_fast():
    bulkhead = Bulkhead("test", max_concurrency=1, acquire_timeout=0.05)
    holding = threading.Event()
    release = threading.Event()

    worker = threading.Thread(target=lambda: bulkhead.call(lambda: (holding.set(), release.wait(timeout=2))))
    worker.start()
    holding.wait(timeout=2)

    started = time.perf_counter()
    with pytest.raises(BulkheadFullError):
        bulkhead.call(lambda: None)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5

    release.set()
    worker.join(timeout=2)


def test_bulkhead_releases_capacity_even_when_the_call_raises():
    bulkhead = Bulkhead("test", max_concurrency=1, acquire_timeout=0.05)

    with pytest.raises(Boom):
        bulkhead.call(lambda: (_ for _ in ()).throw(Boom()))

    # A leaked permit would shrink the budget on every error until the
    # bulkhead permanently rejected everything -- the failure mode being
    # guarded against here.
    assert bulkhead.in_use == 0
    assert bulkhead.call(lambda: "ok") == "ok"
