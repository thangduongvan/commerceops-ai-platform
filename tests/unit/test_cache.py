"""V3 (Caching): app/core/cache.py must degrade to a cache miss -- not an
exception -- whenever Redis is unreachable, since Redis is never the
source of truth (see docs/adr/ADR-004-caching.md, "what happens when
Redis dies?"). No mocking needed: the default settings.redis_url points
at the "redis" Docker Compose hostname, which doesn't resolve outside a
container, so these calls exercise the exact same failure path a dead
Redis would hit in production.
"""

from app.core.cache import _jittered_ttl, cache_delete, cache_get_json, cache_set_json, mark_event_processed


def test_jittered_ttl_stays_within_plus_minus_10_percent():
    for _ in range(50):
        ttl = _jittered_ttl(100)
        assert 90 <= ttl <= 110


def test_jittered_ttl_handles_small_base_values():
    # 10% of a small TTL rounds to 0 jitter without the max(1, ...) floor in
    # _jittered_ttl -- that floor keeps expirations from all landing on the
    # exact same second even for short TTLs.
    for _ in range(50):
        ttl = _jittered_ttl(1)
        assert ttl >= 0


def test_cache_get_json_returns_none_when_redis_unreachable():
    assert cache_get_json("some-key") is None


def test_cache_set_json_and_cache_delete_do_not_raise_when_redis_unreachable():
    cache_set_json("some-key", {"a": 1}, ttl_seconds=10)
    cache_delete("some-key")


def test_mark_event_processed_fails_open_when_redis_unreachable():
    # V4: an unreachable Redis must not stop app/worker.py from making
    # progress -- it just means the dedup guard can't do its job, so this
    # degrades to "treat it as not a duplicate" (at-least-once semantics
    # preserved, worst case an event's handlers run more than once).
    assert mark_event_processed("some-event-id", ttl_seconds=60) is True
