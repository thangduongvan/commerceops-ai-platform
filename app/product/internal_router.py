from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.product import service
from app.product.schemas import (
    StockReleaseRequest,
    StockReserveRequest,
    StockReserveResponse,
)

router = APIRouter(prefix="/internal/stock", tags=["internal-stock"])


@router.post("/reserve", response_model=StockReserveResponse)
def reserve_stock(payload: StockReserveRequest, db: Session = Depends(get_db)):
    """Called by Order over HTTP — not part of the public catalogue API."""
    items = [(i.product_id, i.quantity) for i in payload.items]
    reserved = service.reserve_stock(db, items)
    return StockReserveResponse(items=reserved)


@router.post("/release", status_code=204)
def release_stock(payload: StockReleaseRequest, db: Session = Depends(get_db)):
    items = [(i.product_id, i.quantity) for i in payload.items]
    service.release_stock(db, items)
    return None
