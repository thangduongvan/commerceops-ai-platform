"""V3 (Caching): thin cache-aside helpers around a Redis client.

Redis is never the source of truth for anything in this system — Postgres
is. Every helper here treats a Redis connection/timeout error as a cache
miss (or a no-op write), never as an application error. That means a dead
Redis degrades read latency, not correctness or availability (see
docs/adr/ADR-004-caching.md, "what happens when Redis dies?").
"""

import json
import logging
import random
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    max_connections=settings.redis_max_connections,
    socket_connect_timeout=settings.redis_socket_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
    # V5 (Reliability): explicitly no retry. Everything in this module
    # already fails open, so a retry would only add latency to a request
    # that is going to fall through to Postgres anyway — the fastest correct
    # response to a Redis timeout here is to give up immediately.
    retry_on_timeout=False,
)
redis_client = redis.Redis(connection_pool=_pool)


def _jittered_ttl(base_ttl_seconds: int) -> int:
    """+/-10% jitter on every write.

    Without this, a cold cache (e.g. right after a deploy, or after the
    V2 Auto Scaling group scales out) fills up with many keys written at
    roughly the same time. They would then all expire in the same instant
    under sustained load, causing a thundering herd of simultaneous DB
    queries — a cache stampede. Jitter spreads the expirations out.
    """
    jitter = max(1, int(base_ttl_seconds * 0.1))
    return base_ttl_seconds + random.randint(-jitter, jitter)


def cache_get_json(key: str) -> Any | None:
    """Best-effort cache read. Returns None on a miss OR on any Redis error."""
    if not settings.cache_enabled:
        return None
    try:
        raw = redis_client.get(key)
    except redis.RedisError:
        logger.warning("cache_get_json failed for key=%s, treating as a miss", key, exc_info=True)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("cache_get_json: could not decode cached value for key=%s", key)
        return None


def cache_set_json(key: str, value: Any, ttl_seconds: int) -> None:
    """Best-effort cache write. Failures are logged and swallowed."""
    if not settings.cache_enabled:
        return
    try:
        redis_client.set(key, json.dumps(value), ex=_jittered_ttl(ttl_seconds))
    except redis.RedisError:
        logger.warning("cache_set_json failed for key=%s", key, exc_info=True)


def cache_delete(*keys: str) -> None:
    """Best-effort cache invalidation. Failures are logged and swallowed:
    a delete that doesn't reach Redis just means the key expires on its own
    TTL later, not that the write was lost (Postgres already committed)."""
    if not keys:
        return
    try:
        redis_client.delete(*keys)
    except redis.RedisError:
        logger.warning("cache_delete failed for keys=%s", keys, exc_info=True)


def cache_set_if_absent(key: str, ttl_seconds: int, value: str = "1") -> bool:
    """Atomic "claim this key if nobody else has" (Redis SET NX EX).

    Returns True if this caller set the key, False if it already existed.

    V4 introduced this as the worker's whole idempotency guard. V5 demotes it
    to what it actually is — a best-effort, expiring *lease*, used by
    app/core/idempotency.py to stop two workers doing the same work at the
    same time. It is explicitly not a durable record that work was done;
    Postgres holds that now, because a lease that can vanish (Redis restart,
    eviction, network partition) cannot be the thing preventing a duplicate
    charge. See docs/adr/ADR-006-reliability.md.

    Fails open: if Redis is unreachable this returns True ("claim granted"),
    so a dead Redis degrades to "two workers might duplicate some work, and
    the authoritative store will reject the second one" rather than "the
    worker stops making progress." Same trade-off as every other helper here.
    """
    try:
        return bool(redis_client.set(key, value, nx=True, ex=ttl_seconds))
    except redis.RedisError:
        logger.warning("cache_set_if_absent failed for key=%s, proceeding anyway", key, exc_info=True)
        return True


def cache_ping() -> bool:
    """V5: is Redis reachable right now? Used by the deep health probe in
    app/main.py. Never raises."""
    try:
        return bool(redis_client.ping())
    except redis.RedisError:
        return False
