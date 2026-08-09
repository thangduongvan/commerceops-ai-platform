import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Import models so they register on Base's metadata before create_all runs.
from app.customer import models as customer_models  # noqa: F401
from app.customer.router import router as customer_router
from app.core.config import settings
from app.core.database import Base, engine
from app.order import models as order_models  # noqa: F401
from app.order.router import router as order_router
from app.payment.router import router as payment_router
from app.product import models as product_models  # noqa: F401
from app.product.router import router as product_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}


app.include_router(customer_router)
app.include_router(product_router)
app.include_router(order_router)
app.include_router(payment_router)
