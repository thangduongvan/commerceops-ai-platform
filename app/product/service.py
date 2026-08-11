import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core.cache import cache_delete, cache_get_json, cache_set_json
from app.core.config import settings
from app.core.database import SessionLocal, with_read_retry
from app.product.models import Product
from app.product.schemas import ProductCreate, ProductRead, ProductUpdate

logger = logging.getLogger(__name__)


def _product_cache_key(product_id: int) -> str:
    return f"product:{product_id}"


def _product_list_cache_key(skip: int, limit: int) -> str:
    return f"products:list:{skip}:{limit}"


def _serialize(product: Product) -> dict:
    # mode="json" so datetimes come out as ISO strings (JSON-serializable);
    # FastAPI's response_model still parses them back into datetimes on read.
    return ProductRead.model_validate(product).model_dump(mode="json")


def _get_product_from_db(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    return product


def create_product(db: Session, payload: ProductCreate) -> Product:
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    # Nothing to invalidate: this id has never been cached, and the list
    # caches (keyed by skip/limit, not by id) will pick it up once their
    # short TTL expires — see list_products below for why those aren't
    # actively invalidated on write.
    return product


def _is_replica_connection_error(exc: BaseException) -> bool:
    """Fall open only for connection-level failures, not statement errors.

    A lagging or unreachable replica produces connection/timeout errors.
    Application errors (syntax, missing table in a misconfigured env) must
    surface — falling open would hide them behind a primary query, and in
    tests would send SessionLocal at the real Postgres URL.
    """
    if isinstance(exc, OperationalError) and getattr(exc, "connection_invalidated", False):
        return True
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    msg = str(exc).lower()
    needles = (
        "connection",
        "could not connect",
        "server closed",
        "timeout",
        "refused",
        "getaddrinfo",
        "name or service not known",
        "network",
        "ssl",
    )
    return any(n in msg for n in needles)


def _read_with_primary_fallback(read_db: Session, fn):
    """V6: run a product read against the replica session, falling open to the
    primary on connection failure — same shape as app/core/cache.py's fail-open.

    The log line `read_replica_unavailable` is what CloudWatch turns into an
    alarm (infra/modules/cloudwatch). A request that falls back still returns
    200, so infrastructure metrics alone would never see the failure.
    """
    try:
        return with_read_retry(lambda: fn(read_db))
    except (OperationalError, DBAPIError) as exc:
        if not _is_replica_connection_error(exc):
            raise
        logger.warning(
            "read_replica_unavailable error=%s falling_back=primary",
            exc.__class__.__name__,
        )
        primary = SessionLocal()
        try:
            return fn(primary)
        finally:
            primary.close()


def get_product(db: Session, product_id: int) -> Product | dict:
    """Cache-aside read: check Redis first, fall back to Postgres on a miss
    (or on any Redis error — see app/core/cache.py). Postgres remains the
    source of truth; Redis only ever holds a short-TTL derived copy.

    V6: `db` is the read-replica session for GET endpoints (see
    app/product/router.py). On replica failure we fall open to the primary
    rather than 500 — product catalogue is safe to serve from either.
    """
    cache_key = _product_cache_key(product_id)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return cached

    def _load(session: Session) -> Product:
        return _get_product_from_db(session, product_id)

    product = _read_with_primary_fallback(db, _load)
    cache_set_json(cache_key, _serialize(product), settings.cache_ttl_seconds)
    return product


def list_products(db: Session, skip: int = 0, limit: int = 50) -> list[Product] | list[dict]:
    """Cache-aside read, same pattern as get_product, but keyed by the
    pagination params (skip/limit) rather than a single id.

    Unlike get_product, this cache is deliberately NOT actively invalidated
    when a product is created/updated: the key space is combinatorial over
    every (skip, limit) a client might request, so tracking or scanning it
    on every write is complexity this version doesn't need yet. Instead,
    staleness here is bounded by cache_ttl_seconds — an explicit eventual-
    consistency trade-off for listings, vs. the strong (delete-on-write)
    consistency get_product gives for a single product.

    V6: served from the read replica (with primary fallback); see get_product.
    """
    cache_key = _product_list_cache_key(skip, limit)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return cached

    def _load(session: Session) -> list[Product]:
        stmt = select(Product).order_by(Product.id).offset(skip).limit(limit)
        return list(session.execute(stmt).scalars())

    products = _read_with_primary_fallback(db, _load)
    cache_set_json(cache_key, [_serialize(p) for p in products], settings.cache_ttl_seconds)
    return products


def update_product(db: Session, product_id: int, payload: ProductUpdate) -> Product:
    # Reads-then-writes in one session against the primary — must not use the
    # replica, or the write half would fail (standbys are read-only) and the
    # read half could be stale relative to a concurrent update.
    product = _get_product_from_db(db, product_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    # Single, deterministic key -> cheap to invalidate directly, so we do:
    # the next read is guaranteed fresh instead of waiting out the TTL.
    cache_delete(_product_cache_key(product_id))
    return product
