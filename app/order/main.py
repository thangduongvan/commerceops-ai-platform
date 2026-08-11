"""Order service entrypoint (V7) — also owns Customer and publishes SQS events."""

import logging

from fastapi import FastAPI

from app.clients import payment_client, product_client
from app.core.config import settings
from app.core.service_app import create_service_lifespan, register_health_routes
from app.customer.models import Customer
from app.customer.router import router as customer_router
from app.order.models import Order, OrderItem
from app.order.router import router as order_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title=f"{settings.app_name} — Order",
    version="0.7.0",
    lifespan=create_service_lifespan(
        [Customer.__table__, Order.__table__, OrderItem.__table__]
    ),
)


def _downstream_checks(checks: dict) -> None:
    checks["product_service"] = product_client.probe()
    checks["payment_service"] = payment_client.probe()


register_health_routes(
    app,
    check_redis=True,
    check_queue=True,
    extra_checks=_downstream_checks,
)
app.include_router(customer_router)
app.include_router(order_router)
