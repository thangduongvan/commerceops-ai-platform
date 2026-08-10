from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cache import cache_delete, cache_get_json, cache_set_json
from app.core.config import settings
from app.product.models import Product
from app.product.schemas import ProductCreate, ProductRead, ProductUpdate


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


def get_product(db: Session, product_id: int) -> Product | dict:
    """Cache-aside read: check Redis first, fall back to Postgres on a miss
    (or on any Redis error — see app/core/cache.py). Postgres remains the
    source of truth; Redis only ever holds a short-TTL derived copy."""
    cache_key = _product_cache_key(product_id)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return cached

    product = _get_product_from_db(db, product_id)
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
    """
    cache_key = _product_list_cache_key(skip, limit)
    cached = cache_get_json(cache_key)
    if cached is not None:
        return cached

    stmt = select(Product).order_by(Product.id).offset(skip).limit(limit)
    products = list(db.execute(stmt).scalars())
    cache_set_json(cache_key, [_serialize(p) for p in products], settings.cache_ttl_seconds)
    return products


def update_product(db: Session, product_id: int, payload: ProductUpdate) -> Product:
    product = _get_product_from_db(db, product_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    # Single, deterministic key -> cheap to invalidate directly, so we do:
    # the next read is guaranteed fresh instead of waiting out the TTL.
    cache_delete(_product_cache_key(product_id))
    return product
