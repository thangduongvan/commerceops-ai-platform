"""Product service entrypoint (V7)."""

import logging

from fastapi import FastAPI

from app.core.config import settings
from app.core.service_app import create_service_lifespan, register_health_routes
from app.product.internal_router import router as stock_router
from app.product.models import Product
from app.product.router import router as product_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title=f"{settings.app_name} — Product",
    version="0.7.0",
    lifespan=create_service_lifespan([Product.__table__]),
)

register_health_routes(app, check_redis=True, check_replica=True)
app.include_router(product_router)
app.include_router(stock_router)
