from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str
    description: str | None = None
    price: float = Field(gt=0)
    stock_quantity: int = Field(ge=0, default=0)


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = Field(default=None, gt=0)
    stock_quantity: int | None = Field(default=None, ge=0)


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    price: float
    stock_quantity: int
    created_at: datetime
    updated_at: datetime


# V7: internal stock APIs called by the Order service over HTTP.
class StockItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class StockReserveRequest(BaseModel):
    items: list[StockItemRequest] = Field(min_length=1)


class StockReservedItem(BaseModel):
    product_id: int
    quantity: int
    unit_price: float


class StockReserveResponse(BaseModel):
    items: list[StockReservedItem]


class StockReleaseRequest(BaseModel):
    items: list[StockItemRequest] = Field(min_length=1)
