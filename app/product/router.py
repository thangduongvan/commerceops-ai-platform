from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db, get_read_db
from app.product import service
from app.product.schemas import ProductCreate, ProductRead, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])


@router.post("", response_model=ProductRead, status_code=201)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    return service.create_product(db, payload)


@router.get("/{product_id}", response_model=ProductRead)
def get_product(product_id: int, db: Session = Depends(get_read_db)):
    # V6: product reads are the high-volume, safe-to-serve-stale path, so they
    # go to the asynchronous read replica. Order/customer stay on the primary
    # — a customer reading their own order immediately after placing it cannot
    # tolerate replica lag. See docs/adr/ADR-007-database-ha.md.
    return service.get_product(db, product_id)


@router.get("", response_model=list[ProductRead])
def list_products(skip: int = 0, limit: int = 50, db: Session = Depends(get_read_db)):
    return service.list_products(db, skip=skip, limit=limit)


@router.put("/{product_id}", response_model=ProductRead)
def update_product(product_id: int, payload: ProductUpdate, db: Session = Depends(get_db)):
    return service.update_product(db, product_id, payload)
