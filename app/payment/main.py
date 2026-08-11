"""Payment service entrypoint (V7)."""

import logging

from fastapi import FastAPI

from app.core.config import settings
from app.core.service_app import create_service_lifespan, register_health_routes
from app.payment.models import Payment
from app.payment.router import router as payment_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title=f"{settings.app_name} — Payment",
    version="0.7.0",
    lifespan=create_service_lifespan([Payment.__table__]),
)

register_health_routes(app, check_payment_gateway=True)
app.include_router(payment_router)
