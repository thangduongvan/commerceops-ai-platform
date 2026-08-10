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
