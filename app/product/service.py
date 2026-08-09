from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.product.models import Product
from app.product.schemas import ProductCreate, ProductUpdate


def create_product(db: Session, payload: ProductCreate) -> Product:
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    return product


def list_products(db: Session, skip: int = 0, limit: int = 50) -> list[Product]:
    stmt = select(Product).order_by(Product.id).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars())


def update_product(db: Session, product_id: int, payload: ProductUpdate) -> Product:
    product = get_product(db, product_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product
